"""Rehearse schema-5 upgrade using a synthetic historical database copy."""
import json
import sqlite3
import pytest

from stability_hub.migrations import MIGRATIONS,CURRENT_VERSION
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role,actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import ExportRequest
from stability_hub.workflow_excel import ExcelExports
from test_v5_regressions import TECH,preview,confirm_initial


@pytest.mark.parametrize('source_version',[5,7])
def test_a03_a09_old_schema_copy_preserves_versions_and_verified_export(tmp_path,xlsx,context,source_version):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    confirm_initial(flow,p)
    old_home = tmp_path/'schema5-copy'
    old_home.mkdir()
    database = old_home/'history.sqlite'
    tables = ['workflow_projects','documents','workflow_uploads','workflow_detections','workflow_candidates',
              'workflow_versions','workflow_version_states','workflow_adoptions','workflow_events']
    with sqlite3.connect(database) as target,flow.store.connect() as origin:
        for version in range(1,source_version+1):
            for sql in MIGRATIONS[version]:
                target.execute(sql)
        target.execute(f'PRAGMA user_version={source_version}')
        for table in tables:
            columns = [r[1] for r in target.execute(f'PRAGMA table_info({table})')]
            for row in origin.execute(f'SELECT * FROM {table}'):
                values = dict(row)
                if table == 'workflow_candidates':
                    raw = json.loads(values['raw_json'])
                    raw.pop('source_value',None)
                    values['raw_json'] = json.dumps(raw)
                if table == 'workflow_versions':
                    record = json.loads(values['record_json'])
                    record['result'].pop('interpretation',None)
                    values['record_json'] = json.dumps(record)
                    sources = json.loads(values['source_json'])
                    for source in sources:
                        source['raw_json'].pop('source_value',None)
                        source.pop('issue_decisions',None)
                    values['source_json'] = json.dumps(sources)
                target.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                               [values[c] for c in columns])
        original = target.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions').fetchall()
    store = Store(old_home)
    upgraded = Workflow(store)
    upgraded.configure(ProjectConfig(project='PROJECT-P'))
    with store.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == CURRENT_VERSION
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert all(r[0] is not None for r in conn.execute('SELECT interpretation_config_json FROM workflow_uploads'))
        assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions')] == original
    backup = next((old_home/'migration-backups').glob('*.sqlite'))
    with sqlite3.connect(backup) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == source_version
        assert conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions').fetchall() == original
    cells = upgraded.results('PROJECT-P',actor=TECH)
    assert cells.total == 1 and cells.cells[0].display.text == '99%'
    out = ExcelExports(upgraded).create(ExportRequest(project='PROJECT-P',base_revision=cells.base_revision),
        actor=actor_for_role('lead','Lead',Role.PROJECT_LEAD))
    assert out.state == 'verified'
