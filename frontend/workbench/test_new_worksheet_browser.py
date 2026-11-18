"""Browser acceptance for creating a study without source files."""
import os
import json
import re
import hashlib
import socket
import threading
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from docx import Document
from openpyxl import Workbook, load_workbook
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def main():
    with TemporaryDirectory() as temp, socket.socket() as listener:
        actor = actor_for_role('local', '本地操作员', Role.ADMIN)
        app = create_app(Store(Path(temp)), [], actor, workbench=ROOT / 'frontend/workbench/dist')
        listener.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                cache = Path(os.environ['LOCALAPPDATA']) / 'ms-playwright'
                executable = next(cache.glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
                browser = pw.chromium.launch(executable_path=str(executable))
                context = browser.new_context(viewport={'width': 1366, 'height': 768},
                                              permissions=['clipboard-read', 'clipboard-write'])
                page = context.new_page()
                page.set_default_timeout(7000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_role('link', name='新建研究表').click()
                project_form = page.locator('details').filter(has=page.get_by_text('新建项目', exact=True)).filter(has=page.locator('input[name=project]'))
                if not project_form.evaluate('(element) => element.open'):
                    project_form.locator('summary').click()
                project_form.get_by_label('项目编号').fill('EVAL-NEW')
                page.get_by_label('项目名称').fill('验收项目')
                page.get_by_role('button', name='创建项目').click()
                page.get_by_text('添加项目检项', exact=True).click()
                page.locator('[name=items]').fill('纯度\tSEC\t%\t数值\n外观\t目视\t\t文字')
                page.get_by_role('button', name='添加检项').click()
                expect(page.get_by_text('纯度 · SEC', exact=True)).to_be_visible()
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                page.get_by_label('工作表名称', exact=True).fill('待补全示例')
                dialogs = []
                def keep_plan(dialog):
                    dialogs.append(dialog.message)
                    dialog.dismiss()
                page.once('dialog', keep_plan)
                page.get_by_role('link', name='工作台', exact=True).click()
                assert dialogs and '未保存' in dialogs[0]
                expect(page.get_by_label('工作表名称', exact=True)).to_have_value('待补全示例')
                page.get_by_role('button', name='保存草稿').click()
                expect(page.get_by_role('status')).to_contain_text('待补全计划已保存')
                page.goto(url + '/workbench/#/projects/EVAL-NEW')
                page.get_by_role('link', name='待补全示例').click()
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                expect(page.get_by_label('工作表名称', exact=True)).to_have_value('待补全示例')
                expect(page.get_by_label('批号', exact=True)).to_have_value('')
                expect(page.get_by_text('纯度 · SEC', exact=True)).to_be_visible()
                fields = {'工作表名称': '长期研究', '批号': 'B01', '规格': '1mg', '材料阶段': '裸抗',
                          '方案编号或内部研究名称': 'P1', '条件': '5℃', '开始日期': '2026-01-31',
                          '时间点': '0M, 3M'}
                for label, value in fields.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_role('radio', name='长期', exact=True).check()
                page.get_by_role('button', name='5℃±3℃', exact=True).click()
                page.get_by_role('checkbox', name='避光', exact=True).check()
                expect(page.get_by_label('条件', exact=True)).to_have_value('5℃±3℃ · 避光')
                page.get_by_role('button', name='常熟工厂', exact=True).click()
                expect(page.get_by_label('地点', exact=True)).to_have_value('常熟工厂')
                page.get_by_label('条件', exact=True).fill('5℃')
                page.get_by_role('button', name='创建研究表').click()
                expect(page.get_by_role('heading', name='长期研究')).to_be_visible()
                new_sheet = context.request.get(url + '/api/v1/worksheets/' + page.url.rsplit('/', 1)[-1]).json()
                assert new_sheet['location'] == '常熟工厂'
                drafts = context.request.get(url + '/api/v1/projects/EVAL-NEW/worksheet-drafts')
                assert drafts.ok and drafts.json() == []
                expect(page.get_by_role('columnheader', name='3M 2026-04-30')).to_be_visible()
                page.get_by_role('button', name='编辑结果').click()
                canvas = page.locator('canvas[id^="univer-sheet-main-canvas"]')
                expect(canvas).to_be_visible()
                other = context.new_page()
                other.goto(page.url)
                other.get_by_role('button', name='编辑结果').click()
                expect(other.get_by_role('alert')).to_contain_text('正在编辑此工作表')
                other.close()
                page.bring_to_front()
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                assert box
                page.mouse.click(box['x'] + 510, box['y'] + 117)
                multiline_result = '澄清，第一行说明。\n第二行仍在同一结果单元格。'
                clipboard_values = '98.20%\t<0.1\nNA*\t"' + multiline_result + '"'
                page.evaluate('value => navigator.clipboard.writeText(value)', clipboard_values)
                page.keyboard.press('Control+v')
                expect(page.get_by_role('status')).to_have_text('有未保存的改动')
                page.once('dialog', lambda dialog: dialog.accept())
                page.reload()
                page.get_by_role('button', name='恢复编辑').click()
                expect(page.get_by_role('status')).to_have_text('已恢复本标签页的未保存改动。')
                page.get_by_role('button', name='保存结果').click()
                expect(page.get_by_role('status')).to_have_text('已保存。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_role('cell', name='98.20%')).to_be_visible()
                result_matrix = page.get_by_role('region', name='研究结果矩阵')
                rendered_values = [[value.strip() for value in row.locator('td').all_text_contents()[-2:]]
                                   for row in result_matrix.locator('tbody tr').all()]
                assert ['98.20%', '<0.1%'] in rendered_values, rendered_values
                assert ['NA*', multiline_result] in rendered_values, rendered_values
                multiline_cell = result_matrix.get_by_role('cell', name='澄清，第一行说明。', exact=False)
                expect(multiline_cell).to_be_visible()
                assert multiline_cell.evaluate("cell => getComputedStyle(cell).whiteSpace === 'pre-wrap'")

                page.get_by_role('button', name='编辑结果').click()
                expect(canvas).to_be_visible()
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                assert box
                page.mouse.click(box['x'] + 510, box['y'] + 117)
                page.evaluate("navigator.clipboard.writeText('98.30%')")
                page.keyboard.press('Control+v')
                expect(page.get_by_role('status')).to_have_text('有未保存的改动')
                page.get_by_role('button', name='保存结果').click()
                page.get_by_placeholder('说明本次对已有结果的更正').fill('核对原始结果后更正')
                page.get_by_role('button', name='保存结果').click()
                expect(page.get_by_role('status')).to_have_text('已保存。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_role('cell', name='98.30%')).to_be_visible()
                page.get_by_role('button', name='编辑结果').click()
                expect(canvas).to_be_visible()
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                assert box
                page.mouse.click(box['x'] + 510, box['y'] + 117)
                page.get_by_role('button', name='新增复测').click()
                page.get_by_label('复测结果').fill('98.40%')
                page.get_by_label('复测说明').fill('独立复测')
                page.get_by_role('button', name='保存复测').click()
                expect(page.get_by_role('status')).to_contain_text('已保存独立复测')
                page.get_by_role('button', name='增加计划时间点').click()
                page.get_by_label('新增时间点').fill('6M')
                page.locator('.extra-form .n-base-selection').click()
                page.get_by_text('纯度', exact=True).click()
                page.get_by_label('计划变更依据').fill('延长考察')
                page.get_by_role('button', name='保存计划').click()
                expect(page.get_by_role('status')).to_have_text('已保存新计划版本。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_role('cell', name='98.30% 最新记录 · 98.40%')).to_be_visible()
                expect(page.get_by_role('columnheader', name='6M 2026-07-31')).to_be_visible()
                expect(page.get_by_role('cell', name='不要求')).to_be_visible()
                protocol = Document()
                protocol.add_paragraph('长期稳定性考察项目')
                protocol_table = protocol.add_table(rows=0, cols=3)
                for values in (('考察项目', '0月', '12月'), ('纯度', '√', '√')):
                    for cell, value in zip(protocol_table.add_row().cells, values, strict=True):
                        cell.text = value
                protocol_file = BytesIO()
                protocol.save(protocol_file)
                uploaded = context.request.post(url + '/api/v1/intake/protocol', multipart={
                    'file': {'name': '测试方案.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                             'buffer': protocol_file.getvalue()},
                })
                assert uploaded.ok, uploaded.text()
                page.get_by_role('button', name='编辑结果').click()
                expect(canvas).to_be_visible()
                page.get_by_role('button', name='后补研究方案').click()
                page.locator('.protocol-form .n-base-selection').first.click()
                page.get_by_text('测试方案.docx', exact=True).click()
                page.locator('.protocol-form .n-base-selection').last.click()
                page.get_by_text('长期稳定性考察项目', exact=True).click()
                expect(page.get_by_text('方案新增时间点 12M', exact=False)).to_be_visible()
                page.get_by_label('条件和检项核对依据').fill('核对原方案后补充出处')
                page.get_by_role('button', name='确认关联方案').click()
                expect(page.get_by_role('status')).to_have_text('已关联研究方案。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_role('link', name='下载原方案', exact=False)).to_be_visible()
                expect(page.get_by_role('cell', name='98.30% 最新记录 · 98.40%')).to_be_visible()
                page.get_by_role('button', name='生成确认表 Excel').click()
                with page.expect_download() as event:
                    page.get_by_role('link', name='下载确认表').click()
                assert event.value.path().read_bytes().startswith(b'PK')
                page.get_by_role('link', name='复制研究表结构').click()
                expect(page.get_by_text('来源研究表', exact=False)).to_be_visible()
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                page.get_by_label('工作表名称', exact=True).fill('新批次结构副本')
                page.get_by_label('批号', exact=True).fill('B-COPY')
                page.get_by_label('开始日期', exact=True).fill('2026-02-28')
                page.get_by_label('材料阶段', exact=True).fill('裸抗')
                page.get_by_role('button', name='创建研究表').click()
                expect(page.get_by_role('heading', name='新批次结构副本')).to_be_visible()
                expect(page.get_by_role('link', name='查看结构来源', exact=False)).to_be_visible()
                expect(page.get_by_role('columnheader', name='6M 2026-08-28')).to_be_visible()
                expect(page.get_by_role('cell', name='不要求')).to_be_visible()
                sheet_id = page.url.rsplit('/', 1)[-1]
                assert context.request.get(url + '/api/v1/worksheets/' + sheet_id).json()['cells'] == []
                def receive_candidates():
                    receive = page.get_by_role('button', name='条候选供核对', exact=False)
                    expect(receive).to_be_visible()
                    receive.click()
                    expect(receive).to_have_count(0)

                book = Workbook()
                book.active.title = 'Results'
                book.active.append(['Test item', 'Time point', 'Result', 'Unit', 'Method'])
                book.active.append(['纯度', '0M', 97.2, '%', 'SEC'])
                sample = context.request.post(url + '/api/v1/projects/EVAL-NEW/samples', data={
                    'batch': 'B02', 'sample_type': 'DS', 'presentation': '1mg', 'material_stage': '裸抗', 'purpose': '研究',
                })
                assert sample.ok, sample.text()
                b02_intake = url + '/workbench/#/import?sample=' + sample.json()['id']
                source = Path(temp) / 'EVAL-NEW.xlsx'
                book.save(source)
                book.close()
                page.goto(b02_intake)
                page.reload()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(source)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_text('补充原件未注明的身份').click()
                for label, value in {'批号': 'B02', '规格': '1mg', '方案编号': 'P1', '条件': '5℃'}.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_label('考察类型').select_option('long_term')
                page.locator('.import-panel .n-base-selection').click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-NEW · 验收项目').click()
                page.get_by_role('button', name='解析并核对').click()
                expect(page.get_by_role('cell', name='B02')).to_be_visible(timeout=15000)
                receive_candidates()
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                expect(page.get_by_text('此工作表已写入。', exact=True)).to_be_visible()
                result = context.request.get(url + '/api/v1/projects/EVAL-NEW/result-display?batch=B02')
                assert result.ok and result.json()['total'] == 1, result.text()
                old_cells = context.request.get(url + '/api/v1/projects/EVAL-NEW/result-cells?batch=B02')
                assert old_cells.ok, old_cells.text()
                old_report = context.request.post(url + '/api/v1/reports', data={
                    'project': 'EVAL-NEW', 'base_revision': old_cells.json()['base_revision'],
                    'filters': {'batch': 'B02'}, 'stability_document': {'version': '更正前'},
                })
                assert old_report.ok, old_report.text()
                old_report_id = old_report.json()['export_id']
                old_word_url = url + old_report.json()['word_download_url']
                old_word = context.request.get(old_word_url).body()
                revised = Path(temp) / 'EVAL-NEW-revised.xlsx'
                book = Workbook()
                book.active.title = 'Results'
                book.active.append(['Test item', 'Time point', 'Result', 'Unit', 'Method'])
                book.active.append(['纯度', '0M', 97.3, '%', 'SEC'])
                book.save(revised)
                book.close()
                page.get_by_role('button', name='返回导入列表').click()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(revised)
                page.get_by_role('button', name='解析并核对').click()
                expect(page.get_by_role('cell', name='B02')).to_be_visible()
                receive_candidates()
                page.get_by_role('button', name='处理变动').click()
                page.get_by_label('处理方式').select_option('correction')
                page.get_by_label('核对依据').fill('核对新版原始记录后更正')
                page.get_by_role('button', name='保存并核对选择').click()
                expect(page.get_by_role('status')).to_contain_text('已记录处理选择')
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                expect(page.get_by_text('此工作表已写入。', exact=True)).to_be_visible()
                page.get_by_role('link', name='打开项目数据').click()
                page.get_by_placeholder('输入批号筛选').fill('B02')
                page.get_by_role('button', name='按批号查看').click()
                expect(page.get_by_role('status')).to_contain_text('共 1 条结果')
                expect(page.get_by_role('cell', name='97.3% 留痕作废')).to_be_visible()
                controls = page.locator('.result-controls')
                controls.get_by_label('考察类型').select_option('release')
                controls.get_by_role('button', name='按批号查看').click()
                expect(page.get_by_text('当前筛选范围没有已提交结果。')).to_be_visible()
                controls.get_by_label('考察类型').select_option('long_term')
                controls.get_by_role('button', name='按批号查看').click()
                expect(page.get_by_role('status')).to_contain_text('共 1 条结果')
                assert 'study=long_term' in controls.get_by_role('link', name='查看趋势').get_attribute('href')
                assert 'study=long_term' in controls.get_by_role('link', name='生成文件').get_attribute('href')
                controls.get_by_label('考察类型').select_option('')
                controls.get_by_role('button', name='按批号查看').click()
                expect(page.get_by_role('status')).to_contain_text('共 1 条结果')
                page.locator('.result-controls').get_by_role('link', name='生成文件').click()
                expect(page.get_by_placeholder('留空包含所有批次')).to_have_value('B02')
                page.get_by_role('button', name='预览范围').click()
                expect(page.get_by_role('status')).to_contain_text('范围内 1 条结果')
                page.get_by_role('button', name='生成文件').click()
                with page.expect_download() as delivery:
                    page.get_by_role('link', name='下载 Excel').click()
                assert delivery.value.path().read_bytes().startswith(b'PK')
                page.reload()
                expect(page.get_by_role('link', name='EVAL-NEW · results.xlsx')).to_be_visible()
                page.get_by_placeholder('留空包含所有批次').fill('')
                page.locator('.delivery-form .n-select').last.click()
                page.get_by_text('检测结果 Word', exact=True).click()
                page.get_by_role('button', name='预览范围').click()
                expect(page.get_by_role('status')).to_contain_text('范围内 5 条结果')
                page.get_by_role('button', name='生成文件').click()
                with page.expect_download() as word:
                    page.locator('.delivery-form').get_by_role('link', name='下载 Word', exact=True).click()
                report = Document(BytesIO(word.value.path().read_bytes()))
                assert any('97.3' in cell.text for table in report.tables for row in table.rows for cell in row.cells)
                expect(page.get_by_role('link', name='EVAL-NEW · report.zip')).to_have_count(2)
                page.get_by_placeholder('留空包含所有批次').fill('B02')
                page.locator('.delivery-form .n-select').last.click()
                page.get_by_text('稳定性考察报告 Word', exact=True).click()
                page.get_by_label('设备信息', exact=True).fill('设备记录 A01')
                page.get_by_label('变更历史', exact=True).fill('补充设备记录')
                page.get_by_role('button', name='预览范围').click()
                expect(page.get_by_role('status')).to_contain_text('范围内 1 条结果')
                page.get_by_role('button', name='生成文件').click()
                with page.expect_download() as stability:
                    page.locator('.delivery-form').get_by_role('link', name='下载 Word', exact=True).click()
                structured = Document(BytesIO(stability.value.path().read_bytes()))
                expect(page.get_by_text('版本 草稿', exact=True)).to_be_visible()
                page.get_by_text('查看变更说明', exact=True).click()
                expect(page.locator('.delivery-changes').get_by_text('补充设备记录', exact=True)).to_be_visible()
                assert any(p.text == '设备记录 A01' for p in structured.paragraphs)
                assert any('97.3' in cell.text for table in structured.tables for row in table.rows for cell in row.cells)
                compare_panel = page.locator('.delivery-comparison')
                compare_panel.get_by_text('比较 Word 报告结果', exact=True).click()
                def select_report(label, text, *, exclude=None):
                    compare_panel.locator('label').filter(has_text=label).locator('.n-base-selection').click()
                    options = page.locator('.n-base-select-option:visible').filter(has_text=text)
                    if exclude:
                        options = options.filter(has_not_text=exclude)
                    options.last.click()

                select_report('基准报告', '草稿')
                select_report('对比报告', 'EVAL-NEW', exclude=re.compile('更正前|草稿'))
                compare_panel.get_by_role('button', name='比较结果').click()
                expect(compare_panel.get_by_role('status')).to_contain_text('纳入范围 4 条 · 移出范围 0 条 · 采用版本变化 0 条')
                differences = compare_panel.get_by_role('table', name='Word 报告结果差异')
                expect(differences.get_by_role('cell', name='未纳入', exact=True)).to_have_count(4)
                expect(differences.get_by_role('cell', name='B01', exact=True)).to_have_count(4)
                select_report('基准报告', '更正前')
                select_report('对比报告', '草稿')
                compare_panel.get_by_role('button', name='比较结果').click()
                expect(compare_panel.get_by_role('status')).to_contain_text('纳入范围 0 条 · 移出范围 0 条 · 采用版本变化 1 条')
                expect(differences.locator('tbody td').nth(5)).to_have_text('97.2')
                expect(differences.locator('tbody td').nth(6)).to_have_text('97.3')
                assert context.request.get(old_word_url).body() == old_word
                compare_panel.get_by_text('比较 Word 报告结果', exact=True).click()
                scan_bytes = b'%PDF-1.4\n'
                scan_id = hashlib.sha256(scan_bytes).hexdigest()
                archived = page.request.post(url + '/api/v1/pdf-ocr', multipart={
                    'file': {'name': 'standards.pdf', 'mimeType': 'application/pdf', 'buffer': scan_bytes},
                })
                assert archived.ok and archived.json()['id'] == scan_id
                page.goto(url + '/workbench/#/projects/EVAL-NEW')
                study_search = page.locator('.all-study-search')
                study_search.locator('summary').click()
                study_search.get_by_label('搜索研究表').fill('b-copy')
                expect(study_search.get_by_role('link', name='新批次结构副本', exact=True)).to_be_visible()
                expect(study_search.get_by_role('link', name='长期研究', exact=True)).to_have_count(0)
                study_search.get_by_label('搜索研究表').fill('missing batch')
                expect(study_search.get_by_text('未找到匹配的研究表。')).to_be_visible()
                study_search.get_by_label('搜索研究表').fill('')
                page.get_by_placeholder('输入批号筛选').fill('B-COPY')
                page.get_by_role('button', name='按批号查看').click()
                page.locator('.result-controls').get_by_role('link', name='生成确认表', exact=True).click()
                expect(page.get_by_placeholder('留空包含所有批次')).to_have_value('B-COPY')
                page.locator('.delivery-form .n-select').last.click()
                page.locator('.n-base-select-option').filter(has_text='新批次结构副本 · B-COPY').click()
                page.get_by_role('button', name='预览范围').click()
                expect(page.get_by_role('status')).to_contain_text('3 个计划时间点')
                page.locator('.confirmation-points .n-tag').filter(has_text='0M').locator('.n-tag__close').click()
                expect(page.get_by_role('status')).to_contain_text('2 个计划时间点')
                page.keyboard.press('Escape')
                page.locator('.confirmation-attachment .n-select').click()
                page.get_by_text('standards.pdf', exact=True).click()
                page.get_by_role('button', name='生成文件').click()
                with page.expect_download() as direct:
                    page.locator('.delivery-form').get_by_role('link', name='下载 Excel').click()
                confirmation = load_workbook(BytesIO(direct.value.path().read_bytes()))
                assert '6M' in [cell.value for cell in confirmation.active[7]]
                assert '0M' not in [cell.value for cell in confirmation.active[7]]
                assert confirmation.active['E9'].value is None
                assert any(scan_id in row for row in confirmation.active.values)
                confirmation.close()
                page.reload()
                with page.expect_download() as attached:
                    page.get_by_role('link', name='下载附件 standards.pdf').click()
                assert attached.value.path().read_bytes() == scan_bytes
                page.goto(url + '/workbench/#/trends?project=EVAL-NEW')
                page.locator('.trend .n-base-selection').click()
                page.get_by_text('纯度 · SEC · 5℃', exact=False).first.click()
                expect(page.get_by_role('img', name='结果趋势图')).to_be_visible()
                batch_tags = page.locator('.batch-choice .n-tag')
                expect(batch_tags).to_have_count(2)
                batch_tags.filter(has_text='B01').locator('.n-tag__close').click()
                expect(batch_tags).to_have_count(1)
                page.keyboard.press('Escape')
                page.get_by_role('button', name='生成趋势 PNG').click()
                with page.expect_download() as image:
                    page.get_by_role('link', name='下载趋势 PNG').click()
                with Image.open(image.value.path()) as png:
                    assert json.loads(png.info['Description'])['batches'] == ['B02']
                scan_dir = Path(temp) / 'pdf-ocr' / scan_id
                (scan_dir / 'pages.jsonl').write_text('{"page":1,"tables":[],"lines":[]}\n', encoding='utf-8')
                (scan_dir / 'state').write_text('cancelled', encoding='ascii')
                page.goto(url + '/workbench/#/scans')
                page.get_by_role('button', name='standards.pdf').click()
                expect(page.get_by_role('status')).to_contain_text('已取消')
                page.get_by_role('button', name='登记本页标准条款').click()
                page.get_by_role('button', name='手动添加条款').click()
                page.get_by_label('适用对象').select_option('DS')
                page.get_by_label('适用范围原文或核对说明').fill('测试用原液标准')
                page.get_by_label('检项', exact=True).fill('纯度')
                page.get_by_label('方法', exact=True).fill('SEC')
                page.get_by_label('标准原文').fill('测试条款')
                page.get_by_role('button', name='核对无误，登记条款').click()
                page.get_by_role('link', name='打开质量标准库').click()
                page.get_by_role('button', name='standards.pdf').click()
                expect(page.get_by_role('cell', name='PDF 第 1 页 · 人工录入条款 1')).to_be_visible()
                expect(page.get_by_role('cell', name='测试条款', exact=True)).to_be_visible()
                manual_doc = Document()
                manual_doc.add_paragraph('长期考察，按方案正文在 0M 和 3M 检测纯度。')
                manual_file = Path(temp) / '正文方案.docx'
                manual_doc.save(manual_file)
                page.goto(url + '/workbench/#/import')
                page.get_by_label('文件用途').select_option('protocol')
                page.locator('.import-panel .n-base-selection').first.click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-NEW · 验收项目').click()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(manual_file)
                page.get_by_role('button', name='解析方案').click()
                expect(page.get_by_text('未识别到考察矩阵。', exact=False)).to_be_visible()
                page.get_by_role('link', name='按原方案手动建表').click()
                page.locator('[aria-label="应检项目"]').click()
                page.locator('.n-base-select-option').filter(has_text='纯度 · SEC').click()
                page.keyboard.press('Escape')
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                for label, value in {**fields, '工作表名称': '正文方案手工表', '批号': 'B-MANUAL'}.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_role('radio', name='长期', exact=True).check()
                page.get_by_role('button', name='创建研究表').click()
                expect(page.get_by_role('heading', name='正文方案手工表')).to_be_visible()
                protocol_url = page.get_by_role('link', name='下载原方案', exact=True).get_attribute('href')
                assert context.request.get(url + protocol_url).body() == manual_file.read_bytes()
                page.get_by_role('button', name='生成确认表 Excel').click()
                with page.expect_download() as manual_confirmation:
                    page.get_by_role('link', name='下载确认表').click()
                workbook = load_workbook(BytesIO(manual_confirmation.value.path().read_bytes()))
                assert any(row[:5] == ('方案原件', manual_file.name, 'SHA256',
                    hashlib.sha256(manual_file.read_bytes()).hexdigest(), '对照原方案人工编制')
                    for row in workbook.active.values)
                workbook.close()
                page.goto(url + '/workbench/#/projects/EVAL-NEW')
                page.get_by_placeholder('输入批号筛选').fill('B02')
                page.get_by_role('button', name='按批号查看').click()
                page.get_by_role('link', name='按结果建研究表').click()
                expect(page.get_by_label('批号', exact=True)).to_have_value('B02')
                expect(page.get_by_label('条件', exact=True)).to_have_value('5℃')
                expect(page.get_by_label('时间点 1', exact=True)).to_have_value('0M')
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                page.get_by_label('工作表名称', exact=True).fill('从已有结果建立')
                page.get_by_label('开始日期').fill('2026-01-31')
                page.get_by_role('button', name='创建研究表').click()
                expect(page.get_by_role('heading', name='从已有结果建立')).to_be_visible()
                expect(page.get_by_role('cell', name='97.3%', exact=True)).to_be_visible()
                assert context.request.get(url + '/api/v1/projects/EVAL-NEW/result-cells?batch=B02').json()['total'] == 1
                page.goto(url + '/workbench/#/projects/EVAL-NEW')
                page.get_by_placeholder('输入批号筛选').fill('B02')
                page.get_by_role('button', name='按批号查看').click()
                page.get_by_role('link', name='打开研究表', exact=True).click()
                expect(page.get_by_role('heading', name='从已有结果建立')).to_be_visible()
                page.get_by_role('link', name='导入文件', exact=True).click()
                page.locator('.import-panel .n-base-selection').first.click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-NEW · 验收项目').click()
                page.get_by_role('button', name='EVAL-NEW-revised.xlsx', exact=True).click()
                page.get_by_role('link', name='打开研究表 从已有结果建立').click()
                expect(page.get_by_role('heading', name='从已有结果建立')).to_be_visible()
                repeat_source = Path(temp) / 'EVAL-NEW-repeat.xlsx'
                book = load_workbook(revised)
                book.properties.title = '同值新来源'
                book.save(repeat_source)
                book.close()
                with app.state.workflow.store.connect() as conn:
                    version_count = conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]
                page.goto(b02_intake)
                page.reload()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(repeat_source)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_text('补充原件未注明的身份').click()
                for label, value in {'批号': 'B02', '规格': '1mg', '方案编号': 'P1', '条件': '5℃'}.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_label('考察类型').select_option('long_term')
                page.locator('.import-panel .n-base-selection').click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-NEW · 验收项目').click()
                page.get_by_role('button', name='解析并核对').click()
                receive_candidates()
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                expect(page.get_by_text('此工作表已写入。', exact=True)).to_be_visible()
                with app.state.workflow.store.connect() as conn:
                    upload_count = conn.execute('SELECT count(*) FROM workflow_uploads').fetchone()[0]
                page.get_by_role('button', name='返回导入列表').click()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(repeat_source)
                page.get_by_role('button', name='解析并核对').click()
                expect(page.get_by_role('status')).to_have_text('该原件已归档，已打开原有导入记录。')
                expect(page.get_by_role('button', name='核对无误，写入本工作表')).to_be_disabled()
                page.get_by_role('link', name='打开研究表 从已有结果建立').click()
                expect(page.get_by_role('heading', name='从已有结果建立')).to_be_visible()
                with app.state.workflow.store.connect() as conn:
                    assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == version_count
                    assert conn.execute('SELECT count(*) FROM workflow_uploads').fetchone()[0] == upload_count
                b02_worksheet_url = page.url
                page.goto(url + '/workbench/#/deliveries?project=EVAL-NEW&batch=B02')
                page.locator('.delivery-form .n-select').last.click()
                page.get_by_text('跨批对比 Word', exact=True).click()
                for batch_code in ('B01', 'B02'):
                    page.locator('.quick-samples .n-base-selection').click()
                    page.locator('.n-base-select-option').filter(has_text=batch_code).click()
                    page.keyboard.press('Escape')
                page.get_by_text('样品分组（可选）', exact=True).click()
                page.locator('.quick-groups').get_by_label('B01', exact=False).select_option('before')
                page.locator('.quick-groups').get_by_label('B02', exact=False).select_option('after')
                page.get_by_role('button', name='预览范围').click()
                expect(page.get_by_role('status')).to_contain_text('范围内 5 条结果')
                page.get_by_text('选择检测组', exact=False).click()
                page.locator('.quick-groups').get_by_role('checkbox', name='外观', exact=False).uncheck()
                expect(page.get_by_role('status')).to_contain_text('范围内 3 条结果')
                page.get_by_role('button', name='生成文件').click()
                with page.expect_download() as cross_batch:
                    page.locator('.delivery-form').get_by_role('link', name='下载 Word', exact=True).click()
                comparison_doc = Document(BytesIO(cross_batch.value.path().read_bytes()))
                cell_text = '\n'.join(cell.text for table in comparison_doc.tables for row in table.rows for cell in row.cells)
                headings = '\n'.join(p.text for p in comparison_doc.paragraphs if p.style.name == 'Heading 2')
                assert 'B01' in headings and 'B02' in headings and '97.3' in cell_text
                assert '外观' not in cell_text
                paragraphs = [p.text for p in comparison_doc.paragraphs]
                assert any(p.startswith('变更前：B01') for p in paragraphs)
                assert any(p.startswith('变更后：B02') for p in paragraphs)
                page.goto(b02_worksheet_url)
                page.get_by_role('button', name='编辑结果').click()
                page.get_by_role('button', name='增加计划时间点').click()
                page.get_by_label('新增时间点').fill('18M')
                page.locator('.extra-form .n-base-selection').click()
                options = page.locator('.n-base-select-option:visible')
                expect(options).to_have_count(1)
                options.click()
                page.get_by_label('计划变更依据').fill('18M 计划补充验收')
                page.get_by_role('button', name='保存计划').click()
                expect(page.get_by_role('status')).to_have_text('已保存新计划版本。')
                page.get_by_role('button', name='结束编辑').click()
                expect(page.get_by_role('columnheader', name='18M 2027-07-31')).to_be_visible()
                expect(page.get_by_role('cell', name='97.3%', exact=True)).to_be_visible()
                follow_up = Path(temp) / 'EVAL-NEW-18M.xlsx'
                book = load_workbook(revised)
                book.active['B2'] = '18M'
                book.active['C2'] = 96.8
                book.save(follow_up)
                book.close()
                page.goto(b02_intake)
                page.reload()
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(follow_up)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_text('补充原件未注明的身份').click()
                for label, value in {'批号': 'B02', '规格': '1mg', '方案编号': 'P1', '条件': '5℃'}.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_label('考察类型').select_option('long_term')
                page.locator('.import-panel .n-base-selection').click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-NEW · 验收项目').click()
                page.get_by_role('button', name='解析并核对').click()
                receive_candidates()
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                page.get_by_role('link', name='打开研究表 从已有结果建立').click()
                expect(page).to_have_url(b02_worksheet_url)
                expect(page.get_by_role('cell', name='97.3%', exact=True)).to_be_visible()
                expect(page.get_by_role('cell', name='96.8%', exact=True)).to_be_visible()
                sheet_api = url + '/api/v1/worksheets/' + b02_worksheet_url.rsplit('/', 1)[-1]
                view = page.request.get(sheet_api).json()
                lease = page.request.post(sheet_api + '/lease').json()['token']
                long_note = '第一行保留中文核对说明。\n' + 'ContinuousReference' * 30 + '\n第三行保留完整。'
                event = page.request.post(sheet_api + '/events', data={
                    'token': lease, 'revision': view['revision'], 'idempotency_key': 'long-note',
                    'category': '备注', 'description': long_note,
                })
                assert event.ok, event.text()
                assert page.request.post(sheet_api + '/lease/release', data={'token': lease}).ok
                page.set_viewport_size({'width': 430, 'height': 850})
                page.reload()
                page.get_by_text('研究事件 · 1 项', exact=True).click()
                note = page.locator('.worksheet-notes p')
                assert note.text_content() == long_note
                assert note.evaluate("e => getComputedStyle(e).whiteSpace === 'pre-wrap' && e.scrollWidth <= e.clientWidth")
                assert page.evaluate('document.documentElement.scrollWidth === innerWidth')
                page.set_viewport_size({'width': 1366, 'height': 768})
                other_project = page.request.post(url + '/api/v1/projects', data={'project': 'EVAL-OTHER', 'name': '另一个项目'})
                assert other_project.ok, other_project.text()
                page.goto(url + '/workbench/#/new?project=EVAL-NEW')
                page.reload()
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                page.get_by_label('工作表名称', exact=True).fill('切换前未保存计划')
                page.get_by_label('项目', exact=True).click()
                page.once('dialog', lambda dialog: dialog.dismiss())
                page.locator('.n-base-select-option').filter(has_text='EVAL-OTHER').click()
                expect(page.get_by_label('工作表名称', exact=True)).to_have_value('切换前未保存计划')
                page.get_by_label('项目', exact=True).click()
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('.n-base-select-option').filter(has_text='EVAL-OTHER').click()
                expect(page).to_have_url(url + '/workbench/#/new?project=EVAL-OTHER')
                page.get_by_text('研究资料 · 正式建表前补齐', exact=True).click()
                expect(page.get_by_label('工作表名称', exact=True)).to_have_value('')
                # Standard upload -> next action -> select clauses -> draft resume -> create.
                standard = Document()
                table = standard.add_table(rows=0, cols=3)
                for values in (("Test Item", "Method", "Specification"),
                               ("Purity", "NR CE-SDS", "≥90.00%"),
                               ("Total Minor Species", "NR CE-SDS", "≤10.00%")):
                    for cell, value in zip(table.add_row().cells, values, strict=True):
                        cell.text = value
                standard_path = Path(temp) / 'acceptance-standard.docx'
                standard.save(standard_path)
                page.goto(url + '/workbench/#/import')
                expect(page.get_by_role('heading', name='导入文件', exact=True).last).to_be_visible()
                page.get_by_role('link', name='质量标准 Word：导入、查看或用于建表').click()
                standard_input = page.get_by_label('选择 Word 标准')
                expect(standard_input).to_be_enabled()
                standard_input.set_input_files(standard_path)
                expect(page.get_by_role('status')).to_contain_text('识别到 2 条标准')
                page.get_by_role('link', name='使用此标准新建研究表').click()
                page.get_by_label('项目', exact=True).click()
                page.locator('.n-base-select-option').filter(has_text='EVAL-OTHER').click()
                expect(page.get_by_role('heading', name='引用质量标准条款')).to_be_visible()
                page.get_by_label('引用条款 1:2', exact=True).check()
                page.get_by_label('引用条款 1:3', exact=True).check()
                page.get_by_role('button', name='将所选条款加入研究表').click()
                expect(page.locator('div.criterion-text').filter(has_text='≥90.00%')).to_be_visible()
                expect(page.locator('div.criterion-text').filter(has_text='≤10.00%')).to_be_visible()
                page.get_by_role('button', name='保存草稿', exact=True).click()
                expect(page.get_by_role('status')).to_contain_text('待补全计划已保存')
                page.get_by_role('link', name='重新打开已保存版本').click()
                page.reload()
                expect(page.locator('div.criterion-text').filter(has_text='≥90.00%')).to_be_visible()
                for label, value in {'批号': 'STD-B01', '规格': '1mg', '材料阶段': '裸抗', '开始日期': '2026-01-01',
                                     '方案编号或内部研究名称': 'STD-P1', '条件': '5℃'}.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_label('材料类别').select_option('DS')
                page.get_by_role('radio', name='长期', exact=True).check()
                page.get_by_role('button', name='创建研究表', exact=True).click()
                expect(page.get_by_role('heading', name='STD-B01', exact=True)).to_be_visible()
                view = page.request.get(url + '/api/v1/worksheets/' + page.url.rsplit('/', 1)[-1]).json()
                assert [row['standards'][0]['criterion'] for row in view['rows']] == ['≥90.00%', '≤10.00%']
                expect(page.get_by_label('稳定性研究信息')).to_contain_text('STD-B01')
                expect(page.get_by_role('columnheader', name='可接受标准 Acceptable Criteria')).to_be_visible()
                for detail in page.locator('.worksheet-matrix details').all():
                    detail.locator('summary').click()
                screenshot = ROOT / 'outputs' / 'acceptance-standard-linked.png'
                screenshot.parent.mkdir(exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
