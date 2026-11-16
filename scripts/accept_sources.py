"""Replay frozen real sources in a new isolated home, preserving unresolved business answers."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from stability_hub.models import ProjectConfig, ImportContext
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.permissions import actor_for_role, Role
from stability_hub.review_tasks import current_review
from stability_hub.review_batches import preview_batch, apply_batch, undo_batch
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchApply, TaskBatchUndo
from stability_hub.ledger_review import LedgerReview
from stability_hub.workflow_contracts import ReceiveRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-home', type=Path, required=True)
    parser.add_argument('--replay-home', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'outputs/v5-acceptance'
    assert args.replay_home.resolve().is_relative_to(root.resolve()) and not args.replay_home.exists()
    actor = actor_for_role('v55-real-replay', '隔离原件回归', Role.TECHNICIAN)
    flow = Workflow(Store(args.replay_home))
    with sqlite3.connect((args.source_home / 'history.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        originals = [dict(r) for r in conn.execute('SELECT u.*,d.stored_path FROM workflow_uploads u JOIN documents d ON d.sha256=u.source_hash')]
    assert originals, 'No frozen uploads available for acceptance'
    report = {'scope': 'Fresh replay of frozen real files; not the migrated daily decisions', 'files': []}
    for original in originals:
        config = ProjectConfig.model_validate_json(original['interpretation_config_json'] or original['config_json'])
        flow.configure(config)
        p = flow.upload(original['project'], original['filename'], Path(original['stored_path']).read_bytes(),
            ImportContext.model_validate_json(original['context_json']), uuid4().hex, actor=actor)
        assert p.source_hash == original['source_hash']
        LedgerReview(flow).receive(p.preview_id, ReceiveRequest(base_revision=p.base_revision, idempotency_key=uuid4().hex,
            candidate_ids=tuple(c.id for c in p.candidates), defer_confirmation=True), actor=actor)
        def state(p=p):
            with flow.store.connect() as conn:
                return current_review(flow, conn, flow._upload(conn, p.preview_id), actor, _shared=True)
        before, sheets, flights, tasks, _ = state()
        entry = {'project': p.project, 'upload_id': p.preview_id, 'source_hash': p.source_hash,
                 'candidates': len(before.candidates), 'task_codes': dict(Counter(t.code for t in tasks)), 'cases': {}}
        def apply(scope, p=p):
            with flow.store.connect() as conn:
                info = preview_batch(flow, conn, p.preview_id, scope, actor)
            result = apply_batch(flow, p.preview_id, TaskBatchApply(base_revision=info['base_revision'],
                idempotency_key=uuid4().hex, reason='隔离原件明确证据回归，不代表业务验收', scope=scope, fingerprint=info['fingerprint']), actor=actor)
            return result, info
        def undo(result, p=p, state=state):
            current = state()[0]
            undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=current.base_revision, idempotency_key=uuid4().hex,
                reason='撤销隔离测试并核对原始问题恢复', batch_id=result['batch_id']), actor=actor)
        # Select by task semantics, not project identity, worksheet name or coordinates.
        revisions = [t for t in tasks if t.code == 'source_revision' and t.fields == ('result',)
                     and t.proposal.get('eligible') and t.proposal.get('operation') == 'keep_active']
        if revisions:
            chosen = [t for t in revisions if t.section == revisions[0].section][:2]
            result, info = apply(TaskBatchScope(section=chosen[0].section,
                task_ids=tuple(t.id for t in chosen), operation='suggested'))
            now = {c.id: c for c in state()[0].candidates}
            assert info['changes']
            for member in info['members']:
                for field, value in member.get('fields', {}).items():
                    assert now[member['candidate_id']].corrections[field] == value
            undo(result)
            assert all(t.id in {x.id for x in state()[3]} for t in chosen)
            entry['cases']['each_revision_own_value_save_reopen_undo'] = True
        conflicts = [t for t in state()[3] if t.code in {'identity_conflict', 'method_conflict'}]
        assert all(t.actions == ('manual',) and not t.proposal.get('eligible') for t in conflicts)
        entry['cases']['explicit_identity_conflicts_remain_unresolved'] = len(conflicts)
        rows = [t for t in state()[3] if t.code == 'row_exclusion' and t.proposal.get('eligible')]
        if rows:
            row = rows[0]
            result, info = apply(TaskBatchScope(section=row.section, task_ids=(row.id,), operation='suggested'))
            ids = {m['candidate_id'] for m in row.members}
            assert ids and all(c.lifecycle == 'withdrawn' for c in state()[0].candidates if c.id in ids)
            undo(result)
            restored = {c.id: c for c in state()[0].candidates}
            original_candidates = {c.id: c for c in before.candidates}
            assert all(restored[cid].lifecycle == original_candidates[cid].lifecycle for cid in ids)
            entry['cases']['row_exclusion_and_undo'] = True
        if not revisions and not rows:
            entry['cases']['read_only_frozen_source_review'] = True
        entry['ready_sheets'] = [f.section for f in state()[2] if f.can_commit]
        report['files'].append(entry)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(entry, ensure_ascii=True), flush=True)
    report['passed'] = True
    report['business_acceptance'] = 'pending; bilingual choices deliberately unresolved'
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
