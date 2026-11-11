"""Indexes and transaction-local undo reuse preserve the same decisions and facts."""
import json
import sqlite3

from stability_hub.ledger_review import LedgerReview
from stability_hub.models import ProjectConfig
from stability_hub.review_issues import effective_source_decisions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from test_v5_regressions import TECH, preview, confirm_initial
from test_v51_interpretation import receive


def test_cold_ledger_reads_undo_set_once_and_ignores_other_project(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([[f'Assay-{i}', '0M', 99, '%'] for i in range(40)]), context)
    p = receive(flow, p)
    flow.configure(ProjectConfig(project='OTHER'))
    with flow.store.connect() as conn:
        conn.executemany("INSERT INTO workflow_events(project,subject_id,action,actor_id,reason,detail_json) VALUES ('OTHER',?,'review_batch','test','synthetic','{}')",
                         ((f'other-{i}',) for i in range(5000)))
    statements = []
    original = flow.store.connect
    from contextlib import contextmanager

    @contextmanager
    def traced():
        with original() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    flow.store.connect = traced
    fresh = Workflow(flow.store)
    response = LedgerReview(fresh).matrix(p.preview_id, actor=TECH)
    undo = [q for q in statements if "action='undo_review_batch'" in q]
    assert not any("WHERE action='undo_review_batch'" in q for q in undo)
    assert sum("WHERE project='PROJECT-P' AND action='undo_review_batch'" in q for q in undo) == 1
    assert len(response['preview']['candidates']) == 40
    unchanged = LedgerReview(fresh).matrix(p.preview_id, actor=TECH, after_revision=response['preview']['base_revision'])
    assert unchanged['not_modified']


def test_undo_cache_includes_inherited_decisions_and_invalidates_on_transaction_write(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([['Purity', '0M', 99, '%']]), context)
    cid = p.candidates[0].id
    with flow.store.connect() as conn:
        inherited = {'issue_id': 'inherited', 'batch_id': 'parent-batch', 'action': 'keep_body'}
        conn.execute("INSERT INTO workflow_events(project,subject_id,action,actor_id,reason,detail_json) VALUES (?,?,'inherit_source_decision','test','inherit',?)",
                     (p.project, cid, json.dumps(inherited)))
        assert effective_source_decisions(conn, cid) == [inherited]
        conn.execute("SAVEPOINT undo_probe")
        conn.execute("INSERT INTO workflow_events(project,subject_id,action,actor_id,reason,detail_json) VALUES (?,?,'undo_review_batch','test','undo',?)",
                     (p.project, p.preview_id, json.dumps({'batch_id': 'parent-batch'})))
        assert effective_source_decisions(conn, cid) == []
        conn.execute("ROLLBACK TO undo_probe")
        conn.execute("RELEASE undo_probe")
        # sqlite total_changes does not change on ROLLBACK TO itself. Cache must
        # not be reused across a savepoint rollback within the same transaction.
        assert effective_source_decisions(conn, cid) == [inherited]
    with flow.store.read() as conn:
        assert effective_source_decisions(conn, cid) == [inherited]


def test_version8_index_upgrade_backs_up_without_rewriting_facts(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([['Purity', '0M', 99, '%']]), context)
    confirm_initial(flow, p)
    target_home = tmp_path / 'old8'
    target_home.mkdir()
    database = target_home / 'history.sqlite'
    with flow.store.read() as origin, sqlite3.connect(database) as target:
        origin.backup(target)
        for name in ['business_worksheet_slots', 'business_worksheet_leases', 'business_worksheet_plans',
                     'business_worksheets', 'workflow_result_clock']:
            target.execute(f'DROP TABLE {name}')
        for name in ['workflow_candidates_upload', 'workflow_events_subject_action', 'workflow_events_project_action']:
            target.execute(f'DROP INDEX {name}')
        target.execute('DELETE FROM schema_migrations WHERE version>=9')
        target.execute('PRAGMA user_version=8')
        before = target.execute('SELECT id,record_json,source_json FROM workflow_versions').fetchall()
        candidates = target.execute('SELECT id,raw_json FROM workflow_candidates').fetchall()
    upgraded = Store(target_home)
    with upgraded.read() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 10
        assert [tuple(r) for r in conn.execute('SELECT id,record_json,source_json FROM workflow_versions')] == before
        assert [tuple(r) for r in conn.execute('SELECT id,raw_json FROM workflow_candidates')] == candidates
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        for query, args, index in [
            ('SELECT * FROM workflow_candidates WHERE upload_id=?', (p.preview_id,), 'workflow_candidates_upload'),
            ("SELECT * FROM workflow_events WHERE subject_id=? AND action='receive_source'", (p.preview_id,), 'workflow_events_subject_action'),
            ("SELECT * FROM workflow_events WHERE project=? AND action='undo_review_batch'", (p.project,), 'workflow_events_project_action'),
        ]:
            assert any(index in r['detail'] for r in conn.execute('EXPLAIN QUERY PLAN ' + query, args))
    backup = next((target_home / 'migration-backups').glob('*.sqlite'))
    with sqlite3.connect(backup) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 8
        assert conn.execute('SELECT id,record_json,source_json FROM workflow_versions').fetchall() == before
