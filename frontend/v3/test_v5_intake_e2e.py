"""Real V5 UI commands on isolated stores/ports; never writes through hidden requests."""
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
import threading
import time
from uuid import uuid4

from openpyxl import Workbook,load_workbook
from openpyxl.comments import Comment
from PIL import Image
from playwright.sync_api import sync_playwright,expect
import uvicorn

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role,actor_for_role
from stability_hub.storage import Store

ROOT=Path(__file__).resolve().parents[2]


def source(path,rows):
    book=Workbook()
    s=book.active
    s.title='Results'
    s.append(['Project','Sample type','Batch','Presentation','Study','Study id','Condition','Test item','Method','Time point','Result','Unit'])
    for item,value in rows:
        s.append(['V5-UI','DP','B1','10mg','long_term','S1','5C',item,'UV','0M',value,'%'])
        if item=='Percent':
            s.cell(s.max_row,11).number_format='0.0%'
        if item=='Problem' and isinstance(value,str) and value.startswith('='):
            s.cell(s.max_row,11).comment=Comment('原结果录入错误，须核对更正','synthetic')
    book.save(path)


def main():
    out=ROOT/'outputs/v5-acceptance'/('ui-'+uuid4().hex[:10])
    out.mkdir(parents=True)
    html=out/'stability_hub_v5.html'
    subprocess.run([sys.executable,str(ROOT/'frontend/v3/build.py'),'--output',str(html)],cwd=ROOT,check=True)
    store=Store(out/'isolated-home')
    servers=[]
    ports={}
    report={'scope':'V5 real UI synthetic acceptance','console_errors':[],'cases':{},'human_gold_count':0}
    try:
        for role in (Role.TECHNICIAN,Role.SENIOR_TECHNICIAN,Role.PROJECT_LEAD):
            listener=socket.socket()
            listener.bind(('127.0.0.1',0))
            ports[role]=listener.getsockname()[1]
            app=create_app(store,[ProjectConfig(project='V5-UI')],actor_for_role(role.value,role.value,role),frontend=html)
            server=uvicorn.Server(uvicorn.Config(app,log_level='warning'))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
            thread.start()
            servers.append((server,thread,listener))
            deadline=time.monotonic()+15
            while not server.started and time.monotonic()<deadline:
                time.sleep(.05)
            assert server.started
        source(out/'first.xlsx',[(n,99) for n in ['Keep','Correct','Retest','Same','Problem']])
        source(out/'second.xlsx',[('Keep',98),('Correct',98.5),('Retest',98.5),('Same',99),('Problem','=99'),('Exclude',7),('Percent',.995)])
        exe=next((Path(os.environ['LOCALAPPDATA'])/'ms-playwright').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
        pw=sync_playwright().start()
        if pw:
            browser=pw.chromium.launch(headless=True,executable_path=str(exe))
            page=browser.new_page(viewport={'width':1440,'height':1100},accept_downloads=True)
            page.on('pageerror',lambda error:report['console_errors'].append(str(error)))
            def console(message):
                if message.type=='error':
                    if '409' in message.text and message.location.get('url','').endswith('/bulk-apply'):
                        report.setdefault('expected_http_rejections',[]).append(message.text)
                    else:
                        report['console_errors'].append(message.text)
            page.on('console',console)
            def visit(role,upload=None):
                url=f'http://127.0.0.1:{ports[role]}/?mode=real&project=V5-UI'
                if upload:
                    url+='&view=ledger&upload='+upload+'&sheet=Results'
                page.goto(url,wait_until='networkidle')
                if upload:
                    expect(page.locator('#v5Preflight')).to_be_visible(timeout=20000)
            def upload(name):
                page.get_by_role('button',name='数据接收',exact=True).click()
                page.locator('#v3FileInput').set_input_files(str(out/name))
                page.locator('#v44ConfirmFile, #v44RouteProject, #v5DirectWorksheet, input[name="project"]').first.wait_for(state='visible',timeout=20000)
                if page.locator('input[name="project"]').is_visible():
                    page.locator('input[name="project"]').fill('V5-UI')
                    page.locator('form[onsubmit*="submitUnifiedProject"] button.btn-primary').click()
                    page.locator('#v5DirectWorksheet').wait_for(state='visible',timeout=20000)
                else:
                    page.locator('#v44ConfirmFile, #v44RouteProject, #v5DirectWorksheet').first.wait_for(state='visible',timeout=20000)
                if page.locator('#v5DirectWorksheet').is_visible():
                    page.locator('#v5DirectWorksheet').click()
                elif page.locator('#v44RouteProject').is_visible():
                    page.locator('#v44RouteProject').select_option('V5-UI')
                    page.get_by_role('button',name='确认归属并预览',exact=True).click()
                    expect(page.locator('#v44ConfirmFile')).to_be_visible(timeout=20000)
                    page.locator('#v44ConfirmFile').check()
                    page.locator('#v5Receive').click()
                else:
                    expect(page.locator('#v44ConfirmFile')).to_be_visible(timeout=20000)
                    page.locator('#v44ConfirmFile').check()
                    page.locator('#v5Receive').click()
                expect(page.locator('#v5Preflight')).to_be_visible(timeout=20000)
                expect(page.locator('#v4LedgerSourceChoice')).to_contain_text(name,timeout=20000)
                page.wait_for_function('(name)=>v5Review.preview?.filename===name',arg=name)
                return page.url.split('upload=')[1].split('&')[0]
            def row(name):
                cid=page.evaluate('(name)=>v5Review.preview.candidates.find(c=>c.raw_fields.test_item===name||v5Review.label(c.record?.key?.item||c.interpreted_fields.test_item)===name)?.id',name)
                node=page.locator(f'#ledgerDataTable [data-candidate="{cid}"]')
                details=node.locator('details.v53-cell-details:not([open]) > summary')
                if details.count():details.click()
                issues=node.locator('details.v51-cell-issues:not([open])')
                if issues.count():
                    issues.locator('summary').click()
                return node
            def first_cell(tab=page):
                node=tab.locator('#ledgerDataTable [data-candidate]').first
                closed=node.locator('details.v53-cell-details:not([open]) > summary')
                if closed.count():closed.click()
                return node
            def click_tool(name):
                button=page.get_by_role('button',name=name,exact=True)
                if not button.is_visible():
                    secondary=page.locator('.v52-secondary:not([open]) > summary')
                    if secondary.count(): secondary.click()
                    page.get_by_text('来源与规则设置',exact=True).click()
                button.click()

            def confirm_sheet():
                page.locator('#v46ConfirmSheet').click()
                btn = page.locator('#v5ActiveForm button.btn-primary')
                try:
                    btn.wait_for(state='visible', timeout=2000)
                    btn.click()
                except Exception:
                    pass
            def open_regions():
                secondary=page.locator('.v52-secondary:not([open]) > summary')
                if secondary.count(): secondary.click()
                closed=page.locator('#v51Unclassified:not([open]) > summary')
                if closed.count(): closed.click()
            def save_decision(name,action):
                if not row(name).locator('[data-v5="decision"]').is_visible():
                    row(name).locator('details').last.locator('summary').click()
                row(name).locator('[data-v5="decision"]').click()
                f=page.locator('#v5ActiveForm')
                f.locator('[name="action"]').select_option(action)
                f.locator('[name="reason"]').fill('合成样例人工明确处置 '+action)
                f.get_by_role('button',name='保存草稿决定',exact=True).click()
                expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本次决定已保存草稿',timeout=20000)
            visit(Role.TECHNICIAN)
            first=upload('first.xlsx')
            with store.connect() as conn:
                assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]==0
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled()
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            second=upload('second.xlsx')
            expect(row('Percent')).to_contain_text('× 100')
            report['uploads']={'first':first,'second':second}
            expect(page.locator('#v46ConfirmSheet')).to_be_disabled()
            with store.connect() as conn:
                before=[tuple(r) for r in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions ORDER BY id')]
                assert len(before)==5
            # Resolve comment only; formula must remain open after refresh.
            problem=row('Problem')
            problem.locator('[data-issue-code="source_comment"]').get_by_role('button',name='处理此问题').click()
            f=page.locator('#v5ActiveForm')
            f.locator('[name="action"]').select_option('keep_body')
            f.locator('[name="reason"]').fill('合成批注单独决定，公式仍待处理')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(row('Problem').locator('[data-issue-code="formula_result"]')).to_be_visible(timeout=20000)
            page.reload(wait_until='networkidle')
            expect(row('Problem').locator('[data-issue-code="source_comment"]')).to_have_count(0)
            row('Problem').locator('[data-issue-code="formula_result"]').get_by_role('button',name='处理此问题').click()
            f=page.locator('#v5ActiveForm')
            f.locator('[name="action"]').select_option('manual')
            f.locator('[name="result"]').fill('98.5')
            f.locator('[name="reason"]').fill('合成样例明确公式解释值')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('决定已保存',timeout=20000)
            for name,action in [('Keep','keep_old'),('Correct','correction'),('Retest','retest'),('Same','repeat_source'),('Problem','keep_old'),('Exclude','exclude')]:
                save_decision(name,action)
            page.wait_for_function('!window.v5Review?.busy')
            page.screenshot(path=str(out/'01-draft-decisions.png'),full_page=True)
            with store.connect() as conn:
                assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions ORDER BY id')]==before
            expect(page.locator('#v46ConfirmSheet')).to_be_disabled()
            visit(Role.SENIOR_TECHNICIAN,second)
            for name,action in [('Keep','confirm_fact'),('Correct','adopt'),('Retest','confirm_fact'),('Problem','confirm_fact')]:
                row(name).locator('[data-v5="approve"]').click()
                f=page.locator('#v5ActiveForm')
                f.locator('[name="action"]').select_option(action)
                f.locator('[name="reason"]').fill('合成样例必要复核')
                f.get_by_role('button',name='保存草稿决定',exact=True).click()
                expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('必要复核已保存',timeout=20000)
            with store.connect() as conn:
                assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions ORDER BY id')]==before
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled()
            page.wait_for_function('!window.v5Review?.busy')
            page.screenshot(path=str(out/'02-ready.png'),full_page=True)
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            with store.connect() as conn:
                assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]==8
                assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
            report['cases']['A14-A19']='PASS mixed worksheet, original facts unchanged before commit, scoped issues and advanced draft reviews via UI'
            page.locator('#v4LedgerSourceChoice').select_option('')
            expect(page.locator('#v4LedgerInlineEditor')).to_be_empty()
            expect(page.locator('#v4LedgerSourceChoice')).to_have_value('')
            expect(page.locator('#ledgerDataTable')).to_contain_text('99.500%',timeout=20000)
            # Unsaved edits cannot silently disappear when switching sheet.
            visit(Role.PROJECT_LEAD)
            page.get_by_role('button',name='检测台账',exact=True).click()
            expect(page.locator('#v5OutputControls')).to_be_visible(timeout=15000)
            page.locator('#v5OutputControls summary').click()
            f=page.locator('#v5ExportForm')
            f.locator('[name="scope"]').select_option('filtered')
            for key,value in {'batch':'B1','presentation':'10mg','study':'long_term','study_id':'S1','condition':'5C','item':'Percent','method':'UV','method_version':'not_reported'}.items():
                if key in {'study','sample_type','item','method_version'}:
                    f.locator(f'[name="{key}"]').select_option(value)
                else:
                    f.locator(f'[name="{key}"]').fill(value)
            f.locator('[name="include_png"]').check()
            f.get_by_role('button',name='生成并核验输出').click()
            expect(page.locator('#v5ExportStatus')).to_contain_text('输出已核验',timeout=20000)
            for control,name in [('#v5DownloadExcel','results.xlsx'),('#v5DownloadPng','trend.png')]:
                with page.expect_download() as download:
                    page.locator(control).click()
                download.value.save_as(out/name)
            book=load_workbook(out/'results.xlsx')
            assert book['Records'].max_row==2 and book['Records']['M2'].value=='99.500%'
            book.close()
            with Image.open(out/'trend.png') as image:
                image.verify()
            page.screenshot(path=str(out/'03-downloads.png'),full_page=True)
            report['cases']['A01/A22']='PASS UI interpreted percent, same-snapshot Excel/PNG generated and downloaded through controls'
            # Zero-candidate and hidden sheets must be reviewable, without fake facts.
            book=Workbook()
            s=book.active
            s.title='Unrecognized'
            s.append(['Label x','Period x','Reading x','Unused note'])
            s.append(['Extra assay','0M',.993,'Keep visible'])
            s.append(['Extra assay','3M',.992])
            s['C2'].number_format=s['C3'].number_format='0.0%'
            hidden=book.create_sheet('Hidden notes')
            hidden['A1']='Synthetic auxiliary note'
            hidden.sheet_state='hidden'
            book.save(out/'regions.xlsx')
            visit(Role.TECHNICIAN)
            region_upload=upload('regions.xlsx')
            expect(page.locator('#ledgerDataTable')).to_contain_text('目前没有候选')
            expect(page.get_by_role('button',name='Hidden notes（原件隐藏）',exact=False)).to_be_visible()
            with page.expect_download() as original:
                click_tool('下载冻结原件')
            original.value.save_as(out/'downloaded-original.xlsx')
            assert (out/'downloaded-original.xlsx').read_bytes()==(out/'regions.xlsx').read_bytes()
            click_tool('指定区域和表头')
            f=page.locator('#v5ActiveForm')
            for key,value in {'range_ref':'A1:D3','header_row':'1','column_test_item':'A','column_time_point':'B','column_result':'C',
                              'method':'UV','unit':'%','sample_type':'DP','batch':'BX','presentation':'20mg','study':'accelerated',
                              'study_id':'S2','condition':'25C','reason':'Synthetic explicit header and identity'}.items():
                if key in {'study','sample_type'}:
                    f.locator(f'[name="{key}"]').select_option(value)
                else:
                    f.locator(f'[name="{key}"]').fill(value)
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            expect(page.locator('#v5ScopePreview')).to_contain_text('D1, D2',timeout=20000)
            # A1:D3 is a 12-cell rectangle, including the blank D3 (no fact is created for it).
            expect(page.locator('#v5Raw .v5-raw-highlight')).to_have_count(12)
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('已重解析为草稿',timeout=20000)
            expect(page.locator('#ledgerDataTable')).to_contain_text('99.3%')
            expect(page.locator('#v46ConfirmSheet')).to_be_disabled()
            # Dirty forms prevent switching sheet and do not save implicitly.
            first_cell().locator('[data-v5="edit"]').first.click()
            page.locator('#v5ActiveForm [name="batch"]').fill('UNSAVED')
            page.get_by_role('button',name='Hidden notes（原件隐藏）',exact=False).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('未保存修改')
            page.get_by_role('button',name='取消编辑',exact=True).click()
            # A concurrent UI edit invalidates the entire batch preview.
            for checkbox in page.locator('#ledgerDataTable [data-select]').all():
                checkbox.check()
            click_tool('批量身份预览')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="method_version"]').fill('R2')
            f.locator('[name="reason"]').fill('Synthetic batch identity preview')
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            expect(page.locator('#v5ScopePreview')).to_contain_text('0.993',timeout=20000)
            other=browser.new_page()
            other.goto(f'http://127.0.0.1:{ports[Role.TECHNICIAN]}/?mode=real&project=V5-UI&view=ledger&upload={region_upload}',wait_until='networkidle')
            first_cell(other).locator('[data-v5="edit"]').first.click()
            other.locator('#v5ActiveForm [name="batch"]').fill('BX2')
            other.locator('#v5ActiveForm [name="reason"]').fill('Synthetic concurrent single row correction')
            other.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(other.locator('#v4LedgerReviewMessage')).to_contain_text('修改已保存',timeout=20000)
            other.close()
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('已变化',timeout=20000)
            with store.connect() as conn:
                data=[json.loads(r[0]) for r in conn.execute('SELECT corrections_json FROM workflow_candidates WHERE upload_id=?',(region_upload,))]
                assert all(d.get('method_version')!='R2' for d in data)
            page.get_by_role('button',name='取消编辑',exact=True).click()
            click_tool('刷新核对')
            expect(page.locator('#ledgerDataTable')).to_contain_text('BX2',timeout=20000)
            click_tool('批量身份预览')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="method_version"]').fill('R2')
            f.locator('[name="reason"]').fill('Synthetic current scope batch identity')
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('原子保存',timeout=20000)
            while page.locator('#v51Unclassified [data-v5="classify"]').count():
                open_regions()
                page.locator('#v51Unclassified [data-v5="classify"]').first.click()
                page.locator('#v5ActiveForm [name="role"]').select_option('note')
                page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic explicit note classification')
                page.get_by_role('button',name='保存草稿决定',exact=True).click()
                expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('区域用途已保存',timeout=20000)
                expect(page.locator('#v46Busy')).to_be_hidden(timeout=20000)
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            page.get_by_role('button',name='Hidden notes（原件隐藏）',exact=False).click()
            expect(page.locator('#ledgerDataTable')).to_contain_text('目前没有候选')
            open_regions()
            page.locator('#v51Unclassified [data-v5="classify"]').click()
            page.locator('#v5ActiveForm [name="role"]').select_option('note')
            page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic hidden-sheet note only')
            page.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled(timeout=20000)
            with store.connect() as conn:
                count_before=conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]
            page.locator('#v46ConfirmSheet').click()
            page.locator('#v5ActiveForm [name="checked"]').check()
            page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic hidden sheet contains only notes')
            page.get_by_role('button',name='确认辅助来源用途',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('未生成检测事实',timeout=20000)
            with store.connect() as conn:
                assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]==count_before
            page.wait_for_function('!window.v5Review?.busy')
            page.screenshot(path=str(out/'04-zero-hidden-completed.png'),full_page=True)
            report['cases']['A06/A11/A12/A18/A20']='PASS zero-candidate/hidden source, explicit region reparse, unmapped cells block, dirty navigation guard, concurrent UI edit rejects entire batch, zero-fact sheet completion'
            # Bind a merged original cell to two explicitly entered logical results.
            source(out/'merged.xlsx',[('SplitAssay',99)])
            book=load_workbook(out/'merged.xlsx')
            book.active.merge_cells('K2:L2')
            book.save(out/'merged.xlsx')
            merged=upload('merged.xlsx')
            def split():
                page.locator('[data-v5="nextIssue"]').click()
                cell=page.locator('#ledgerDataTable [data-candidate]:has([data-v5="issue"])').first
                if not cell.count():
                    cell=page.locator('#ledgerDataTable [data-candidate]').first
                for selector in ['details.v53-cell-details:not([open]) > summary','details.v51-cell-issues:not([open]) > summary']:
                    if cell.locator(selector).count():cell.locator(selector).first.click()
                cell.locator('[data-v5="issue"]').first.click()
                page.locator('#v5OpenProjection').click()
                f=page.locator('#v5ActiveForm')
                f.locator('#v5ProjectionRows').wait_for()
                f.locator('[name="hazard"]').select_option(index=0)
                f.locator('.v5-projection').first.locator('details > summary').click()
                for key,value in {'test_item':'SplitAssay','time_point':'0M','result':'99','unit':'%','method':'UV'}.items():
                    f.locator('.v5-projection').first.locator(f'[name="{key}"]').fill(value)
                page.locator('#v5AddProjection').click()
                f.locator('.v5-projection').nth(1).locator('details > summary').click()
                for key,value in {'test_item':'SplitAssay','time_point':'3M','result':'98','unit':'%','method':'UV'}.items():
                    f.locator('.v5-projection').nth(1).locator(f'[name="{key}"]').fill(value)
                f.locator('[name="reason"]').fill('Synthetic original cell has two explicitly confirmed logical results')
                f.get_by_role('button',name='保存草稿决定',exact=True).click()
                expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('明确归属已保存',timeout=20000)
                expect(page.locator('#v46Busy')).to_be_hidden(timeout=20000)
            split()
            first_cell().locator('[data-v5="restore"]').click()
            page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic attribution requires another review')
            page.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('已恢复草稿',timeout=20000)
            expect(page.locator('#v46Busy')).to_be_hidden(timeout=20000)
            split()
            with store.connect() as conn:
                assert conn.execute("SELECT count(*) FROM workflow_candidates WHERE upload_id=? AND lifecycle='draft'",(merged,)).fetchone()[0]==2
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled()
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            report['cases']['A05/A13']='PASS merged original -> explicit two-result split -> restore parent -> resplit without duplicate active children -> worksheet commit'
            # File-local mapping, standalone project rule, explicit old draft upgrade, retirement.
            source(out/'scope-a.xlsx',[('Unmapped',99)])
            source(out/'scope-b.xlsx',[('Unmapped',98)])
            source(out/'scope-c.xlsx',[('Unmapped',97)])
            source(out/'scope-d.xlsx',[('Unmapped',96)])
            scoped_a=upload('scope-a.xlsx')
            click_tool('建立独立检项')
            f=page.locator('#v5ActiveForm')
            for key,value in {'label':'Target assay','method':'UV','unit':'%','reason':'Synthetic independently named assay'}.items():
                f.locator(f'[name="{key}"]').fill(value)
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('独立检项已创建',timeout=20000)
            page.locator('#ledgerDataTable [data-select]').check()
            click_tool('本文件检项映射')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="item_id"]').select_option(label='Target assay · UV · %')
            f.locator('[name="reason"]').fill('Synthetic current file mapping only')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本文件映射已保存',timeout=20000)
            scoped_b=upload('scope-b.xlsx')
            with store.connect() as conn:
                assert json.loads(conn.execute('SELECT corrections_json FROM workflow_candidates WHERE upload_id=?',(scoped_b,)).fetchone()[0])=={}
                assert conn.execute("SELECT count(*) FROM workflow_events WHERE action='save_project_rule'").fetchone()[0]==0
            visit(Role.TECHNICIAN,scoped_a)
            page.locator('#ledgerDataTable [data-select]').check()
            click_tool('另存为项目规则')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="item_id"]').select_option(label='Target assay · UV · %')
            f.locator('[name="reason"]').fill('Synthetic separate explicit project rule')
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('项目规则已单独保存',timeout=20000)
            visit(Role.TECHNICIAN,scoped_b)
            click_tool('预览规则升级')
            page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic explicit existing draft upgrade')
            page.get_by_role('button',name='预览精确范围',exact=True).click()
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('草稿已按预览升级解释',timeout=20000)
            expect(row('Target assay')).to_be_visible()
            scoped_c=upload('scope-c.xlsx')
            expect(row('Target assay')).to_be_visible()
            click_tool('项目规则管理')
            page.locator('#v5ActiveForm [name="rule"]').check()
            page.locator('#v5ActiveForm [name="reason"]').fill('Synthetic explicit rule retirement')
            page.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('已停用',timeout=20000)
            scoped_d=upload('scope-d.xlsx')
            with store.connect() as conn:
                assert json.loads(conn.execute('SELECT corrections_json FROM workflow_candidates WHERE upload_id=?',(scoped_d,)).fetchone()[0])=={}
                assert json.loads(conn.execute('SELECT corrections_json FROM workflow_candidates WHERE upload_id=?',(scoped_c,)).fetchone()[0])['test_item'].startswith('item_')
            report['cases']['A08/A09/A21']='PASS generated item ID, current-file-only mapping, explicit project rule preview/save, explicit frozen draft upgrade, rule retirement and later-file isolation'
            # Group issue review must retain each original value.
            source(out/'group.xlsx',[('GroupA',99),('GroupB',98)])
            book=load_workbook(out/'group.xlsx')
            book.active['K2'].comment=Comment('Synthetic source note requires review','synthetic')
            book.active['K3'].comment=Comment('Synthetic source note requires review','synthetic')
            book.save(out/'group.xlsx')
            grouped=upload('group.xlsx')
            for checkbox in page.locator('#ledgerDataTable [data-select]').all():
                checkbox.check()
            # Same comment text in two physical cells remains two separate groups.
            click_tool('问题组范围预览')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="operation"]').select_option('keep_body')
            f.locator('[name="reason"]').fill('Synthetic selected physical sources keep each own result')
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            expect(page.locator('#v5ScopePreview tr')).to_have_count(3,timeout=20000)
            expect(page.locator('#v5ScopePreview')).to_contain_text('保留当前正文及数值')
            expect(page.locator('#v5ScopePreview')).to_contain_text('2 个关联结果',timeout=20000)
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本次已处理 2 处来源',timeout=20000)
            with store.connect() as conn:
                rows=conn.execute('SELECT raw_json,corrections_json FROM workflow_candidates WHERE upload_id=? ORDER BY rowid',(grouped,)).fetchall()
                assert [json.loads(r[0])['fields']['result'] for r in rows]==['99','98']
                assert all('result' not in json.loads(r[1]) for r in rows)
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled()
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            report['cases']['A04/A12/A21-group']='PASS exact issue-member preview and atomic apply preserve 99 and 98 separately'
            source(out/'history.xlsx',[('OldHistory',91),('OtherHistory',92)])
            history_upload=upload('history.xlsx')
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            with store.connect() as conn:
                history_rows=[tuple(r) for r in conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions WHERE candidate_id IN (SELECT id FROM workflow_candidates WHERE upload_id=?) ORDER BY id',(history_upload,))]
            visit(Role.SENIOR_TECHNICIAN,history_upload)
            click_tool('历史身份关联')
            f=page.locator('#v5ActiveForm')
            f.locator('label').filter(has_text='OldHistory').locator('[type="checkbox"]').check()
            f.locator('label').filter(has_text='OtherHistory').locator('[type="checkbox"]').check()
            f.locator('[name="item"]').select_option(label='Target assay · UV · %')
            f.locator('[name="reason"]').fill('Synthetic human confirms same analytical identity and keeps 91')
            f.get_by_role('button',name='预览精确范围',exact=True).click()
            expect(page.locator('#v5ScopePreview')).to_contain_text('存在碰撞',timeout=20000)
            chosen=page.locator('#v5Adoption0 option').filter(has_text=re.compile(r'^91\s')).get_attribute('value')
            page.locator('#v5Adoption0').select_option(chosen)
            page.locator('#v5ApplyScope').click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('历史逻辑关联及采用选择已保存',timeout=20000)
            with store.connect() as conn:
                assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions WHERE candidate_id IN (SELECT id FROM workflow_candidates WHERE upload_id=?) ORDER BY id',(history_upload,))]==history_rows
            click_tool('修改检项展示名')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="item_id"]').select_option(label='Target assay · UV · %')
            f.locator('[name="label"]').fill('Target assay revised')
            f.locator('[name="reason"]').fill('Synthetic display spelling change only')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('展示名称已保存',timeout=20000)
            page.locator('#v4LedgerSourceChoice').select_option('')
            expect(page.locator('#ledgerDataTable')).to_contain_text('Target assay revised',timeout=20000)
            expect(page.locator('#ledgerDataTable tr').filter(has_text='Target assay revised')).to_contain_text('91%')
            page.screenshot(path=str(out/'05-logical-history.png'),full_page=True)
            report['cases']['A03/A09-history']='PASS UI exact history scope, collision preview, explicit adoption, display rename, immediate formal refresh; immutable physical versions unchanged'
            page.locator('#v45Batch').select_option('BX2')
            expect(page.locator('#ledgerDataTable')).to_contain_text('99.300%',timeout=20000)
            expect(page.locator('#ledgerDataTable')).to_contain_text('R2')
            expect(page.locator('#ledgerDataTable')).not_to_contain_text('Target assay revised')
            page.locator('#v45Batch').select_option('BX')
            expect(page.locator('#ledgerDataTable')).to_contain_text('99.200%',timeout=20000)
            report['cases']['A20-batch/A24-version']='PASS formal batch switches preserve result scope and show explicit method version R2'
            # Manual extraction has a real source binding, and days remain days in output.
            book=Workbook()
            book.active.title='Manual source'
            book.active['A1']='Synthetic manually read source note'
            book.active['C13']='Recorded 99.7'
            book.save(out/'manual.xlsx')
            visit(Role.TECHNICIAN)
            manual_upload=upload('manual.xlsx')
            click_tool('绑定原格补录')
            f=page.locator('#v5ActiveForm')
            f.locator('[name="coordinate"]').fill('C13')
            f.locator('.v5-projection').first.locator('details > summary').click()
            for key,value in {'test_item':'Manual assay','time_point':'7D','result':'99.7','unit':'%','method':'UV',
                              'batch':'BM','presentation':'30mg','study_id':'SM','condition':'40C','sample_type':'DP','study':'stress'}.items():
                n=f.locator('.v5-projection').first.locator(f'[name="{key}"]')
                if key in {'sample_type','study'}:
                    n.select_option(value)
                else:
                    n.fill(value)
            f.locator('[name="reason"]').fill('Synthetic explicit extraction of bound cell C13')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('明确归属已保存',timeout=20000)
            expect(page.locator('#v46Busy')).to_be_hidden(timeout=20000)
            open_regions()
            page.locator('#v51Unclassified [data-v5="classify"]').click()
            f=page.locator('#v5ActiveForm')
            f.locator('[name="role"]').select_option('note')
            f.locator('[name="reason"]').fill('Synthetic A1 note classified separately')
            f.get_by_role('button',name='保存草稿决定',exact=True).click()
            expect(page.locator('#v46ConfirmSheet')).to_be_enabled(timeout=20000)
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            with store.connect() as conn:
                record=json.loads(conn.execute('SELECT record_json FROM workflow_versions WHERE candidate_id IN (SELECT id FROM workflow_candidates WHERE upload_id=?)',(manual_upload,)).fetchone()[0])
                assert record['key']['day']==7 and record['key']['month'] is None
            visit(Role.PROJECT_LEAD)
            page.get_by_role('button',name='检测台账',exact=True).click()
            page.locator('#v5OutputControls summary').click()
            f=page.locator('#v5ExportForm')
            f.locator('[name="scope"]').select_option('filtered')
            for key,value in {'batch':'BM','presentation':'30mg','study':'stress','study_id':'SM','condition':'40C',
                              'item':'Manual assay','method':'UV','method_version':'not_reported','day':'7'}.items():
                n=f.locator(f'[name="{key}"]')
                if key in {'study','item','method_version'}:
                    n.select_option(value)
                else:
                    n.fill(value)
            f.locator('[name="include_png"]').check()
            f.get_by_role('button',name='生成并核验输出').click()
            expect(page.locator('#v5ExportStatus')).to_contain_text('输出已核验',timeout=20000)
            with page.expect_download() as dl:
                page.locator('#v5DownloadExcel').click()
            dl.value.save_as(out/'days.xlsx')
            with page.expect_download() as dl:
                page.locator('#v5DownloadPng').click()
            dl.value.save_as(out/'days.png')
            book=load_workbook(out/'days.xlsx')
            assert book['Records'].max_row==2 and book['Records']['I2'].value=='7D' and book['Records']['M2'].value=='99.7%'
            book.close()
            with Image.open(out/'days.png') as image:
                image.verify()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out/'06-mobile-days-download.png'),full_page=True)
            report['cases']['A05-manual/A22/A24-days']='PASS source-bound manual extraction, separate note coverage, worksheet commit, day=7 Excel/PNG downloads and 390px layout'
            page.set_viewport_size({'width':1440,'height':1100})
            visit(Role.TECHNICIAN)
            from v52_ui_cases import run as v52_cases
            v52_cases(page,out,report,store,upload)
            source(out/'other-project.xlsx',[('Other project assay',77)])
            book=load_workbook(out/'other-project.xlsx')
            book.active['A2']='V5-ALT'
            book.save(out/'other-project.xlsx')
            page.get_by_role('button',name='数据接收',exact=True).click()
            page.locator('#v3FileInput').set_input_files(str(out/'other-project.xlsx'))
            page.locator('#v44ConfirmFile, #v5DirectWorksheet').first.wait_for(state='visible', timeout=20000)
            if page.locator('#v5DirectWorksheet').is_visible():
                page.locator('#v5DirectWorksheet').click()
            else:
                page.get_by_role('button',name='确认创建项目并预览',exact=True).click()
                page.locator('#v44ConfirmFile').check()
                page.locator('#v5Receive').click()
            expect(page.locator('#v4LedgerInlineEditor .v52-heading')).to_contain_text('other-project.xlsx',timeout=20000)
            expect(page.locator('#workspaceName')).to_contain_text('V5-ALT')
            expect(page.locator('#ledgerDataTable')).not_to_contain_text('Percent')
            confirm_sheet()
            expect(page.locator('#v4LedgerReviewMessage')).to_contain_text('本表已正式提交',timeout=20000)
            page.locator('.v3-switch').click()
            page.locator('[data-open-project="V5-UI"]').first.click()
            page.get_by_role('button',name='检测台账',exact=True).click()
            expect(page.locator('#v4LedgerSourceChoice')).to_be_visible(timeout=20000)
            page.locator('#v4LedgerSourceChoice').select_option(scoped_d)
            expect(page.locator('#v4LedgerInlineEditor .v52-heading')).to_contain_text('scope-d.xlsx',timeout=20000)
            first_cell().locator('[data-v5="edit"]').first.click()
            page.locator('#v5ActiveForm [name="batch"]').fill('UNSAVED-CROSS-PROJECT')
            page.locator('.v3-switch').click()
            page.locator('[data-open-project="V5-ALT"]').first.click()
            expect(page.locator('#v5ProjectGuard')).to_contain_text('未保存修改')
            expect(page.locator('#workspaceName')).to_contain_text('V5-UI')
            page.get_by_role('button',name='关闭项目中心',exact=True).click()
            page.get_by_role('button',name='取消编辑',exact=True).click()
            page.locator('.v3-switch').click()
            page.locator('[data-open-project="V5-ALT"]').first.click()
            page.get_by_role('button',name='检测台账',exact=True).click()
            expect(page.locator('#ledgerDataTable')).to_contain_text('Other project assay',timeout=20000)
            expect(page.locator('#ledgerDataTable')).not_to_contain_text('Percent')
            page.reload(wait_until='networkidle')
            expect(page.locator('#workspaceName')).to_contain_text('V5-ALT',timeout=20000)
            report['cases']['A20-project']='PASS UI project creation/switching, dirty edit blocks cross-project navigation, explicit cancellation and reload preserve project scope'
            assert not report['console_errors'],report['console_errors']
            browser.close()
        report['result']='PASS synthetic V5 UI scenarios; real annotated holdouts remain pending'
    except BaseException as exc:
        report['result']='FAILED: '+str(exc)
        if 'page' in locals() and not page.is_closed():
            page.screenshot(path=str(out/'failure.png'),full_page=True)
            (out/'failure.html').write_text(page.content(),encoding='utf-8')
        raise
    finally:
        if 'pw' in locals():
            pw.stop()
        for server,thread,listener in servers:
            server.should_exit=True
            thread.join(timeout=10)
            listener.close()
        report['ports']={k.value:v for k,v in ports.items()}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(out/'report.json'),'result':report.get('result')},ensure_ascii=False))


if __name__=='__main__':
    main()
