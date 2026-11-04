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
    try:
        with sync_playwright() as pw:
            exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
            browser=pw.chromium.launch(headless=True,executable_path=str(exe))
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(30000)
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('request',lambda r:report['requests'].append({'method':r.method,'url':r.url}))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{port}') else route.abort())
            try:
                start=time.perf_counter()
                page.goto(f'http://127.0.0.1:{port}/?project={args.project}&view=ledger',wait_until='networkidle')
                page.locator('#v012ResultsPanel').wait_for()
                page.wait_for_function('v012Display.trend && v012Display.chart')
                report['initial_seconds']=time.perf_counter()-start
                assert page.evaluate('v012Display.data.project')==args.project
                assert page.evaluate('v012Display.data.total')>0
                assert page.evaluate('v5Review.scene')=='formal'
                assert page.locator('#ledgerDataTable').inner_text().strip()
                assert not page.locator('#ledgerDataTable').inner_text().find('原文未注明@')>=0
                expect(page.locator('#v012MatrixExcel')).to_be_disabled()
                expect(page.locator('#v012TrendStatus')).not_to_contain_text('失败')
                assert page.locator('#ledgerDataTable').bounding_box()['y'] < page.locator('#v012ResultsPanel').bounding_box()['y']
                report['total_results']=page.evaluate('v012Display.data.total')
                report['initial_points']=page.evaluate('v012Display.trend.points.length')
                page.screenshot(path=str(out/'01-real-matrix-trend.png'),full_page=True)
                if report['initial_points']:
                    # Native click at a rendered data point; inspect the adopted version's source.
                    page.locator('#v012Canvas').scroll_into_view_if_needed()
                    position=page.evaluate('''()=>{const c=v012Display.chart;const p=c.getDatasetMeta(0).data.find(p=>!p.skip);const b=c.canvas.getBoundingClientRect();return {x:b.x+p.x,y:b.y+p.y};}''')
                    page.mouse.click(position['x'],position['y'])
                    expect(page.locator('#v4SourceDrawer')).to_be_visible()
                    page.locator('#v4SourceDrawer .v4-drawer-close').click()
                options=page.locator('#v45Batch option').all_text_contents()
                if len(options)>1:
                    page.locator('#v45Batch').select_option(options[1])
                    page.wait_for_function('v012Display.trend && v012Display.chart')
                    expect(page.locator('#v45Batch')).to_have_value(options[1])
                    page.reload(wait_until='networkidle')
                    expect(page.locator('#v45Batch')).to_have_value(options[1])
                    page.wait_for_function('v012Display.trend && v012Display.chart')
                group_options=page.locator('#v012Analysis option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')
                if len(group_options)>1:
                    for gid in group_options[:3]:
                        page.locator('#v012Analysis').select_option(gid)
                    page.wait_for_function('(id)=>v012Display.analysis===id && v012Display.trend && v012Display.chart',arg=group_options[min(2,len(group_options)-1)])
                expect(page.locator('#v012TrendStatus')).not_to_contain_text('失败')
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(out/'02-mobile-real-results.png'),full_page=True)
                assert not report['errors'],report['errors']
                assert not any('/ledger-matrix' in r['url'] or '/preflight' in r['url'] for r in report['requests'])
                assert not any(r['method']!='GET' for r in report['requests'])
                assert fingerprints(store)==before
                assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in sources.items())
                report.update(passed=True,database_rows_unchanged=True,sources_unchanged=True)
            finally:
                (out/'final.html').write_text(page.content(),encoding='utf-8')
                page.screenshot(path=str(out/'final.png'),full_page=True)
                browser.close()
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        listener.close()
        (out/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='requests'},ensure_ascii=False))


if __name__=='__main__':
    main()
