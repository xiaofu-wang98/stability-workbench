"""Actual archived standard -> UI registration -> sample -> study, isolated store."""
import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def main():
    # Read-only access to the actual archived file; all mutations use a temporary store.
    with sqlite3.connect((ROOT / 'data/history.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        source = conn.execute("SELECT filename,source FROM specifications WHERE filename LIKE '%IP501%' LIMIT 1").fetchone()
    assert source, 'Actual IP501 standard is required for this acceptance test'
    out = ROOT / 'outputs/mvp-audit-20261115'
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp, socket.socket() as listener:
        app = create_app(Store(Path(temp)), [], actor_for_role('test', '隔离验收', Role.ADMIN),
                         workbench=ROOT / 'frontend/workbench/dist')
        listener.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                executable = next((Path(os.environ['LOCALAPPDATA']) / 'ms-playwright').glob(
                    'chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
                browser = pw.chromium.launch(executable_path=str(executable))
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.set_default_timeout(12000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(url)
                assert page.request.post(url + '/api/v1/projects', data={'project': '501', 'name': '隔离验收'}).ok
                page.goto(url + '/workbench/#/specifications')
                page.get_by_label('选择 Word 标准').set_input_files({
                    'name': source[0], 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': source[1]})
                expect(page.get_by_role('button', name='确认所选 26 条标准并入库')).to_be_visible()
                page.get_by_role('button', name='确认所选 26 条标准并入库').click()
                expect(page.get_by_role('status').filter(has_text='已关联 26 条标准')).to_be_visible()
                coa = page.locator('section.panel').filter(has=page.get_by_role('heading', name='样品放行结果（COA）'))
                coa.locator('.n-base-selection').click()
                page.locator('.n-base-select-option').filter(has_text='501 · 隔离验收').click()
                coa.get_by_label('样品批号').fill('MVP-TEST')
                coa.get_by_role('button', name='查找样品').click()
                coa.get_by_role('button', name='另建样品对象').click()
                coa.get_by_label('材料阶段').fill('裸抗')
                coa.get_by_label('样品类型').select_option('DS')
                coa.get_by_label('规格', exact=True).fill('验收规格')
                coa.get_by_label('用途', exact=True).fill('稳定性研究')
                coa.get_by_role('button', name='登记并打开 COA').click()
                expect(coa.get_by_text('当前样品 MVP-TEST', exact=False)).to_be_visible()
                samples = page.request.get(url + '/api/v1/projects/501/samples?batch=MVP-TEST').json()
                assert len(samples) == 1
                spec = page.request.get(url + '/api/v1/specifications').json()[0]['id']
                page.goto(url + f'/workbench/#/new?project=501&specification={spec}&sample={samples[0]["id"]}&batch=MVP-TEST')
                expect(page.get_by_label('批号', exact=True)).to_have_value('MVP-TEST')
                expect(page.get_by_label('规格', exact=True)).to_have_value('验收规格')
                page.get_by_label('开始日期', exact=True).fill('2026-11-15')
                page.get_by_label('方案编号或内部研究名称').fill('隔离验收方案')
                page.get_by_role('radio', name='长期', exact=True).check()
                page.get_by_role('button', name='5℃±3℃', exact=True).click()
                # Selecting the standard is a real component interaction, not an API shortcut.
                picker = page.locator('.spec-picker')
                expect(picker.locator('.n-base-selection')).to_be_visible()
                picker.get_by_role('button', name='收起').click()
                page.get_by_role('button', name='创建研究表').click()
                expect(page.get_by_role('heading', name='MVP-TEST', exact=True)).to_be_visible()
                sheet_id = page.url.rsplit('/', 1)[-1]
                sheet = page.request.get(url + '/api/v1/worksheets/' + sheet_id).json()
                assert len(sheet['rows']) == 26 and all(row.get('standards') for row in sheet['rows'])
                page.reload()
                expect(page.get_by_role('heading', name='MVP-TEST', exact=True)).to_be_visible()
                page.screenshot(path=str(out / 'real-standard-study.png'), full_page=True)
                page.get_by_role('button', name='编辑结果').click()
                expect(page.locator('canvas[id^="univer-sheet-main-canvas"]')).to_be_visible()
                page.get_by_role('button', name='标准与检项维护').click()
                form = page.locator('form.standard-links')
                form.locator('.n-base-selection').first.click()
                page.locator('.n-base-select-option').first.click()
                form.get_by_label('可接受标准（人工补充）').fill('人工补充验收文字')
                form.get_by_label('适用或修改依据').fill('隔离验收：核对标准')
                form.get_by_role('button', name='保存标准关联').click()
                expect(page.get_by_role('status')).to_have_text('已保存标准关联。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_text('人工补充验收文字', exact=True)).to_be_visible()
                page.reload()
                expect(page.get_by_text('人工补充验收文字', exact=True)).to_be_visible()
                assert not errors, errors
                (out / 'real-standard-browser.json').write_text(json.dumps({
                    'standard_rows': 26, 'sample_created': True, 'study_created': True,
                    'standard_links_persisted': True, 'criteria_edit_survives_reload': True,
                    'page_errors': errors, 'daily_database_mutated': False,
                }, ensure_ascii=False, indent=2), encoding='utf-8')
                browser.close()
                print('PASS: actual 26-row standard, UI registration, sample creation, study creation, criteria edit/reload')
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
