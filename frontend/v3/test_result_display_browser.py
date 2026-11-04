"""Real browser acceptance on an explicit outputs copy; never accepts the daily data home."""
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

ROOT=Path(__file__).resolve().parents[2]


def fingerprints(store):
    with store.read() as conn:
        return {r[0]:hashlib.sha256('\n'.join(sorted(repr(tuple(x)) for x in conn.execute(
            'SELECT * FROM "'+r[0]+'"'))).encode()).hexdigest()
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--check-exports',action='store_true',help='Exercise privileged downloads on this outputs copy only')
    args=parser.parse_args()
    if not args.home.resolve().is_relative_to((ROOT/'outputs').resolve()):
        parser.error('Only an outputs copy is allowed')
    out=args.home.parent
    store=Store(args.home)
    before=fingerprints(store)
    sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.home/'sources').glob('*')}
    actor=actor_for_role('local-user','Browser acceptance',Role.TECHNICIAN)
    app=create_app(store,[],actor,frontend=ROOT/'app.html')
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    thread.start()
    report={'passed':False,'errors':[],'requests':[]}
    in_flight=set()
    def on_request(request):
        if '/result-trend?' in request.url:
            in_flight.add(request)
            report['max_simultaneous_trends']=max(report.get('max_simultaneous_trends',0),len(in_flight))
    try:
        with sync_playwright() as pw:
            exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser=pw.chromium.launch(headless=True,executable_path=str(exe))
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(30000)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('request',lambda r:report['requests'].append({'method':r.method,'url':r.url}))
            page.on('request',on_request)
            page.on('requestfinished',lambda r:in_flight.discard(r))
            page.on('requestfailed',lambda r:in_flight.discard(r))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{port}') else route.abort())
            try:
                start=time.perf_counter()
                page.goto(f'http://127.0.0.1:{port}/?project={args.project}&view=ledger',wait_until='networkidle')
                page.locator('#v012ResultsPanel').wait_for()
                page.wait_for_function('v012Display.cards.size>0')
                report['matrix_seconds']=time.perf_counter()-start
                assert page.evaluate('v012Display.data.project')==args.project
                assert page.evaluate('v012Display.data.total')>0
                assert page.evaluate('v5Review.scene')=='formal'
                assert page.locator('#ledgerDataTable').inner_text().strip()
                assert '原文未注明@' not in page.locator('#ledgerDataTable').inner_text()
                expect(page.locator('#v45Batch')).to_have_count(0)
                expect(page.locator('#v45Condition')).to_have_count(0)
                expect(page.locator('#v012Analysis')).to_have_count(0)
                assert page.locator('[data-result-batch]').count()>0
                assert page.locator('[data-result-condition][aria-pressed="true"]').count()==1
                expect(page.locator('#ledgerDataTable')).to_contain_text('质量标准 / 原文记录')
                expect(page.locator('#ledgerDataTable')).to_contain_text('未评价')
                assert page.locator('#ledgerDataTable .badge-pass').count()==0
                expect(page.locator('.v013-matrix-frame')).to_have_attribute('data-compact','false')
                page.locator('[data-table-mode]').click()
                expect(page.locator('.v013-matrix-frame')).to_have_attribute('data-compact','true')
                page.locator('[data-table-mode]').click()
                expect(page.locator('.v013-matrix-frame')).to_have_attribute('data-compact','false')
                expect(page.locator('#v012MatrixExcel')).to_be_disabled()
                assert page.locator('#ledgerDataTable').bounding_box()['y'] < page.locator('#v012ResultsPanel').bounding_box()['y']
                report['total_results']=page.evaluate('v012Display.data.total')
                page.screenshot(path=str(out/'01-controls-matrix.png'))
                page.locator('#v013TrendGrid [data-trend-card]').first.scroll_into_view_if_needed()
                page.wait_for_function('[...v012Display.cards.values()].some(s=>s.chart&&s.trend?.points.length)')
                report['first_charts_seconds']=time.perf_counter()-start
                report['card_count']=page.locator('[data-trend-card]').count()
                report['numeric_card_count']=page.locator('#v013TrendGrid [data-trend-card]').count()
                assert report['card_count']>1
                assert len(page.locator('#v013TrendGrid').evaluate('e=>getComputedStyle(e).gridTemplateColumns').split())==4
                page.wait_for_function('''()=>[...document.querySelectorAll('#v013TrendGrid [data-trend-card]')].slice(0,4).every(n=>v012Display.cards.get(n.dataset.trendCard)?.trend)''')
                page.screenshot(path=str(out/'02-multiple-trends.png'))
                # Native click at a rendered point opens the adopted version's source.
                gid=page.evaluate('[...v012Display.cards.values()].find(s=>s.chart&&s.trend?.points.length).group.id')
                page.locator(f'[data-trend-card="{gid}"] canvas').scroll_into_view_if_needed()
                position=page.evaluate('''(id)=>{const c=v012Display.cards.get(id).chart;const p=c.getDatasetMeta(0).data.find(p=>!p.skip);const b=c.canvas.getBoundingClientRect();return {x:b.x+p.x,y:b.y+p.y};}''',gid)
                page.mouse.click(position['x'],position['y'])
                expect(page.locator('#v4SourceDrawer')).to_be_visible()
                page.locator('#v4SourceDrawer .v4-drawer-close').click()
                # A single failed card remains retryable and cannot clear successful neighbours.
                fail_once={'pending':True}
                def simulate_failure(route):
                    if gid in route.request.url and fail_once['pending']:
                        fail_once['pending']=False
                        route.fulfill(status=503,content_type='application/json',body=json.dumps({'code':'acceptance_failure','message':'模拟单图读取失败'}))
                    else:
                        route.continue_()
                page.route('**/result-trend?**',simulate_failure)
                failed_card=page.locator(f'[data-trend-card="{gid}"]')
                failed_card.locator('[data-retry]').click()
                expect(failed_card.locator('[data-trend-status]')).to_contain_text('趋势读取失败')
                failed_card.locator('[data-retry]').click()
                page.wait_for_function('(id)=>v012Display.cards.get(id)?.chart!=null',arg=gid)
                page.unroute('**/result-trend?**',simulate_failure)
                report['single_card_failure_retry']=True
                batches=page.locator('[data-result-batch]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.resultBatch)')
                if len(batches)>1:
                    page.locator('[data-result-batch]').nth(1).click()
                    expect(page.locator('[data-result-batch][aria-pressed="true"]')).to_have_attribute('data-result-batch',batches[1])
                    assert page.evaluate('v012Display.visible().every(c=>c.slot_key.batch===v45Ledger.batch)')
                    page.reload(wait_until='networkidle')
                    expect(page.locator('[data-result-batch][aria-pressed="true"]')).to_have_attribute('data-result-batch',batches[1])
                condition_count=page.locator('[data-result-condition]').count()
                if condition_count>1:
                    for index in range(min(3,condition_count)):
                        page.locator('[data-result-condition]').nth(index).click()
                    assert page.evaluate('v012Display.visible().every(c=>c.slot_key.study===v45Ledger.study&&c.slot_key.condition===v45Ledger.condition)')
                    expected=page.locator('[data-result-condition][aria-pressed="true"]').inner_text()
                    page.reload(wait_until='networkidle')
                    expect(page.locator('[data-result-condition][aria-pressed="true"]')).to_have_text(expected)
                expect(page.locator('#v4LedgerSourceChoice')).not_to_be_visible()
                page.locator('.v013-source-entry summary').click()
                expect(page.locator('#v4LedgerSourceChoice')).to_be_visible()
                page.locator('.v013-source-entry summary').click()
                page.locator('#v013TrendGrid [data-trend-card]').first.scroll_into_view_if_needed()
                page.wait_for_function('[...v012Display.cards.values()].some(s=>s.chart&&s.trend?.points.length)')
                assert page.evaluate('[...v012Display.cards.values()].every(s=>!s.trend||(s.trend.scope[2]===v45Ledger.study&&s.trend.scope[4]===v45Ledger.condition&&s.trend.batches.includes(v45Ledger.batch)))')
                assert not any('失败' in t for t in page.locator('[data-trend-status]').all_text_contents())
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#v013TrendGrid [data-trend-card]').first.scroll_into_view_if_needed()
                assert len(page.locator('#v013TrendGrid').evaluate('e=>getComputedStyle(e).gridTemplateColumns').split())==1
                page.screenshot(path=str(out/'03-mobile-trends.png'))
                assert not report['errors'],report['errors']
                assert not any('/ledger-matrix' in r['url'] or '/preflight' in r['url'] for r in report['requests'])
                assert not any(r['method']!='GET' for r in report['requests'])
                assert report['max_simultaneous_trends']<=3
                assert fingerprints(store)==before
                assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
                report.update(passed=True,database_rows_unchanged=True,sources_unchanged=True)
                if args.check_exports:
                    # Export audits are allowed only on the disposable copy, after the read-only proof.
                    page.wait_for_function('v012Display.active===0 && v012Display.queue.length===0')
                    page.wait_for_load_state('networkidle')
                    app.state.actor=actor_for_role('acceptance-lead','Lead',Role.PROJECT_LEAD)
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.reload(wait_until='networkidle')
                    page.locator('#v013TrendGrid [data-trend-card]').first.scroll_into_view_if_needed()
                    page.wait_for_function('[...v012Display.cards.values()].some(s=>s.chart&&s.trend?.points.length)')
                    gid=page.evaluate('[...v012Display.cards.values()].find(s=>s.chart&&s.trend?.points.length).group.id')
                    state=page.evaluate('(id)=>({slots:v012Display.cards.get(id).slotIds,selection:v012Display.cards.get(id).trend.selection_id})',gid)
                    card=page.locator(f'[data-trend-card="{gid}"]')
                    with page.expect_response(lambda r:r.url.endswith('/api/v1/exports') and r.request.method=='POST') as response:
                        card.locator('[data-chart-export]').click()
                    result=response.value
                    assert result.status==200,result.text()
                    assert sorted(result.request.post_data_json['slot_ids'])==sorted(state['slots'])
                    expect(card.locator('[data-export-status]')).to_contain_text('已核验')
                    with page.expect_download() as download:
                        card.get_by_role('button',name='下载 PNG',exact=True).click()
                    path=out/'card-export.png'
                    download.value.save_as(path)
                    from PIL import Image
                    with Image.open(path) as img:
                        assert json.loads(img.info['Description'])['selection_id']==state['selection']
                    with page.expect_download() as download:
                        card.get_by_role('button',name='下载 Excel',exact=True).click()
                    download.value.save_as(out/'card-export.xlsx')
                    after=fingerprints(store)
                    assert all(after[k]==v for k,v in before.items() if k!='workflow_exports')
                    report['card_export_downloads_verified']=True
                    assert report['max_simultaneous_trends']<=3
            finally:
                (out/'final.html').write_text(page.content(),encoding='utf-8')
                page.screenshot(path=str(out/'final.png'))
                browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        listener.close()
        (out/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='requests'},ensure_ascii=False))


if __name__=='__main__':
    main()
