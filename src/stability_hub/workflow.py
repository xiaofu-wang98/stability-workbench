"""Human-confirmed intake. Parsers/mapping are reused; no implicit result adoption."""

import hashlib
import json
import re
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory

from .matrix_fields import interpret_raw
from .xlsx_revisions import retained_fields
from .mapping import ALLOWED_CONTEXT, map_corrected, prepare_observation
from .models import ImportContext, ItemDefinition, ProjectConfig, RawObservation, StabilityRecord, digest
from .normalize import NeedsReview
from .parsing import parse_source
from .permissions import Actor, Capability, require
from .result_queries import cells_from_rows, selected_rows
from .storage import Store
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, next_result_sequence, project_row, remember, replay, uid
from .workflow_contracts import CandidateView, MutationResult, ResultFilter, ResultPage, UploadPreview
from .identity_links import frozen_config, linked_config, check_historical_identity

EDITABLE = ALLOWED_CONTEXT | {"criterion", "subresult", "reporting_basis", "test_item", "result", "unit", "source_number", "test_date"}


class Workflow:
    def __init__(self, store: Store):
        self.store = store
        # Only deterministic source interpretation is cached. History, permissions,
        # collisions and submission gates are recomputed from each DB snapshot.
        self._source_interpretations = OrderedDict()
        self._source_cache_lock = threading.RLock()
        self._review_snapshots = OrderedDict()
        self._review_states = OrderedDict()
        self.review_build_counts = Counter()

    def create_project(self, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', request.project):
            raise WorkflowError('invalid_project', '项目编号仅允许字母、数字、点、横线和下划线，最长80字符。')
        config = ProjectConfig(project=request.project, name=request.name)
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM workflow_projects WHERE project=?', (config.project,)).fetchone():
                raise WorkflowError('project_exists', '项目已存在，请选择已有项目。', 409)
            if self.store.snapshot(config.project) or self.store.issues(config.project):
                raise WorkflowError('legacy_project', '存在历史项目，请先通过既有项目配置激活。', 409)
            conn.execute('INSERT INTO workflow_projects(project,config_json,startup_config_json) VALUES (?,?,?)',
                         (config.project, config.model_dump_json(), config.model_dump_json()))
            event(conn, config.project, config.project, 'create_project', actor, '确认项目编号与名称后创建空项目',
                  {'name': request.name})
        return self.configuration(config.project, actor=actor)

    def add_items(self, project, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            payload = {'new_items': project, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            before = ProjectConfig.model_validate_json(project_row(conn, project)['config_json'])
            if {i.id for i in request.items} & {i.id for i in before.items}:
                raise WorkflowError('existing_item', '批量新增不能修改已有检项，请使用已有项目映射或高级修改。')
            created = tuple(ItemDefinition.model_validate({**i.model_dump(), 'id':i.id or uid('item')}) for i in request.items)
            after = ProjectConfig.model_validate({**before.model_dump(), 'items': (*before.items, *created)})
            conn.execute('UPDATE workflow_projects SET config_json=? WHERE project=?', (after.model_dump_json(), project))
            event(conn, project, project, 'add_items', actor, request.reason,
                  {'items': [i.model_dump(mode='json') for i in created]})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def edit_shared(self, upload_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        if set(request.fields) - {'batch', 'presentation', 'sample_type', 'purpose', 'study_id', 'study', 'condition', 'method', 'method_version'}:
            raise WorkflowError('invalid_shared_fields', '共享修正仅限身份信息，不能批量修改数值、时间点或确认来源警告。')
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            upload = self._upload(conn, upload_id)
            project = upload['project']
            payload = {'shared_edit': upload_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            ids = set(request.candidate_ids)
            rows = conn.execute('SELECT * FROM workflow_candidates WHERE upload_id=?', (upload_id,)).fetchall()
            chosen = [r for r in rows if r['id'] in ids]
            if len(chosen) != len(ids) or any(r['lifecycle'] != 'draft' for r in chosen):
                raise WorkflowError('candidate_changed', '所选范围包含其他上传或已提交记录，请刷新。', 409)
            for row in chosen:
                before = json.loads(row['corrections_json'])
                after = {**before, **request.fields}
                conn.execute('UPDATE workflow_candidates SET corrections_json=?,revision=revision+1 WHERE id=?', (encoded(after), row['id']))
                event(conn, project, row['id'], 'edit_candidate', actor, request.reason, {'before': before, 'after': after, 'scope': upload_id})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def configure(self, config: ProjectConfig):
        """Trusted startup configuration; preserve legacy facts without inventing approval."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM workflow_projects WHERE project=?", (config.project,)).fetchone()
            if current:
                if config not in (ProjectConfig.model_validate_json(current["config_json"]),
                                  ProjectConfig.model_validate_json(current["startup_config_json"] or current["config_json"])):
                    raise WorkflowError("config_change_requires_migration", "现有项目字典变更需显式迁移，不能自动重解释。", 409)
                return
            if self.store.issues(config.project):
                raise WorkflowError("legacy_issues", "旧项目存在未解决观察，请先制定显式迁移决策。", 409)
            legacy = self.store.snapshot(config.project)
            conn.execute("INSERT INTO workflow_projects(project,config_json,startup_config_json) VALUES (?,?,?)",
                         (config.project, config.model_dump_json(), config.model_dump_json()))
            for entry in legacy:
                detection = "legacy_detection_" + entry["observation_id"]
                version = "legacy_version_" + entry["observation_id"]
                conn.execute("INSERT INTO workflow_detections(id,project) VALUES (?,?)", (detection, config.project))
                sources = [{"source_id": "legacy_" + s["id"], **s} for s in entry["sources"]]
                conn.execute("""INSERT INTO workflow_versions(id,project,slot_id,record_json,detection_id,
                    source_json,upload_sequence,intent,reason,references_json,submitted_by)
                    VALUES (?,?,?,?,?,?,0,'legacy','Preserved legacy effective value; no new approval claimed','[]','legacy-unknown')""",
                    (version, config.project, entry["slot_id"], encoded(entry["record"]), detection, encoded(sources)))
                conn.execute("INSERT INTO workflow_version_states VALUES (?,'legacy')", (version,))
                conn.execute("INSERT INTO workflow_adoptions VALUES (?,?,?)", (config.project, entry["slot_id"], version))

    def configuration(self, project, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            row = project_row(conn, project)
            return {"config": json.loads(row["config_json"]), "base_revision": row["revision"]}

    def change_configuration(self, project, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payload = {"configuration": project, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            before = ProjectConfig.model_validate_json(project_row(conn, project)["config_json"])
            data = before.model_dump(mode="json")
            if request.item:
                existing = next((i for i in before.items if i.id == request.item.id), None)
                if existing:
                    require(actor, Capability.CORRECT_RESULT)
                    data["items"] = [request.item.model_dump(mode="json") if i.id == existing.id else i.model_dump(mode="json")
                                     for i in before.items]
                else:
                    data["items"].append(request.item.model_dump(mode="json"))
            else:
                if request.sample_type in before.presentation_defaults:
                    require(actor, Capability.CORRECT_RESULT)
                data["presentation_defaults"][request.sample_type] = request.presentation
            # Validates duplicate names/aliases before any write; no implicit unit conversion.
            after = ProjectConfig.model_validate(data)
            conn.execute("UPDATE workflow_projects SET config_json=? WHERE project=?", (after.model_dump_json(), project))
            event(conn, project, project, "configure_project", actor, request.reason,
                  {"before": before.model_dump(mode="json"), "after": after.model_dump(mode="json")})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
            return result

    def _upload(self, conn, upload_id):
        row = conn.execute("SELECT * FROM workflow_uploads WHERE id=?", (upload_id,)).fetchone()
        if not row:
            raise WorkflowError("not_found", "上传记录不存在。", 404)
        return row

    def list_previews(self, project, offset=0, limit=20, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            project_row(conn, project)
            return [dict(row) for row in conn.execute("""
                SELECT u.id AS preview_id,u.filename,u.uploaded_at,u.uploaded_by,
                  COUNT(c.id) AS total,
                  SUM(CASE WHEN c.lifecycle='draft' THEN 1 ELSE 0 END) AS draft,
                  SUM(CASE WHEN c.lifecycle='submitted' THEN 1 ELSE 0 END) AS submitted,
                  SUM(CASE WHEN c.lifecycle='withdrawn' THEN 1 ELSE 0 END) AS withdrawn
                FROM workflow_uploads u LEFT JOIN workflow_candidates c ON c.upload_id=u.id
                WHERE u.project=? GROUP BY u.id ORDER BY u.rowid DESC LIMIT ? OFFSET ?
                """, (project, limit, offset))]

    def _candidate(self, conn, candidate_id):
        row = conn.execute("SELECT * FROM workflow_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise WorkflowError("not_found", "候选不存在。", 404)
        return row

    def _source_rows(self, conn, rows, upload, *, _cache=None):
        """Read revision evidence for pre-patch drafts without rewriting frozen raw JSON."""
        raws = {}
        for r in rows:
            cached = _cache.get(r['id']) if _cache is not None else None
            if cached is not None and cached[0] == r['raw_json']:
                raw = cached[1]
            else:
                raw = RawObservation.model_validate_json(r['raw_json'])
                if _cache is not None:
                    _cache[r['id']] = (r['raw_json'],raw)
            raws[r['id']] = raw
        legacy = [r for r in rows if r['lifecycle'] == 'draft' and 'revisions' not in raws[r['id']].model_fields_set]
        if legacy and Path(upload['filename']).suffix.lower() == '.xlsx':
            path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
            if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
                raise WorkflowError('source_changed', '原件校验失败，不能核对旧草稿修订。', 409)
            parsed = parse_source(path)
            evidence = {(r.location.section,r.location.row,r.location.column): r for r in parsed.observations}
            for row in legacy:
                raw = raws[row['id']]
                current = evidence.get((raw.location.section,raw.location.row,raw.location.column))
                if current:
                    raws[row['id']] = raw.model_copy(update={'revisions': current.revisions, 'evidence': current.evidence})
        from .numeric_formats import VERSION as numeric_version
        upgrade=conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='reinterpret_draft' ORDER BY sequence DESC LIMIT 1",(upload['id'],)).fetchone()
        from .matrix_fields import IDENTITY_VERSIONS
        from .normalize import SEMANTIC_VERSION
        versions = json.loads(upgrade[0]) if upgrade else {}
        from .standards import VERSION as standards_version, LEGACY_VERSION as standards_legacy
        physical = None
        for row in rows:
            if row['lifecycle'] == 'draft':
                raw = raws[row['id']]
                metadata = dict(raw.source_value)
                if versions.get('numeric_rule_version') == numeric_version:
                    metadata['rule_version'] = numeric_version
                if versions.get('identity_rule_version') in IDENTITY_VERSIONS:
                    metadata['identity_rule_version'] = versions['identity_rule_version']
                if versions.get('semantic_rule_version') == SEMANTIC_VERSION:
                    metadata['semantic_rule_version'] = SEMANTIC_VERSION
                if versions.get('standards_rule_version') in {standards_version,standards_legacy}:
                    target_standard=versions['standards_rule_version']
                    if metadata.get('standards_rule_version') != target_standard and raw.location.format == 'xlsx':
                        from .standard_upgrade import source_evidence, upgrade_raw
                        physical = physical if physical is not None else source_evidence(self, conn, upload)
                        raw = upgrade_raw(raw, physical.get(raw.location))
                    metadata['standards_rule_version'] = target_standard
                raws[row['id']] = raw.model_copy(update={'source_value': metadata})
        return raws

    def _mapped(self, conn, candidate, upload, *, config=None, context=None, raw=None, acknowledge_warning=None, _logical=True, source_review=None, identity_overlays=None, interpreted_fields=None):
        if candidate["lifecycle"] == "submitted":
            frozen = conn.execute("SELECT record_json FROM workflow_versions WHERE candidate_id=?", (candidate["id"],)).fetchone()
            if frozen:
                from .identity_associations import logical_record
                return logical_record(conn,upload['project'],StabilityRecord.model_validate_json(frozen[0]),links=identity_overlays)
            receipt = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='repeat_source_reference' ORDER BY sequence DESC LIMIT 1",
                                   (candidate['id'],)).fetchone()
            if receipt:
                from .identity_associations import logical_record
                return logical_record(conn,upload['project'],StabilityRecord.model_validate(json.loads(receipt[0])['record']),links=identity_overlays)
        raw = raw or self._source_rows(conn, [candidate], upload)[candidate['id']]
        changes = json.loads(candidate["corrections_json"])
        issues = ()
        explicit_source_decision = upload['workflow_version'] == 4 and conn.execute(
            "SELECT 1 FROM workflow_events WHERE subject_id=? AND action IN ('resolve_issue','resolve_structure') LIMIT 1",
            (candidate['id'],)).fetchone()
        if upload['workflow_version'] >= 5 or explicit_source_decision:
            from .review_issues import resolved_raw
            raw, issues = source_review if source_review is not None else resolved_raw(conn,candidate['id'],raw,corrections=changes)
            acknowledge_warning = False
        context = context or ImportContext.model_validate_json(upload["context_json"])
        config = linked_config(conn, config or frozen_config(conn, upload))
        # Source trust is decided by the common review conclusion below. The
        # mapper validates effective fields/units/identity, without running its
        # legacy acknowledgement gate a second time before those fields exist.
        mapping_raw = raw.model_copy(update={'comment':'','formula':'','hazards':(),'revisions':{}}) if (
            upload['workflow_version'] >= 5 or explicit_source_decision) else raw
        record = map_corrected(mapping_raw, context, config, changes,
            candidate['acknowledge_warning'] if acknowledge_warning is None else acknowledge_warning, source_facts=True,
            source_scope=self.source_identity_scope(upload,raw),interpreted_fields=interpreted_fields)
        if issues:
            issue = issues[0]
            code = 'unsupported_structure' if issue.code == 'structure' else issue.code
            if issue.code == 'source_revision':
                from .xlsx_revisions import is_deleted
                if is_deleted(raw):
                    code = 'deleted_source'
            raise NeedsReview(code,issue.detail)
        from .identity_associations import logical_record
        if _logical:
            record = logical_record(conn, upload['project'], record,links=identity_overlays)
        if _logical:
            check_historical_identity(conn,config,record)
        return record

    @staticmethod
    def source_identity_scope(upload,raw):
        if upload['workflow_version'] < 5:
            return ''
        # A missing study/specification does not establish sameness even within
        # one workbook. Matrix block batch anchors distinguish local objects;
        # unstructured rows stay local until an operator explicitly relates them.
        anchor = raw.evidence.get('batch') or raw.evidence.get('test_item') or raw.location
        return upload['id'] + ':' + digest([raw.location.section,anchor.row,anchor.column])[:12]

    def upload(self, project, filename, data, context: ImportContext, idempotency_key, *, actor: Actor, _parsed=None, deduplicate=False, _legacy_compatibility=False):
        require(actor, Capability.PREVIEW)
        filename = Path(filename.replace("\\", "/")).name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".xlsx", ".docx"}:
            raise WorkflowError("unsupported_format", "当前仅支持已适配的 XLSX 和 DOCX。")
        if len(data) > 20 * 1024 * 1024:
            raise WorkflowError("file_too_large", "单文件不得超过 20MB。", 413)
        if not idempotency_key or not idempotency_key.strip():
            raise WorkflowError("idempotency_required", "上传需要幂等键。")
        sha = hashlib.sha256(data).hexdigest()
        request_hash = digest({"sha": sha, "context": context.model_dump(), "filename": filename})
        with self.store.connect() as conn:
            conn.execute('BEGIN')
            project_row(conn, project)
            prior = conn.execute("SELECT * FROM workflow_uploads WHERE project=? AND uploaded_by=? AND client_key=?",
                                 (project, actor.id, idempotency_key)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise WorkflowError("idempotency_conflict", "上传幂等键已用于不同文件或上下文。", 409)
                return self._preview(conn, prior, actor)
            identical = conn.execute('SELECT * FROM workflow_uploads WHERE project=? AND source_hash=? ORDER BY sequence LIMIT 1', (project, sha)).fetchone()
            if identical and deduplicate:
                return self._preview(conn, identical, actor).model_copy(update={'duplicate_source': True})
        with TemporaryDirectory(prefix="hub-upload-") as temp:
            path = Path(temp) / ("source" + suffix)
            path.write_bytes(data)
            parsed = _parsed if _parsed is not None else parse_source(path,allow_partial=True)
        frozen = self.store.home / "sources" / (sha + suffix)
        try:
            with frozen.open("xb") as target:
                target.write(data)
        except FileExistsError:
            if hashlib.sha256(frozen.read_bytes()).hexdigest() != sha:
                raise WorkflowError("source_changed", "保存的原件哈希不匹配。", 409) from None
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            identical = conn.execute('SELECT * FROM workflow_uploads WHERE project=? AND source_hash=? ORDER BY sequence LIMIT 1', (project, sha)).fetchone()
            if identical and deduplicate:
                return self._preview(conn, identical, actor).model_copy(update={'duplicate_source': True})
            prior = conn.execute("SELECT * FROM workflow_uploads WHERE project=? AND uploaded_by=? AND client_key=?",
                                 (project, actor.id, idempotency_key)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise WorkflowError("idempotency_conflict", "上传幂等键冲突。", 409)
                return self._preview(conn, prior, actor)
            conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?)", (sha, filename, str(frozen)))
            upload_id = uid("upload")
            conn.execute("""INSERT INTO workflow_uploads(sequence,id,project,source_hash,filename,context_json,uploaded_by,client_key,request_hash)
                         VALUES (?,?,?,?,?,?,?,?,?)""", (next_result_sequence(conn), upload_id, project, sha, filename,
                         context.model_dump_json(), actor.id, idempotency_key, request_hash))
            conn.execute('UPDATE workflow_uploads SET interpretation_config_json=?,workflow_version=5 WHERE id=?',
                         (project_row(conn,project)['config_json'],upload_id))
            if _legacy_compatibility:
                conn.execute('UPDATE workflow_uploads SET workflow_version=3,interpretation_config_json=NULL WHERE id=?',(upload_id,))
            detections = {}
            for raw in parsed.observations:
                raw = prepare_observation(raw, parsed.metadata)
                from .file_rules import rule_corrections
                rule_fields, rule_hazards, rule_trace = rule_corrections(conn,project,raw)
                if rule_hazards:
                    raw = raw.model_copy(update={'hazards':raw.hazards + rule_hazards})
                number = raw.fields.get("source_number", "").strip() or None
                if number not in detections:
                    detections[number] = uid("detection")
                    conn.execute("INSERT INTO workflow_detections(id,project,source_number) VALUES (?,?,?)",
                                 (detections[number], project, number))
                candidate_id = uid('candidate')
                conn.execute("""INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json)
                             VALUES (?,?,?,?,?,?)""", (candidate_id, project, upload_id, raw.model_dump_json(), detections[number],encoded(rule_fields)))
                if rule_trace:
                    event(conn,project,candidate_id,'apply_project_rule',actor,'显式项目规则命中，固定本文件解释版本',rule_trace)
            event(conn, project, upload_id, "upload", actor, "文件已接收；尚未确认检测身份或入库。", {"source_hash": sha, "parsing_summary": parsed.coverage})
            bump(conn, project)
            return self._preview(conn, self._upload(conn, upload_id), actor)

    def _preview(self, conn, upload, actor, *, fresh=False):
        from .review_issues import resolved_raw
        from .file_rules import source_signature
        config_project = upload["project"]
        from .identity_associations import overlays, versions, adoption_choices, current
        identity_overlays = overlays(conn,config_project)
        historical,history_by_id = {},{}
        for version in versions(conn,config_project):
            historical.setdefault(version['slot_id'],[]).append(version)
            history_by_id[version['id']] = version
        adopted_choices = adoption_choices(conn,config_project)
        rows = conn.execute("SELECT * FROM workflow_candidates WHERE upload_id=? ORDER BY rowid", (upload["id"],)).fetchall()
        views, slots = [], {}
        receipts = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='receive_source'", (upload["id"],)).fetchall()
        received = {cid for r in receipts for cid in json.loads(r[0])["candidate_ids"]}
        config = frozen_config(conn, upload)
        context = ImportContext.model_validate_json(upload['context_json'])
        probe_config = config.model_copy(update={'items': tuple(i.model_copy(update={'unit': ''}) for i in config.items)})
        with self._source_cache_lock:
            source_cache = self._source_interpretations.setdefault(upload['id'],{})
            self._source_interpretations.move_to_end(upload['id'])
            while len(self._source_interpretations)>2:
                self._source_interpretations.popitem(last=False)
        raws = self._source_rows(conn,rows,upload,_cache=source_cache.setdefault('_raw',{}))
        # Source mapping without a dictionary/association does not depend on
        # historical result values. Reuse unchanged candidates even after the
        # first sheet is committed; compare against CURRENT history below.
        # Final worksheet checks still explicitly request fresh mapping.
        pure_projection = upload['workflow_version'] >= 5 and not config.items and not identity_overlays
        projection_config = config.model_dump_json()
        projection_cache = source_cache.setdefault('_projection',{})
        source_decisions,rule_history = {},{}
        for e in conn.execute('''SELECT e.subject_id,e.sequence,e.action,e.detail_json FROM workflow_events e
            JOIN workflow_candidates c ON c.id=e.subject_id WHERE c.upload_id=?
            AND e.action IN ('restore_draft','resolve_issue','resolve_structure','inherit_source_decision','apply_project_rule') ORDER BY e.sequence''',(upload['id'],)):
            cid=e['subject_id']
            if e['action']=='restore_draft':
                source_decisions[cid]=[]
            elif e['action']=='apply_project_rule':
                rule_history.setdefault(cid,[]).append({'sequence':e['sequence'],**json.loads(e['detail_json'])})
            else:
                source_decisions.setdefault(cid,[]).append(json.loads(e['detail_json']))
        undone_batches={json.loads(r[0])['batch_id'] for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",(upload['id'],))}
        if undone_batches:
            source_decisions={cid:[d for d in ds if d.get('batch_id') not in undone_batches] for cid,ds in source_decisions.items()}
        superseded = {}
        for e in conn.execute("SELECT subject_id,action,detail_json FROM workflow_events WHERE project=? AND action IN ('split_source','supersede_projection') ORDER BY sequence",(config_project,)):
            data=json.loads(e['detail_json'])
            superseded[e['subject_id']]=tuple(data.get('children',[])) if e['action']=='split_source' else (data['parent_id'],)
        revision_reviewed = {r['subject_id'] for r in conn.execute(
            "SELECT subject_id,detail_json FROM workflow_events WHERE project=? AND action='correct_extraction'", (config_project,))
            if json.loads(r['detail_json']).get('revision_confirmed')}
        for row in rows:
            raw = raws[row['id']]
            acknowledge_warning = row['acknowledge_warning']
            if raw.revisions and 'revisions' not in json.loads(row['raw_json']) and row['id'] not in revision_reviewed:
                acknowledge_warning = False
            corrections = json.loads(row["corrections_json"])
            cache_key = (row['raw_json'],encoded(raw.source_value),row['corrections_json'],row['revision'],encoded(source_decisions.get(row['id'],[])))
            cached = source_cache.get(row['id'])
            if cached is not None and cached[0] == cache_key:
                original_fields,source_review,effective_fields,mapping_signature,row_signature,suggested_fields = cached[1]
            else:
                original_fields = interpret_raw(raw, raw.fields) if raw.adapter == 'stability_matrix' else raw.fields
                source_review = resolved_raw(conn,row['id'],raw,decisions=source_decisions.get(row['id'],[]),corrections=corrections)
                effective_fields = original_fields
                if source_review[0].fields != raw.fields:
                    effective_fields = interpret_raw(source_review[0]) if raw.adapter == 'stability_matrix' else source_review[0].fields
                if raw.adapter == 'stability_matrix' and set(corrections) & {'method', 'criterion', 'subresult'}:
                    effective_fields = interpret_raw(source_review[0], {**source_review[0].fields, **corrections})
                from .ledger_review import LedgerReview
                mapping_signature = source_signature(raw,interpreted=original_fields)
                row_signature = LedgerReview.row_signature(raw,interpreted=original_fields)
                suggested_fields = (interpret_raw(raw, retained_fields(raw)) if raw.adapter == 'stability_matrix' else retained_fields(raw)) if raw.revisions else {}
                source_cache[row['id']] = (cache_key,(original_fields,source_review,effective_fields,mapping_signature,row_signature,suggested_fields))
            if upload['workflow_version'] < 5 and raw.revisions:
                effective_fields = None  # Legacy mapper retains its own revision acknowledgement path.
            record = old = None
            code = None
            existing_detection = None
            adopted_detection = None
            projection_key = (cache_key,upload['context_json'],row['lifecycle'],acknowledge_warning,projection_config)
            projection = projection_cache.get(row['id']) if pure_projection and not fresh else None
            projection_hit = projection is not None and projection[0] == projection_key
            try:
                if projection_hit:
                    if projection[2] is not None:
                        raise NeedsReview(*projection[2])
                    record = projection[1]
                else:
                    try:
                        record = self._mapped(conn, row, upload, config=config, context=context, raw=raw,
                            acknowledge_warning=acknowledge_warning,source_review=source_review,identity_overlays=identity_overlays,interpreted_fields=effective_fields)
                    except NeedsReview as exc:
                        if pure_projection:
                            projection_cache[row['id']] = (projection_key,None,(exc.code,str(exc)))
                        raise
                    if pure_projection:
                        projection_cache[row['id']] = (projection_key,record,None)
                existing = current(historical.get(record.key.slot_id,[]))
                if existing:
                    old = StabilityRecord.model_validate_json(existing["record_json"])
                    existing_detection = existing["detection_id"]
                adopted = history_by_id.get(adopted_choices.get(record.key.slot_id))
                if adopted and (adopted['slot_id'] != record.key.slot_id or adopted['state'] not in {'confirmed','legacy'}):
                    adopted = None
                adopted_detection = adopted['detection_id'] if adopted else None
                if record.result.status in {"blank", "placeholder"}:
                    status, detail = "empty", "空白不更新历史。"
                elif old:
                    status = "same_value" if record.equivalent(old) else "different_value"
                    detail = "已有相同结果，本次可沿用；若为独立复测请明确选择。" if status == 'same_value' else "与已有结果不同，请选择保留旧值、更正或独立复测；更正仍需批准。"
                else:
                    status, detail = "new", "核对检测身份后，可确认首次非重复结果。"
                if status != "empty" and row["lifecycle"] != "withdrawn":
                    slots.setdefault(record.key.slot_id, []).append(len(views))
            except NeedsReview as exc:
                status, code, detail = "needs_review", exc.code, str(exc)
            actions = []
            if row["lifecycle"] == "draft" and Capability.CONFIRM_FACT in actor.capabilities:
                actions = ["edit", "withdraw"]
                if status not in {"empty", "needs_review"}:
                    actions.append("confirm_initial" if status == "new" else "submit_review")
            applied_defaults = {}
            from .standards import select as select_standard
            fields = select_standard(raw, {**(effective_fields or original_fields), **corrections})
            if raw.subresult_hint and 'test_item' not in corrections:
                fields['test_item'] = fields.get('test_item', '') + ' · ' + raw.subresult_hint
            sample_type = fields.get("sample_type", "").strip() or context.defaults.get("sample_type", "")
            if (row["lifecycle"] == "draft" and not fields.get("presentation", "").strip()
                    and not context.defaults.get("presentation", "").strip()
                    and sample_type in config.presentation_defaults):
                applied_defaults["presentation"] = config.presentation_defaults[sample_type]
            inherited_fields = {k: v for k, v in context.defaults.items()
                                if not fields.get(k, '').strip() and k not in corrections}
            from .ledger_review import LedgerReview
            if projection_hit and len(projection)>3:
                attribution_problem = NeedsReview(*projection[3]) if projection[3] is not None else None
            else:
                attribution_problem = LedgerReview(self).attribution_problem(conn, row, upload, config=probe_config, context=context,raw=raw,interpreted=original_fields)
                if pure_projection:
                    with self._source_cache_lock:
                        current_projection = projection_cache[row['id']]
                        if current_projection[0] == projection_key:
                            projection_cache[row['id']] = (*current_projection[:3],(attribution_problem.code,str(attribution_problem)) if attribution_problem else None)
            can_receive = attribution_problem is None
            if upload['workflow_version'] < 5 and attribution_problem is not None and row['lifecycle'] == 'draft':
                status, code, detail = 'needs_review', attribution_problem.code, str(attribution_problem)
                actions = [a for a in actions if a in {'edit', 'withdraw'}]
            open_issues = source_review[1]
            if upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx':
                actions = [a for a in actions if a not in {'confirm_initial','submit_review'}]
                if row['lifecycle'] == 'draft' and Capability.CONFIRM_FACT in actor.capabilities:
                    actions += ['resolve_issue'] if open_issues else ['save_decision'] if status not in {'needs_review','empty'} else []
            views.append(CandidateView(
                superseded_by=superseded.get(row['id'],()) if row['lifecycle']=='withdrawn' else (),
                result_anchor=raw.result_anchor,
                rule_history=tuple(rule_history.get(row['id'],[])),
                issues=open_issues,
                source_value=raw.source_value,
                mapping_source=mapping_signature,
                logical_row=raw.logical_row, source_span=raw.source_span,
                reviewed=bool(acknowledge_warning) and status == 'new',
                row_signature=row_signature,
                revisions=raw.revisions,
                suggested_fields=suggested_fields,
                can_receive=can_receive, received=row["id"] in received,
                id=row["id"], location=raw.location, raw_fields=raw.fields, status=status, record=record,
                existing_record=old, issue_code=code, detail=detail, comment=raw.comment,
                candidate_revision=row["revision"], lifecycle=row["lifecycle"], detection_id=row["detection_id"],
                source_number=corrections.get("source_number", raw.fields.get("source_number")) or None,
                existing_detection_id=existing_detection,
                adopted_detection_id=adopted_detection,
                test_date=corrections.get("test_date", raw.fields.get("test_date")) or None,
                evidence=raw.evidence,
                interpreted_fields=fields,
                corrections=corrections, applied_defaults=applied_defaults,
                inherited_fields=inherited_fields, allowed_actions=tuple(actions),
            ))
        for indices in slots.values():
            if len(indices) > 1:
                for index in indices:
                    view = views[index]
                    views[index] = view.model_copy(update={
                        "status": "needs_review", "issue_code": "ambiguous_file_slot",
                        "reviewed": False,
                        "detail": "同一文件同槽位存在多条结果，请先纠错或撤回多余候选，不自动取最后一行。",
                        "allowed_actions": tuple(x for x in view.allowed_actions if x in {"edit", "withdraw"}),
                    })
        counts = Counter(v.status for v in views if v.lifecycle != "withdrawn")
        upload_event = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='upload' ORDER BY sequence LIMIT 1", (upload['id'],)).fetchone()
        parsing_summary = json.loads(upload_event[0]).get('parsing_summary', []) if upload_event else []
        return UploadPreview(
            contract_revision='5' if upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower()=='.xlsx' else '2',
            sheet_confirmation_required=(upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx') or any(json.loads(r[0]).get('defer_confirmation', False) for r in receipts),
            parsing_summary=tuple(parsing_summary),            preview_id=upload["id"], project=config_project, filename=upload["filename"], source_hash=upload["source_hash"],
            upload_sequence=upload["sequence"], uploaded_by=upload["uploaded_by"], uploaded_at=upload["uploaded_at"],
            base_revision=project_row(conn, config_project)["revision"], candidates=tuple(views),
            counts={s: counts[s] for s in ("new", "same_value", "empty", "different_value", "needs_review")},
            can_commit=any(set(v.allowed_actions) & {"confirm_initial", "submit_review"} for v in views),
        )

    def preview(self, upload_id, *, actor):
        require(actor, Capability.VIEW)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            return self._preview(conn, self._upload(conn, upload_id), actor)

    def edit(self, candidate_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        if set(request.fields) - EDITABLE or "project" in request.fields:
            raise WorkflowError("invalid_correction", "不能修改系统 ID、项目归属或未知字段。")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._candidate(conn, candidate_id)
            project = row["project"]
            payload = {"edit": candidate_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            if row["lifecycle"] != "draft" or row["revision"] != request.candidate_revision:
                raise WorkflowError("candidate_changed", "候选已提交或版本已变化；已确认事实不能用解析纠错修改。", 409)
            changes = {**json.loads(row["corrections_json"]), **request.fields}
            detection = row["detection_id"]
            if "source_number" in request.fields:
                detection = uid("detection")
                conn.execute("INSERT INTO workflow_detections(id,project,source_number) VALUES (?,?,?)",
                             (detection, project, request.fields["source_number"].strip() or None))
            conn.execute("""UPDATE workflow_candidates SET corrections_json=?,revision=revision+1,detection_id=?,
                         acknowledge_warning=? WHERE id=?""", (encoded(changes), detection, int(request.acknowledge_source_warning), candidate_id))
            event(conn, project, candidate_id, "correct_extraction", actor, request.reason,
                  {"before": json.loads(row["corrections_json"]), "after": changes, "acknowledge_source_warning": request.acknowledge_source_warning,
                   "revision_confirmed": request.acknowledge_source_warning})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
            return result

    def map_items(self, upload_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            upload = self._upload(conn, upload_id)
            project = upload['project']
            payload = {'map_items': upload_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            config = ProjectConfig.model_validate_json(project_row(conn, project)['config_json'])
            if request.item_id not in {item.id for item in config.items}:
                raise WorkflowError('unknown_item', '请选择已确认配置的检测项目。')
            frozen = frozen_config(conn,upload)
            target = next(i for i in config.items if i.id == request.item_id)
            # Explicit file mapping may add this target to the frozen interpretation;
            # unrelated project changes never leak into the upload.
            updated = frozen.model_copy(update={'items': tuple(i for i in frozen.items if i.id != target.id) + (target,)})
            conn.execute('UPDATE workflow_uploads SET interpretation_config_json=? WHERE id=?',
                         (updated.model_dump_json(),upload_id))
            for candidate_id in request.candidate_ids:
                row = self._candidate(conn, candidate_id)
                if row['upload_id'] != upload_id or row['lifecycle'] != 'draft':
                    raise WorkflowError('candidate_changed', '仅可映射本次上传中未提交的候选。', 409)
                before = json.loads(row['corrections_json'])
                changes = {**before, 'test_item': request.item_id}
                conn.execute('UPDATE workflow_candidates SET corrections_json=?,revision=revision+1 WHERE id=?',
                             (encoded(changes), candidate_id))
                event(conn, project, candidate_id, 'correct_extraction', actor, request.reason,
                      {'before': before, 'after': changes, 'file_mapping': True,
                       'target': target.model_dump(mode='json'), 'scope': upload_id,
                       'source': json.loads(row['raw_json'])['fields']})
                from .review_issues import source_issues
                for issue in source_issues(candidate_id,RawObservation.model_validate_json(row['raw_json'])):
                    if issue.code == 'structure' and issue.detail.startswith('项目规则'):
                        event(conn,project,candidate_id,'resolve_structure',actor,request.reason,
                              {'issue_id':issue.id,'hazard':issue.detail,'explicit_file_mapping':True})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def results(self, project, filters=None, offset=0, limit=100, *, actor):
        require(actor, Capability.VIEW)
        if offset < 0 or not 1 <= limit <= 1000:
            raise WorkflowError("invalid_page", "分页范围无效。")
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            cells = cells_from_rows(selected_rows(conn, project, filters or ResultFilter()))
            return ResultPage(project=project, base_revision=project_row(conn, project)["revision"],
                              total=len(cells), offset=offset, limit=limit, cells=cells[offset:offset+limit])
