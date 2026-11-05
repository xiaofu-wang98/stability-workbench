"""0.1.4 native browser acceptance on an explicitly disposable outputs copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import threading

import uvicorn
from playwright.sync_api import sync_playwright, expect
from PIL import Image

from stability_hub.api import create_app
from stability_hub.storage import Store
from stability_hub.permissions import Role, actor_for_role
from test_result_display_browser import fingerprints

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True)
    args=parser.parse_args()
    if not args.home.resolve().is_relative_to((ROOT/'outputs').resolve()):
        parser.error('Only an outputs copy is allowed')
    store=Store(args.home)
    out=args.home.parent
    before=fingerprints(store)
    sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.home/'sources').glob('*')}
    app=create_app(store,[],actor_for_role('014-browser','Acceptance lead',Role.PROJECT_LEAD),frontend=ROOT/'app.html')
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    report={'passed':False,'errors':[]}
    try:
        with sync_playwright() as pw:
            exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser=pw.chromium.launch(headless=True,executable_path=str(exe))
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.set_default_timeout(40000)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(f'http://127.0.0.1:{port}') else r.abort())
            try:
                page.goto(f'http://127.0.0.1:{port}/?project=IP275&view=ledger',wait_until='networkidle')
                page.locator('[data-result-batch="IP275ADCC20240801"]').click()
                page.locator('[data-result-condition]').filter(has_text='长期').first.click()
                page.wait_for_function('v012Display.cards.size>0')
                gid=page.evaluate('[...v012Display.cards.values()].find(s=>s.group.label.includes("HMW")).group.id')
                card=page.locator(f'[data-trend-card="{gid}"]')
                card.scroll_into_view_if_needed()
                page.wait_for_function('(id)=>v012Display.cards.get(id)?.trend?.points.length>0',arg=gid)
                state=page.evaluate('(id)=>({axis:v012Display.cards.get(id).trend.axis,values:v012Display.cards.get(id).trend.points.map(p=>p.value)})',gid)
                assert state['axis']['min']==0 and state['axis']['max']==5,state
                report['adcc0801']=state
                card.screenshot(path=str(out/'014-hmw-overview.png'))
                card.locator('[data-axis-mode]').select_option('detail')
                page.wait_for_function('(id)=>v012Display.cards.get(id)?.trend?.axis.mode==="detail"',arg=gid)
                card.locator('[data-axis-mode]').select_option('manual')
                card.locator('[data-axis-min]').fill('0.4')
                card.locator('[data-axis-max]').fill('1')
                card.locator('[data-axis-apply]').click()
                expect(card.locator('[data-trend-status]')).to_contain_text('裁掉')
                card.locator('[data-axis-min]').fill('0')
                card.locator('[data-axis-max]').fill('2')
                card.locator('[data-axis-apply]').click()
                page.wait_for_function('(id)=>v012Display.cards.get(id)?.trend?.axis.mode==="manual"',arg=gid)
                with page.expect_response(lambda r:r.url.endswith('/exports') and r.request.method=='POST') as response:
                    card.locator('[data-chart-export]').click()
                assert response.value.status==200,response.value.text()
                expect(card.locator('[data-export-status]')).to_contain_text('已核验')
                with page.expect_download() as download:
                    card.get_by_role('button',name='下载 PNG',exact=True).click()
                path=out/'014-hmw-manual.png'
                download.value.save_as(path)
                with Image.open(path) as image:
                    assert json.loads(image.info['Description'])['axis']==page.evaluate('(id)=>v012Display.cards.get(id).trend.axis',gid)
                page.locator('.nav-tab').nth(2).click()
                expect(page.locator('[data-new-study]')).to_be_visible()
                page.locator('[data-new-study]').click()
                dialog=page.locator('.v014-study-dialog')
                dialog.locator('[name=title]').fill('0.1.4隔离验收研究')
                dialog.locator('[name=reason]').fill('仅用于副本验收，人工指定前后批次')
                first=dialog.locator('tr').filter(has_text='IP275ADCC20240801').filter(has_not_text='RS').first
                second=dialog.locator('tr').filter(has_text='IP275ADCC20241002').first
                first.locator('select').select_option('before')
                second.locator('select').select_option('after')
                dialog.locator('[data-align=presentation]').check()
                dialog.locator('[data-align=study_id]').check()
                dialog.locator('[name=alignment_reason]').fill('仅此验收研究内显式对齐原文未注明字段，保持正式身份')
                expect(dialog.locator('[data-save]')).to_be_disabled()
                dialog.locator('[data-preview]').click()
                expect(dialog.locator('[data-preview-box]')).to_contain_text('变更前 1')
                dialog.locator('[data-save]').click()
                expect(dialog).to_have_count(0)
                page.wait_for_function('v014Research.results?.cells.length>0')
                expect(page.locator('[data-comparison-matrix]')).to_contain_text('正式采用')
                expect(page.locator('[data-comparison-matrix]')).to_contain_text('未评价')
                group=page.locator('[data-comparison-group]').filter(has_text='HMW').first
                group.scroll_into_view_if_needed()
                expect(group.locator('[data-chart-status]')).to_contain_text('正式采用')
                page.wait_for_function('n=>Boolean(n._chart)',arg=group.element_handle())
                chart=group.evaluate('n=>({axis:{min:n._chart.options.scales.y.min,max:n._chart.options.scales.y.max},sets:n._chart.data.datasets.map(d=>({label:d.label,dash:d.borderDash}))})')
                assert chart['axis']['min']==0 and chart['axis']['max']==5
                assert len(chart['sets'])==2,chart
                assert any(s['dash'] for s in chart['sets']) and any(not s['dash'] for s in chart['sets'])
                report['comparison_curves']=chart['sets']
                group.screenshot(path=str(out/'014-comparison-hmw.png'))
                with page.expect_response(lambda r:r.url.endswith('/reports') and r.request.method=='POST',timeout=90000) as response:
                    group.locator('[data-group-export]').click()
                result=response.value.json()
                assert response.value.status==200,result
                expect(group.locator('[data-group-download]')).to_contain_text('已核验')
                with page.expect_download() as download:
                    group.get_by_role('button',name='下载 Word',exact=True).click()
                download.value.save_as(out/'014-real-comparison.docx')
                report['report_export']=result
                page.locator('[data-mode=release]').click()
                expect(page.locator('[data-study-results]')).to_contain_text('此模式没有')
                page.locator('.nav-tab').nth(3).click()
                expect(page.locator('[data-stability-report]')).to_be_visible()
                expect(page.locator('[data-comparison-report]')).to_be_enabled()
                page.screenshot(path=str(out/'014-report-center.png'))
                page.evaluate('selectProject("IP281",false)')
                page.locator('.nav-tab').nth(2).click()
                expect(page.locator('[data-study-results]')).to_contain_text('尚未选择')
                assert page.locator('[data-comparison-group]').count()==0
                page.locator('.nav-tab').nth(3).click()
                expect(page.locator('[data-stability-report]')).to_be_enabled()
                expect(page.locator('#tab-reports')).not_to_contain_text('IP166E')
                assert not report['errors'],report['errors']
                after=fingerprints(store)
                assert all(after[k]==v for k,v in before.items() if k not in {'workflow_events','workflow_requests','workflow_projects','workflow_exports','sqlite_sequence'})
                assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
                report.update(passed=True,formal_facts_unchanged=True,sources_unchanged=True,all_project_navigation=True)
            finally:
                page.screenshot(path=str(out/'014-final.png'))
                (out/'014-final.html').write_text(page.content(),encoding='utf-8')
                browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        sock.close()
        (out/'014-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
