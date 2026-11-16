"""0.1.5 end-to-end quick comparison on an explicitly disposable outputs copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright, expect

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
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
    sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (store.home/'sources').glob('*')}
    app=create_app(store,[],actor_for_role('015-browser','Acceptance lead',Role.PROJECT_LEAD),frontend=ROOT/'app.html')
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    report={'passed':False,'errors':[],'requests':[]}
    try:
        with sync_playwright() as pw:
            exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser=pw.chromium.launch(headless=True,executable_path=str(exe))
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.set_default_timeout(40000)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('request',lambda req:report['requests'].append({'method':req.method,'url':req.url,'body':req.post_data_json if req.method=='POST' and 'comparison-preview' in req.url else None}))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{port}') else route.abort())
            def wait_chart():
                page.wait_for_function('''()=>{const n=document.querySelector('[data-q-chart]');return n?._chart&&v014Research.q.summary;}''')
            try:
                page.goto(f'http://127.0.0.1:{port}/?project=IP275&view=comparability',wait_until='networkidle')
                expect(page.locator('[data-q-feedback]')).to_contain_text('请选择')
                expect(page.locator('.v014-study-dialog')).to_have_count(0)
                initial_studies=page.evaluate('v014Research.data.studies.length')
                page.evaluate('window.qPeak=0;setInterval(()=>window.qPeak=Math.max(window.qPeak,v014Research.qActive),10)')
                start=time.perf_counter()
                page.locator('[data-q-batch="IP275ADCC20240801"]').click()
                wait_chart()
                report['first_graph_seconds']=time.perf_counter()-start
                first_point=page.locator('[data-q-chart]').first.evaluate('n=>{const c=n._chart,p=c.getDatasetMeta(0).data.find(p=>!p.skip),b=c.canvas.getBoundingClientRect();return b.y+p.y;}')
                assert 0<=first_point<1100
                report['first_graph_visible_without_scroll']=True
                assert page.evaluate('v014Research.data.studies.length')==initial_studies
                assert page.locator('[data-q-table] .v014-matrix').count()==0
                page.locator('[data-q-condition]').filter(has_text='长期').first.click()
                item=page.locator('[data-q-item]').evaluate('e=>[...e.options].find(o=>o.textContent.includes("HMW")).value')
                page.locator('[data-q-item]').select_option(item)
                wait_chart()
                page.locator('[data-q-batch="IP275ADCC20241002"]').click()
                wait_chart()
                page.wait_for_function('v014Research.q.summary.groups.length===2')
                expect(page.locator('[data-q-alignment]')).to_contain_text('不同')
                page.screenshot(path=str(out/'015-strict-preview.png'))
                page.locator('[data-q-align]').click()
                dialog=page.locator('.v014-study-dialog')
                for checkbox in dialog.locator('[data-q-align-field]').all():
                    checkbox.check()
                dialog.locator('[name=reason]').fill('仅本次已选批次、条件和HMW检项对齐原文未注明字段')
                dialog.get_by_role('button',name='确认并刷新对比图').click()
                page.wait_for_function('v014Research.q.summary?.groups.length===1')
                wait_chart()
                data=page.evaluate('''()=>{const n=document.querySelector('[data-q-chart]');return {axis:n._axis,series:n._chart.data.datasets.map(d=>d.label),slots:v014Research.q.summary.total_slots};}''')
                assert data['axis']['min']==0 and data['axis']['max']==5
                assert len(data['series'])==2 and data['slots']==14,data
                report['unsaved_overlay']=data
                page.screenshot(path=str(out/'015-quick-overlay.png'))
                # Actual chart point -> adopted source evidence.
                page.locator('[data-q-chart] canvas').first.scroll_into_view_if_needed()
                position=page.locator('[data-q-chart]').first.evaluate('''n=>{const c=n._chart,p=c.getDatasetMeta(0).data.find(p=>!p.skip),b=c.canvas.getBoundingClientRect();return {x:b.x+p.x,y:b.y+p.y};}''')
                page.mouse.click(position['x'],position['y'])
                expect(page.locator('#v4SourceDrawer')).to_be_visible()
                page.locator('#v4SourceDrawer .v4-drawer-close').click()
                assert page.locator('[data-q-table] .v014-matrix').count()==0
                page.locator('[data-q-tab=table]').click()
                expect(page.locator('[data-q-table]')).to_contain_text('正式采用')
                expect(page.locator('[data-q-table]')).to_contain_text('未评价')
                page.locator('[data-q-tab=trend]').click()
                wait_chart()
                # Before any export/save: all tables and source bytes must be identical.
                assert fingerprints(store)==before
                assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
                report['unsaved_preview_is_read_only']=True
                # Failure and recovery affect this graph only.
                fail_once={'value':True}
                def fail(route):
                    body=route.request.post_data_json
                    if body.get('group_id') and fail_once['value']:
                        fail_once['value']=False
                        route.fulfill(status=503,content_type='application/json',body=json.dumps({'code':'synthetic_failure','message':'模拟单图失败'}))
                    else:
                        route.continue_()
                page.route('**/comparison-preview',fail)
                card=page.locator('[data-q-chart]').first
                card.locator('[data-q-retry]').click()
                expect(card.locator('[data-chart-status]')).to_contain_text('模拟单图失败')
                card.locator('[data-q-retry]').click()
                wait_chart()
                page.unroute('**/comparison-preview',fail)
                # Applied manual axis, quick report, still no saved research.
                card.locator('[data-axis-mode]').select_option('manual')
                card.locator('[data-axis-min]').fill('0')
                card.locator('[data-axis-max]').fill('2')
                card.locator('[data-axis-apply]').click()
                page.wait_for_function('document.querySelector("[data-q-chart]")._axis?.mode==="manual"')
                with page.expect_response(lambda res:res.url.endswith('/reports') and res.request.method=='POST',timeout=90000) as response:
                    card.locator('[data-q-export]').click()
                result=response.value.json()
                assert response.value.status==200,result
                assert result['result_count']==14 and result['chart_count']==1
                expect(card.locator('[data-group-download]')).to_contain_text('已核验')
                with page.expect_download() as download:
                    card.get_by_role('button',name='下载 Word',exact=True).click()
                download.value.save_as(out/'015-quick-report.docx')
                report['quick_report']=result
                assert page.evaluate('v014Research.data.studies.length')==initial_studies
                after_export=fingerprints(store)
                assert all(after_export[k]==v for k,v in before.items() if k!='workflow_exports')
                # Bulk grouping remains optional and a one-sided group still produces a graph.
                page.locator('[data-q-group]').click()
                for checkbox in page.locator('[data-q-group-batch]').all():
                    checkbox.check()
                page.locator('[data-q-role=after]').click()
                expect(page.locator('[data-q-feedback]')).to_contain_text('当前只有变更后')
                wait_chart()
                page.locator('[data-q-group-batch="IP275ADCC20240801"]').check()
                page.locator('[data-q-role=before]').click()
                wait_chart()
                assert not page.locator('[data-q-feedback]').inner_text().endswith('当前只有变更后样品，可以查看曲线，尚不能进行前后比较。')
                # Save after seeing curves. No old preview-number button is required.
                page.locator('[data-q-save]').click()
                dialog=page.locator('.v014-study-dialog')
                dialog.locator('[name=title]').fill('0.1.5快速对比验收')
                dialog.locator('[name=reason]').fill('副本中明确确认当前范围和分组')
                dialog.get_by_role('button',name='确认保存研究',exact=True).click()
                expect(dialog).to_have_count(0)
                wait_chart()
                expect(page.locator('[data-q-feedback]')).to_contain_text('0.1.5快速对比验收')
                assert page.evaluate('v014Research.selected.filters.item')==item
                page.set_viewport_size({'width':390,'height':844})
                expect(page.locator('[data-q-tab=trend]')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out/'015-mobile.png'))
                appearance=page.locator('[data-q-item]').evaluate('e=>[...e.options].find(o=>o.textContent.includes("暂无精确采用值")).value')
                page.locator('[data-q-item]').select_option(appearance)
                expect(page.locator('[data-q-chart-message]')).to_contain_text('没有可绘制')
                assert page.locator('[data-q-chart]').count()==0
                assert page.evaluate('v014Research.q.aligned_fields.length')==0
                with page.expect_response(lambda res:res.url.endswith('/reports') and res.request.method=='POST',timeout=90000) as qualitative:
                    page.locator('[data-q-export-range]').click()
                assert qualitative.value.status==200,qualitative.value.text()
                assert qualitative.value.json()['chart_count']==0
                report['qualitative_report_without_fake_points']=True
                page.locator('[data-q-item]').select_option('*')
                wait_chart()
                assert page.locator('[data-q-chart]').count()>2
                report['max_simultaneous_graphs']=page.evaluate('window.qPeak')
                assert report['max_simultaneous_graphs']<=3
                # A different project cannot inherit batch choices, alignment or old plots.
                page.evaluate('selectProject("IP281",false);switchMainTab("comparability")')
                expect(page.locator('[data-q-feedback]')).to_contain_text('请选择')
                assert page.locator('[data-q-chart]').count()==0
                assert page.evaluate('v014Research.q.aligned_fields.length')==0
                page.evaluate('switchMainTab("reports")')
                expect(page.locator('[data-stability-report]')).to_be_enabled()
                expect(page.locator('#tab-reports')).not_to_contain_text('IP166E')
                # Explicit handoff from ledger keeps the ledger table-first layout.
                page.set_viewport_size({'width':1600,'height':1100})
                page.evaluate('selectProject("IP275",false);switchMainTab("ledger")')
                page.locator('[data-result-batch="IP275ADCC20240801"]').click()
                page.locator('[data-result-condition]').filter(has_text='长期').first.click()
                page.wait_for_function('v012Display.cards.size>0')
                ledger_card=page.locator('[data-trend-card]').filter(has_text='HMW').first
                ledger_card.locator('[data-q-open-item]').click()
                wait_chart()
                assert page.evaluate('v014Research.q.filters.item')==item
                assert page.evaluate('new Set(v014Research.catalog.samples.filter(s=>v014Research.q.ids.has(s.id)).map(s=>s.batch)).size')==1
                assert not report['errors'],report['errors']
                after=fingerprints(store)
                assert all(after[k]==v for k,v in before.items() if k not in {'workflow_projects','workflow_events','workflow_requests','workflow_exports','sqlite_sequence'})
                report.update(passed=True,formal_results_unchanged=True,sources_unchanged=True,
                    bulk_grouping=True,one_sided_graph=True,table_lazy_load=True,project_isolation=True,ledger_handoff=True)
            finally:
                (out/'015-final.html').write_text(page.content(),encoding='utf-8')
                page.screenshot(path=str(out/'015-final.png'))
                browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        sock.close()
        (out/'015-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='requests'},ensure_ascii=False))


if __name__=='__main__':
    main()
