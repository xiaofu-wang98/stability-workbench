"""Native browser edit, merge, split, scoped save and normal worksheet commit on a temporary store."""
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from uuid import uuid4

from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
import uvicorn

from stability_hub.api import create_app
from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.ledger_review import LedgerReview
from stability_hub.workflow_contracts import ReceiveRequest

ROOT = Path(__file__).resolve().parents[2]


def main():
    out = ROOT/'outputs/v5-acceptance'/('manual-ui-'+uuid4().hex[:8])
    out.mkdir(parents=True)
    path = out/'source.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = 'Results'
    sheet.append(['Test item','Time point','Result','Unit'])
    sheet.append(['Assay','0M',99,'%'])
    sheet.append(['Assay','3M',98,'%'])
    book.save(path)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    actor = actor_for_role('local-user', 'Synthetic technician', Role.TECHNICIAN)
    store = Store(out/'isolated-home')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='MANUAL-UI'))
    p = flow.upload('MANUAL-UI',path.name,path.read_bytes(),ImportContext(defaults={
        'sample_type':'DP','batch':'B1','presentation':'10mg','study':'long_term','study_id':'S1','condition':'5C'}),'synthetic',actor=actor)
    LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive',
        candidate_ids=tuple(c.id for c in p.candidates),defer_confirmation=True),actor=actor)
    listener = socket.socket()
    listener.bind(('127.0.0.1',0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(store,[],actor,frontend=ROOT/'app.html'),log_level='error'))
    thread = threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    thread.start()
    report = {'passed':False,'errors':[]}
    try:
        with sync_playwright() as pw:
            exe = next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser = pw.chromium.launch(headless=True,executable_path=str(exe))
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(20000)
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            try:
                page.goto(f'http://127.0.0.1:{port}/?mode=real&project=MANUAL-UI&view=ledger&upload={p.preview_id}&sheet=Results',wait_until='networkidle')
                page.locator('#v5Preflight').wait_for()
                page.locator('[data-v5="manualSheet"]').click()
                grid = page.locator('#manualGrid')
                grid.locator('[data-manual-cell="A1"]').click()
                grid.locator('[data-manual-cell="B1"]').click(modifiers=['Shift'])
                page.locator('#manualMerge').click()
                expect(grid.locator('[data-manual-cell="A1"]')).to_have_attribute('colspan','2')
                page.locator('#manualSplit').click()
                expect(grid.locator('[data-manual-cell="B1"]')).to_have_count(1)
                cell = grid.locator('[data-manual-cell="C2"]')
                cell.click()
                cell.fill('97')
                page.locator('#manualResult').click()
                expect(page.locator('#manualResults [data-key="result"]')).to_have_value('97')
                page.locator('#manualResults [data-copy]').click()
                expect(page.locator('#manualResults [data-key="result"]').nth(1)).to_have_value('')
                expect(page.locator('#manualResults [data-key="time_point"]').nth(1)).to_have_value('')
                page.locator('#manualResults [data-remove]').nth(1).click()
                form = page.locator('#v5ActiveForm')
                form.locator('[name="reason"]').fill('人工核对原件，测试单格修改与明确结果归属')
                form.locator('.btn-primary').click()
                expect(page.locator('#manualPreview')).to_contain_text('新结果 1 条')
                assert flow.results('MANUAL-UI',actor=actor).cells == ()
                page.locator('#manualReviewed').check()
                page.screenshot(path=str(out/'01-manual-preview.png'))
                revision = page.evaluate('v5Review.preview.base_revision')
                form.locator('.btn-primary').click()
                page.wait_for_function('(r)=>v5Review.preview.base_revision>r&&!v5Review.busy',arg=revision)
                assert any(c['corrections'].get('result')=='97' for c in page.evaluate('v5Review.preview.candidates'))
                expect(page.locator('#v46ConfirmSheet')).to_be_enabled()
                page.locator('#v46ConfirmSheet').click()
                page.locator('#v5ActiveForm .btn-primary').click()
                page.wait_for_function("()=>!v5Review.busy&&v5Review.flights.Results.state==='committed'")
                assert len(flow.results('MANUAL-UI',actor=actor).cells)==2
                assert hashlib.sha256(path.read_bytes()).hexdigest()==source_hash
                assert not report['errors'], report['errors']
                page.screenshot(path=str(out/'02-committed.png'))
                report.update(passed=True,cases=['native_edit','merge','unmerge','split_requires_own_values','preview_before_save','no_fact_before_commit','normal_commit','original_unchanged'])
            finally:
                (out/'final.html').write_text(page.content(),encoding='utf-8')
                page.screenshot(path=str(out/'final.png'))
                browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        listener.close()
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(out/'report.json'),**report},ensure_ascii=True),flush=True)


if __name__=='__main__':
    main()
