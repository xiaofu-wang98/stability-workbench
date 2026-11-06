"""0.2.0: ordinary edits keep inherited identities and the existing commit flow."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
from uuid import uuid4

from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
import uvicorn

from stability_hub.api import create_app
from stability_hub.ledger_review import LedgerReview
from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import ReceiveRequest

ROOT = Path(__file__).resolve().parents[2]


def main():
    out = ROOT / 'outputs' / 'v020-acceptance' / ('edit-' + uuid4().hex[:10])
    out.mkdir(parents=True)
    store = Store(out / 'isolated-home')
    actor = actor_for_role('edit-test', 'Edit technician', Role.TECHNICIAN)
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='EDIT-742'))
    book = Workbook()
    book.active.title = 'Results'
    book.active.append(['Test item', 'Time point', 'Result', 'Unit', 'Method'])
    book.active.append(['Purity', '0M', 99, '%', 'UV'])
    path = out / 'synthetic.xlsx'
    book.save(path)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    context = ImportContext(defaults={'sample_type': 'DP', 'batch': 'LOT-NEW', 'presentation': '10mg',
        'study': 'long_term', 'study_id': 'STUDY-NEW', 'condition': '5C'})
    p = flow.upload('EDIT-742', path.name, path.read_bytes(), context, 'upload', actor=actor)
    LedgerReview(flow).receive(p.preview_id, ReceiveRequest(base_revision=p.base_revision,
        idempotency_key='receive', candidate_ids=tuple(c.id for c in p.candidates)), actor=actor)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(store, [], actor, frontend=ROOT / 'app.html'), log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    report = {'passed': False, 'console_errors': []}
    try:
        with sync_playwright() as pw:
            exe = next((Path(os.environ['LOCALAPPDATA']) / 'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser = pw.chromium.launch(headless=True, executable_path=str(exe))
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.set_default_timeout(20000)
            page.on('pageerror', lambda e: report['console_errors'].append(str(e)))
            try:
                page.goto(f'http://127.0.0.1:{port}/?mode=real&project=EDIT-742&view=ledger&upload={p.preview_id}&sheet=Results', wait_until='networkidle')
                page.locator(f'[data-v5="edit"][data-id="{p.candidates[0].id}"]').first.click()
                form = page.locator('#v5ActiveForm')
                expect(form.locator('[data-edit-basic] [name=result]')).to_be_visible()
                expect(form.locator('[name=reason]')).to_be_visible()
                expect(form.locator('[data-edit-advanced]')).not_to_have_attribute('open', '')
                expect(form.locator('.v5-edit-context')).to_contain_text('LOT-NEW')
                expect(form.locator('[name=batch]')).to_have_value('LOT-NEW')
                page.screenshot(path=str(out / '01-simple-edit.png'))
                form.locator('[data-edit-advanced] summary').click()
                expect(form.locator('[name=study_id]')).to_have_value('STUDY-NEW')
                form.locator('[data-edit-advanced] summary').click()
                form.locator('[name=result]').fill('98.5')
                form.locator('[name=reason]').fill('核对原件，更正本次数值')
                form.get_by_role('button', name='保存草稿决定', exact=True).click()
                page.wait_for_function('()=>!v5Review.busy && v5Review.preview.candidates[0].corrections.result === "98.5"')
                current = flow.preview(p.preview_id, actor=actor).candidates[0]
                assert current.corrections == {'result': '98.5'}
                assert current.record.key.batch == 'LOT-NEW' and current.record.key.study_id == 'STUDY-NEW'
                assert flow.results('EDIT-742', actor=actor).total == 0
                page.locator('#v46ConfirmSheet').click()
                page.locator('#v5ActiveForm button.btn-primary').click()
                page.wait_for_function("()=>!v5Review.busy && v5Review.flights.Results.state==='committed'")
                assert flow.results('EDIT-742', actor=actor).cells[0].adopted.text == '98.5%'
                assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash
                assert not report['console_errors']
                page.screenshot(path=str(out / '02-committed.png'))
                report.update(passed=True, cases=['basic_fields_visible', 'advanced_collapsed', 'inherited_identity_visible',
                    'only_changed_field_saved', 'draft_before_commit', 'normal_commit', 'original_unchanged'])
            finally:
                page.screenshot(path=str(out / 'final.png'))
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(out / 'report.json'), **report}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
