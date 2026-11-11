"""Immutable facts plus explicit review/adoption; intake edits never touch facts."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

from .models import RawObservation, StabilityRecord
from .identity_links import linked_config, frozen_config
from .identity_associations import latest, adopted, logical_row, overlays, set_adoption, record_link
from .permissions import Capability, require
from .workflow_common import (
    WorkflowError, bump, check_revision, encoded, ensure_worksheet_slot_unlocked, event, next_result_sequence,
    remember, replay, uid,
)
from .workflow_contracts import MutationResult
from .workflow_contracts import ConfirmItem, ConfirmRequest


class ResultVersions:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store

    def confirm(self, upload_id, request, *, actor, _conn=None, _sheet_section=None):
        require(actor, Capability.CONFIRM_FACT)
        with (nullcontext(_conn) if _conn is not None else self.store.connect()) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            upload = self.workflow._upload(conn, upload_id)
            project = upload["project"]
            payload = {"confirm": upload_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            if upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx' and _sheet_section is None:
                raise WorkflowError('sheet_confirmation_required','本次来源按工作表确认，请在台账使用“确认整表入库”。')
            deferred = any(json.loads(r[0]).get('defer_confirmation') for r in conn.execute(
                "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='receive_source'", (upload_id,)))
            if deferred and _sheet_section is None and any(i.intent == 'initial' for i in request.items):
                raise WorkflowError('sheet_confirmation_required', '本次来源按工作表确认，请在台账使用“确认整表入库”。')
            views = {v.id: v for v in self.workflow._preview(conn, upload, actor).candidates}
            version_ids, pending_ids = [], []
            for item in request.items:
                view = views.get(item.candidate_id)
                if not view or view.lifecycle != "draft" or view.candidate_revision != item.candidate_revision:
                    raise WorkflowError("candidate_changed", "候选已变化、已提交或不属于当前上传。", 409)
                if view.status in {"empty", "needs_review"}:
                    raise WorkflowError("candidate_unresolved", "请先处理身份、源内容或同文件多结果歧义。")
                row = self.workflow._candidate(conn, item.candidate_id)
                record = view.record
                ensure_worksheet_slot_unlocked(conn, record.key.slot_id)
                existing = latest(conn,project,record.key.slot_id)
                duplicate = existing is not None
                if duplicate and item.intent == "initial":
                    raise WorkflowError("duplicate_requires_review", "重复槽位不能按首次结果保存，请选择复测、更正或重复来源。", 409)
                if not duplicate and item.intent != "initial":
                    raise WorkflowError("intent_mismatch", "当前槽位没有原结果，请核对身份和首次录入意图。")
                if item.intent == "correction":
                    adopted_row = adopted(conn,project,record.key.slot_id)
                    if not adopted_row:
                        raise WorkflowError("no_adopted_result", "无正式采用值，不能提交正式更正。")
                    existing = adopted_row
                if item.intent == "repeat_source" and not record.equivalent(
                    StabilityRecord.model_validate_json(existing["record_json"])
                ):
                    raise WorkflowError("not_equivalent", "重复来源意图要求结果相同；不同值请区分更正或复测。")
                expected_detection = existing["detection_id"] if item.intent in {"repeat_source", "correction"} else row["detection_id"]
                if item.detection_id != expected_detection:
                    raise WorkflowError("detection_mismatch", "请确认正确的独立检测身份；复测使用新身份，更正/重复来源关联原检测。")
                if duplicate and item.intent == "retest" and item.detection_id == existing["detection_id"]:
                    raise WorkflowError("retest_identity", "独立复测不能沿用原检测身份。")
                raw = self.workflow._source_rows(conn, [row], upload)[row['id']].model_copy(update={'revisions': view.revisions, 'evidence': view.evidence})
                doc = conn.execute("SELECT stored_path FROM documents WHERE sha256=?", (upload["source_hash"],)).fetchone()
                version_id = uid("result")
                from .review_issues import effective_source_decisions
                source = {
                    "source_id": row["id"], "document_hash": upload["source_hash"], "filename": upload["filename"],
                    "stored_path": doc["stored_path"], "raw_json": raw.model_dump(mode="json"),
                    "corrections": json.loads(row["corrections_json"]), "upload_id": upload["id"],
                    "upload_sequence": upload["sequence"], "uploaded_at": upload["uploaded_at"],
                    "context_json": json.loads(upload["context_json"]),
                    "config_json": linked_config(conn, frozen_config(conn,upload)).model_dump(mode='json'),
                    "acknowledge_warning": bool(row["acknowledge_warning"]),
                    "identity_scope": self.workflow.source_identity_scope(upload,raw),
                    "issue_decisions": effective_source_decisions(conn,row['id']),
                }
                # The source interpretation remains the original identity; record
                # the explicit overlay separately for independent export replay.
                original_record = self.workflow._mapped(conn,row,upload,_logical=False)
                link = record_link(conn,project,original_record)
                if link:
                    source['identity_association'] = link
                cross_slot = existing and existing.get('physical_slot_id',existing['slot_id']) != record.key.slot_id
                conn.execute("""INSERT INTO workflow_versions(id,project,slot_id,record_json,detection_id,candidate_id,source_json,
                    upload_sequence,intent,reason,references_json,submitted_by,previous_version_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (version_id, project, record.key.slot_id, record.model_dump_json(), item.detection_id, row["id"], encoded([source]),
                     upload["sequence"], item.intent, item.reason, encoded([r.model_dump() for r in item.references]), actor.id,
                     existing["id"] if existing and not cross_slot else None))
                if cross_slot:
                    conn.execute('INSERT INTO workflow_identity_lineage VALUES (?,?)',(version_id,existing['id']))
                state = "pending" if duplicate else "confirmed"
                conn.execute("INSERT INTO workflow_version_states VALUES (?,?)", (version_id, state))
                if not duplicate:
                    conn.execute("INSERT INTO workflow_adoptions VALUES (?,?,?)", (project, record.key.slot_id, version_id))
                else:
                    pending_ids.append(version_id)
                conn.execute("UPDATE workflow_candidates SET lifecycle='submitted',revision=revision+1 WHERE id=?", (row["id"],))
                event(conn, project, version_id, "submit_duplicate" if duplicate else "confirm_initial", actor, item.reason,
                      {"candidate_id": row["id"], "detection_id": item.detection_id, "identity_confirmed": True,
                       "intent": item.intent, "state": state})
                version_ids.append(version_id)
            result = MutationResult(project=project, base_revision=bump(conn, project), result_version_ids=tuple(version_ids),
                                    pending_confirmation_ids=tuple(pending_ids))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
            return result

    def change(self, version_id, request, *, actor):
        """A simple edit creates a pending version, without requiring an OOS workflow."""
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            version = self._version(conn, version_id)
            project = version['project']
            payload = {'result_change': version_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            adopted_row = adopted(conn,project,version['slot_id'])
            if not adopted_row or adopted_row['id'] != version_id:
                raise WorkflowError('adoption_changed', '正式值已变化，请刷新后再修改。', 409)
            record = StabilityRecord.model_validate_json(version['record_json'])
            source = json.loads(version['source_json'])[0]
            if not source.get('raw_json'):
                raise WorkflowError('legacy_source', '此旧记录缺少可复用的原始来源，请上传更正来源文件。')
            upload_id, candidate_id = uid('upload'), uid('candidate')
            raw = RawObservation.model_validate(source['raw_json'])
            fields = {**json.loads(version['record_json'])['key'], 'test_item': record.key.item,
                      'time_point': f'{record.key.day}D' if record.key.day is not None else '' if record.key.month is None else f'{record.key.month}M', 'result': request.result,
                      'unit': record.result.unit}
            fields.pop('item', None)
            fields.pop('month', None)
            fields.pop('day', None)
            conn.execute('''INSERT INTO workflow_uploads(sequence,id,project,source_hash,filename,context_json,uploaded_by,client_key,request_hash)
                            VALUES (?,?,?,?,?,?,?,?,?)''',
                         (next_result_sequence(conn), upload_id, project, source['document_hash'], source['filename'],
                          source['context_json'] if isinstance(source['context_json'], str) else encoded(source['context_json']),
                          actor.id, 'change:' + request.idempotency_key, hashlib.sha256(encoded(payload).encode()).hexdigest()))
            conn.execute('''INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json,acknowledge_warning)
                            VALUES (?,?,?,?,?,?,1)''',
                         (candidate_id, project, upload_id, raw.model_dump_json(), version['detection_id'], encoded(fields)))
            event(conn, project, candidate_id, 'edit_from_display', actor, request.reason,
                  {'previous_version_id': version_id, 'before': record.result.raw_text, 'after': request.result})
            command = ConfirmRequest(base_revision=request.base_revision, idempotency_key='change-confirm:' + request.idempotency_key,
                                     items=(ConfirmItem(candidate_id=candidate_id, candidate_revision=1,
                                                        detection_id=version['detection_id'], identity_confirmed=True,
                                                        intent='correction', reason=request.reason, references=request.references),))
            result = self.confirm(upload_id, command, actor=actor, _conn=conn,_sheet_section=raw.location.section)
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def record_worksheet_result(self, conn, record, *, actor, previous, current, intent, reason, source):
        """Record a direct entry in the same version and adoption tables as file intake."""
        require(actor, Capability.CONFIRM_FACT)
        project, slot_id = record.key.project, record.key.slot_id
        if intent == "correction" and (not previous or not reason):
            raise WorkflowError("correction_requires_reason", "更正需要当前采用结果及说明。")
        if intent == "initial" and current:
            raise WorkflowError("duplicate_requires_review", "已有检测，请明确选择更正或新增检测。", 409)
        if intent == "retest" and not current:
            raise WorkflowError("intent_mismatch", "请先录入首次检测。")
        if intent == "retest" and not reason:
            raise WorkflowError("retest_requires_reason", "请填写新增检测说明。")

        parent = previous if intent == "correction" else current
        detection_id = previous["detection_id"] if intent == "correction" else uid("detection")
        if intent != "correction":
            conn.execute("INSERT INTO workflow_detections(id,project) VALUES (?,?)", (detection_id, project))
        cross_slot = parent and parent.get("physical_slot_id", parent["slot_id"]) != slot_id
        version_id = uid("result")
        source = {**source, "source_id": "entry_" + version_id}
        conn.execute("""INSERT INTO workflow_versions(id,project,slot_id,record_json,detection_id,source_json,
            upload_sequence,intent,reason,references_json,submitted_by,previous_version_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (version_id, project, slot_id, record.model_dump_json(), detection_id, encoded([source]),
             next_result_sequence(conn), intent, reason, "[]", actor.id, parent["id"] if parent and not cross_slot else None))
        if cross_slot:
            conn.execute("INSERT INTO workflow_identity_lineage VALUES (?,?)", (version_id, parent["id"]))
        conn.execute("INSERT INTO workflow_version_states VALUES (?, 'confirmed')", (version_id,))
        if intent in {"initial", "correction"}:
            set_adoption(conn, project, slot_id, version_id)
        event(conn, project, version_id, "worksheet_" + intent, actor, reason,
              {"detection_id": detection_id, "previous_version_id": parent["id"] if parent else None,
               "worksheet_id": source["worksheet_id"], "adopted": intent != "retest"})
        return version_id

    def _version(self, conn, version_id):
        row = conn.execute("""SELECT v.*,s.state,i.previous_version_id AS identity_previous_version_id
                            FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id
                            LEFT JOIN workflow_identity_lineage i ON i.version_id=v.id
                            WHERE v.id=?""", (version_id,)).fetchone()
        if row is None:
            raise WorkflowError("not_found", "结果版本不存在。", 404)
        return logical_row(row,overlays(conn,row['project']))

    def review(self, version_id, request, *, actor, _conn=None):
        require(actor, Capability.REVIEW_DUPLICATE)
        if request.action == "adopt":
            require(actor, Capability.ADOPT_RESULT)
        with (nullcontext(_conn) if _conn is not None else self.store.connect()) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            row = self._version(conn, version_id)
            project = row["project"]
            payload = {"review": version_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            if row["state"] not in {"pending", "confirmed"}:
                raise WorkflowError("invalid_state", "该结果已撤回、拒绝或为迁移旧值，不能重复复核。", 409)
            ensure_worksheet_slot_unlocked(conn, row["slot_id"])
            if row["intent"] == "correction":
                require(actor, Capability.CORRECT_RESULT)
                if request.action == "confirm_fact":
                    raise WorkflowError("correction_requires_adoption", "正式更正请明确采用或拒绝，不能只确认事实。")
            if request.action != "adopt" and row["state"] != "pending":
                raise WorkflowError("invalid_state", "已确认结果不能撤回审批或重新确认事实。", 409)
            before = adopted(conn,project,row['slot_id'])
            if row["intent"] == "correction" and request.action == "adopt" and (not before or before['id'] != row["previous_version_id"]):
                raise WorkflowError("correction_base_changed", "更正依据的正式值已变化，请重新提交核对。", 409)
            state = "rejected" if request.action == "reject" else "confirmed"
            conn.execute("UPDATE workflow_version_states SET state=? WHERE version_id=?", (state, version_id))
            if request.action == "adopt":
                set_adoption(conn,project,row['slot_id'],version_id)
            if request.action == "reject":
                conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (row["candidate_id"],))
            event(conn, project, version_id, "review_" + request.action, actor, request.reason,
                  {"before_adopted": before['id'] if before else None, "state": state})
            result = MutationResult(project=project, base_revision=bump(conn, project), result_version_ids=(version_id,))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
            return result

    def withdraw_candidate(self, candidate_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = self.workflow._candidate(conn, candidate_id)
            upload = self.workflow._upload(conn, candidate["upload_id"])
            project = upload["project"]
            payload = {"withdraw": candidate_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            version = conn.execute("""SELECT v.id,v.submitted_by,s.state FROM workflow_versions v
                JOIN workflow_version_states s ON s.version_id=v.id WHERE v.candidate_id=?""", (candidate_id,)).fetchone()
            owner = version["submitted_by"] if version else upload["uploaded_by"]
            if owner != actor.id:
                require(actor, Capability.REVIEW_DUPLICATE)
            if candidate["lifecycle"] == "withdrawn" or (version and version["state"] != "pending"):
                raise WorkflowError("invalid_state", "不能撤回已确认检测事实；请走正式更正。", 409)
            if version:
                conn.execute("UPDATE workflow_version_states SET state='withdrawn' WHERE version_id=?", (version["id"],))
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (candidate_id,))
            event(conn, project, candidate_id, "withdraw_candidate", actor, request.reason, {"version_id": version["id"] if version else None})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
            return result

    def history(self, project, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in conn.execute(
                "SELECT * FROM workflow_events WHERE project=? ORDER BY sequence", (project,))]

    def original(self, source_id, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            row = self.workflow._candidate(conn, source_id)
            upload = self.workflow._upload(conn, row['upload_id'])
            return self._original_file(upload)

    def original_upload(self, upload_id, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            return self._original_file(self.workflow._upload(conn,upload_id))

    def _original_file(self,upload):
        suffix = Path(upload['filename']).suffix.lower()
        path = self.store.home / 'sources' / (upload['source_hash'] + suffix)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
            raise WorkflowError('source_changed', '冻结原件缺失或哈希异常，请核对来源。', 409)
        return path, upload['filename']

    def source(self, source_id, *, actor):
        require(actor, Capability.VIEW)
        if source_id.startswith("entry_result_"):
            with self.store.read() as conn:
                row = self._version(conn, source_id.removeprefix("entry_"))
                source = next((item for item in json.loads(row["source_json"]) if item["source_id"] == source_id), None)
                if not source:
                    raise WorkflowError("not_found", "来源不存在。", 404)
                return {**source, "project": row["project"], "version_id": row["id"],
                        "submitted_at": row["submitted_at"], "submitted_by": row["submitted_by"],
                        "previous_version_id": row["previous_version_id"], "reason": row["reason"], "intent": row["intent"]}
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            if not source_id.startswith("legacy_"):
                row = self.workflow._candidate(conn, source_id)
                upload = self.workflow._upload(conn, row["upload_id"])
                return {"source_id": source_id, "preview_id": upload["id"], "project": row["project"], "filename": upload["filename"],
                        "source_hash": upload["source_hash"], "upload_sequence": upload["sequence"],
                        "uploaded_at": upload["uploaded_at"], "uploaded_by": upload["uploaded_by"],
                        "raw": json.loads(row["raw_json"]), "corrections": json.loads(row["corrections_json"]),
                        "events": [dict(e) for e in conn.execute("SELECT * FROM workflow_events WHERE subject_id=? ORDER BY sequence", (source_id,))]}
            for row in conn.execute("SELECT project,source_json FROM workflow_versions WHERE intent='legacy'"):
                for source in json.loads(row["source_json"]):
                    if source["source_id"] == source_id:
                        return {k: v for k, v in {**source, "project": row["project"]}.items() if k != "stored_path"}
            raise WorkflowError("not_found", "来源不存在。", 404)
