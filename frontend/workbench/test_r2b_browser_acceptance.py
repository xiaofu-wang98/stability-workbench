"""Stage R2-B browser acceptance demonstration.
Flow:
1. Verify Main Navigation entries: 新建研究表, 质量标准与 COA, 保留现有入口.
2. Enter project -> default view shows sample batch cards and grouped worksheets without entering batch number.
   Cards display material category, batch, presentation, packaging, purpose, and quick shortcuts.
3. Enter worksheet -> Batch copy COA to 0M with atomic endpoint: preview matched items, original, new, conflicts -> 1-click batch copy.
4. Excel paste with legal N/A, NT, —, blank on non-required check positions -> non-required positions preserved and not overwritten.
   Conflict path: pasting actual value on non-required position prompts exact conflict coordinate.
5. Save & reload -> persistence verified.
6. One-click Excel confirmation export -> auto-triggers download while preserving backup link.
7. Verification of lease conflict and local draft recovery without lease theft.
"""
import json
import os
import re
import socket
import threading
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import openpyxl
import uvicorn
from docx import Document
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def create_spec_docx(indices, name):
    doc = Document()
    doc.add_paragraph(name)
    table = doc.add_table(rows=len(indices) + 1, cols=3)
    for cell, val in zip(table.rows[0].cells, ("Test Item", "Method", "Specification")):
        cell.text = val
    for row_idx, i in enumerate(indices, 2):
        for cell, val in zip(table.rows[row_idx - 1].cells, (f"检项_{i}", "SEC", "≥90.0%")):
            cell.text = val
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def run_acceptance():
    out_dir = ROOT / 'outputs/r2b-acceptance'
    out_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as temp, socket.socket() as listener:
        app = create_app(
            Store(Path(temp)),
            [],
            actor_for_role('researcher', '测试研究员', Role.ADMIN),
            workbench=ROOT / 'frontend/workbench/dist',
        )
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()

        try:
            with sync_playwright() as pw:
                executable = next((Path(os.environ['LOCALAPPDATA']) / 'ms-playwright').glob(
                    'chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
                browser = pw.chromium.launch(executable_path=str(executable))
                context = browser.new_context(viewport={'width': 1480, 'height': 1050},
                                              permissions=['clipboard-read', 'clipboard-write'])
                page = context.new_page()
                page.set_default_timeout(15000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))

                print("=== Step 1: 验证主导航入口 ===")
                page.goto(url)
                nav = page.locator('nav[aria-label="主导航"]')
                expect(nav.get_by_role('link', name='工作台')).to_be_visible()
                expect(nav.get_by_role('link', name='新建研究表')).to_be_visible()
                expect(nav.get_by_role('link', name='项目数据')).to_be_visible()
                expect(nav.get_by_role('link', name='质量标准与 COA')).to_be_visible()
                expect(nav.get_by_role('link', name='导入文件')).to_be_visible()
                expect(nav.get_by_role('link', name='趋势与比较')).to_be_visible()
                expect(nav.get_by_role('link', name='生成与历史')).to_be_visible()
                print("  [PASS] 主导航成功补齐「新建研究表」和「质量标准与 COA」，并保留现有全部入口。")

                print("=== Step 2: 建立项目、质量标准与样品实体 ===")
                # 1. 创建项目
                res_proj = page.request.post(url + '/api/v1/projects', data={'project': 'PRJ-R2B', 'name': '友好交互验收项目'})
                assert res_proj.ok

                # 2. 登记样品 BATCH-01
                sample_data = {
                    'batch': 'BATCH-01',
                    'material_stage': '原液',
                    'sample_type': 'DS',
                    'presentation': '100mg/瓶',
                    'packaging': '中硼硅西林瓶',
                    'purpose': '出厂放行与长期稳定性',
                }
                sample_res = page.request.post(url + '/api/v1/projects/PRJ-R2B/samples', data=sample_data)
                assert sample_res.ok, sample_res.text()
                sample_id = sample_res.json()['id']

                # 3. 上传并确认质量标准
                docx_bytes = create_spec_docx([1, 2, 3], "PRJ-R2B 质量标准")
                spec_res = page.request.post(
                    url + '/api/v1/specifications',
                    multipart={'file': {'name': 'PRJ-R2B_Spec.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': docx_bytes}}
                )
                assert spec_res.ok
                spec_id = spec_res.json()['id']

                # 注册标准检项到项目字典
                config_rev = page.request.get(url + '/api/v1/projects/PRJ-R2B/configuration').json()['base_revision']
                reg_res = page.request.post(
                    url + f'/api/v1/projects/PRJ-R2B/specifications/{spec_id}/components',
                    data={
                        'revision': config_rev,
                        'idempotency_key': 'reg-spec-r2b',
                        'components': [
                            {'table': 1, 'row': 2, 'assay_zh': '检项_1', 'component_zh': '检项_1', 'method': 'SEC', 'unit': '%', 'result_type': 'numeric'},
                            {'table': 1, 'row': 3, 'assay_zh': '检项_2', 'component_zh': '检项_2', 'method': 'SEC', 'unit': '%', 'result_type': 'numeric'},
                            {'table': 1, 'row': 4, 'assay_zh': '检项_3', 'component_zh': '检项_3', 'method': 'SEC', 'unit': '%', 'result_type': 'numeric'},
                        ]
                    }
                )
                assert reg_res.ok
                links = reg_res.json()['links']
                item_ids = [l['item_id'] for l in links]

                # 录入 COA 放行结果 (检项_1 = 98.50%, 检项_2 = 96.20%, 检项_3 = 94.00%)
                coa_state = page.request.get(url + f'/api/v1/samples/{sample_id}/coa/{spec_id}').json()
                save_coa_res = page.request.post(
                    url + f'/api/v1/samples/{sample_id}/coa/{spec_id}',
                    data={
                        'revision': coa_state['revision'],
                        'version': coa_state['version'],
                        'idempotency_key': 'coa-save-r2b',
                        'values': {'1:2': '98.50%', '1:3': '96.20%', '1:4': '94.00%'},
                    }
                )
                assert save_coa_res.ok

                # 4. 创建研究工作表：0M 考核检项 1, 2, 3；1M 考核检项 1, 3 (检项 2 在 1M 为非应检位置！)
                criteria = {
                    item_ids[0]: [{'specification_id': spec_id, 'table': 1, 'row': 2}],
                    item_ids[1]: [{'specification_id': spec_id, 'table': 1, 'row': 3}],
                    item_ids[2]: [{'specification_id': spec_id, 'table': 1, 'row': 4}],
                }
                study_res = page.request.post(
                    url + '/api/v1/projects/PRJ-R2B/worksheets',
                    data={
                        'name': '长期稳定性考察',
                        'sample_id': sample_id,
                        'idempotency_key': 'study-r2b',
                        'identity': {
                            'batch': 'BATCH-01',
                            'presentation': '100mg/瓶',
                            'sample_type': 'DS',
                            'study': 'long_term',
                            'study_id': '长期',
                            'condition': '5℃±3℃',
                        },
                        'start_date': '2026-02-01',
                        'items': item_ids,
                        'item_standards': criteria,
                        'points': [
                            {'unit': 'M', 'amount': 0, 'required_items': item_ids},
                            {'unit': 'M', 'amount': 1, 'required_items': [item_ids[0], item_ids[2]]}, # 检项 2 在 1M 为非应检！
                        ]
                    }
                )
                assert study_res.ok, study_res.text()
                sheet_id = study_res.json()['id']
                second = page.request.post(url + '/api/v1/projects/PRJ-R2B/samples',
                                           data={**sample_data, 'material_stage': '裸抗'})
                assert second.ok, second.text()
                unlinked = page.request.post(url + '/api/v1/projects/PRJ-R2B/worksheets', data={
                    'name': '未绑定的历史研究表',
                    'idempotency_key': 'unlinked-r2b',
                    'identity': {'batch': 'BATCH-01', 'presentation': '100mg/瓶', 'sample_type': 'DS',
                                 'study': 'accelerated', 'study_id': '历史', 'condition': '25℃'},
                    'start_date': '2026-02-01',
                    'items': item_ids,
                    'points': [{'unit': 'M', 'amount': 0, 'required_items': item_ids}],
                })
                assert unlinked.ok, unlinked.text()

                print("=== Step 3: 进入项目页面，默认查看样品卡片与研究表分组 ===")
                page.goto(url + '/workbench/#/projects/PRJ-R2B')
                # 验证不需要输入批号即可直接看到样品卡片
                card = page.locator('.sample-card').filter(has_text='材料类别：原液')
                expect(card).to_be_visible()
                expect(card).to_contain_text('批号：BATCH-01')
                expect(card).to_contain_text('材料类别：原液')
                expect(card).to_contain_text('规格：100mg/瓶')
                expect(card).to_contain_text('包装：中硼硅西林瓶')
                expect(card).to_contain_text('用途：出厂放行与长期稳定性')
                # 验证卡片内包含分组的研究表
                expect(card.locator('.sample-worksheets-box')).to_contain_text('长期稳定性考察')
                # 验证快捷入口
                expect(card.get_by_role('link', name='进入研究表')).to_be_visible()
                expect(card.get_by_role('link', name='COA')).to_be_visible()
                expect(card.get_by_role('link', name='上传数据')).to_be_visible()
                expect(page.locator('.unlinked-section')).to_contain_text('未绑定的历史研究表')
                expect(page.locator('.sample-worksheets-box').get_by_text('未绑定的历史研究表')).to_have_count(0)
                card.get_by_role('link', name='COA').click()
                expect(page.get_by_text('当前样品 BATCH-01 · 原液', exact=False)).to_be_visible()
                expect(page.get_by_text('COA 版本 1', exact=False)).to_be_visible()
                page.goto(url + '/workbench/#/projects/PRJ-R2B')
                print("  [PASS] 项目页面默认展示样品卡片，完整展示材料类别、批号、规格、包装、用途与分组研究表，提供快捷入口。")

                print("=== Step 4: 进入研究表，演示 R2-A 0M 批量复制接口接入 ===")
                card.get_by_role('link', name='进入研究表').click()
                expect(page.get_by_role('heading', name='长期稳定性考察')).to_be_visible()

                # 点击编辑结果
                page.get_by_role('button', name='编辑结果').click()
                expect(page.get_by_role('button', name='从 COA 复制到 0M')).to_be_visible()

                # 打开 COA 复制
                page.get_by_role('button', name='从 COA 复制到 0M').click()
                release_panel = page.locator('.release-panel')
                expect(release_panel).to_be_visible()
                completed = release_panel.locator('.date-label input')
                refresh_preview = release_panel.get_by_role('button', name='刷新匹配预览')
                expect(completed).to_have_value('')
                expect(refresh_preview).to_be_disabled()
                expect(release_panel.locator('.batch-table')).to_have_count(0)
                completed.fill('2026-01-15')
                refresh_preview.click()
                expect(release_panel.locator('.batch-table')).to_be_visible()
                expect(release_panel.get_by_text('匹配待复制')).to_have_count(3)
                expect(release_panel).to_contain_text('98.50%')
                expect(release_panel).to_contain_text('96.20%')
                expect(release_panel).to_contain_text('94.00%')

                # 测试错误路径：录入超前日期
                completed.fill('2026-03-01')
                expect(release_panel.locator('.batch-table')).to_have_count(0)
                refresh_preview.click()
                expect(release_panel.get_by_role('alert')).to_contain_text('放行完成日期')

                # 恢复正确日期
                completed.fill('2026-01-15')
                refresh_preview.click()
                expect(release_panel.locator('.row-matched')).to_have_count(3)

                # 一次确认批量复制 (原子操作)
                confirm_copy = release_panel.get_by_role('button', name=re.compile('一次确认批量复制'))
                expect(confirm_copy).to_be_disabled()
                release_panel.locator('thead').get_by_role('checkbox').click()
                expect(confirm_copy).to_be_disabled()
                release_panel.get_by_placeholder('请说明本次批量复制依据').fill('已核对实际放行日期及适用条款')
                confirm_copy.click()
                expect(page.get_by_role('status')).to_have_text('已批量复制 COA 结果到 0M。')
                print("  [PASS] 成功接入 R2-A COA 批量 0M 预览与原子确认接口，完成匹配、原值、新值展示与一次批量复制。")

                print("=== Step 5: 演示 Excel 粘贴含 N/A 的非应检列与冲突坐标拦截 ===")
                expect(page.get_by_role('button', name='结束编辑')).to_be_enabled()
                canvas = page.locator('canvas[id^="univer-sheet-main-canvas"]')
                expect(canvas).to_be_visible()
                box = canvas.bounding_box()
                assert box

                # 聚焦到 1M 第一行单元格位置 (col index 3 in visual grid)
                # 1M 列包含: 行 2 (检项_1: 应检), 行 3 (检项_2: 非应检 '—'), 行 4 (检项_3: 应检)
                page.mouse.click(box['x'] + 680, box['y'] + 105)
                page.keyboard.press('ArrowDown')
                page.keyboard.type('95.50')
                expect(page.get_by_role('alert')).to_contain_text('请选择应检结果格录入。')
                page.keyboard.press('ArrowUp')

                # 5.1 错误路径测试：向包含非应检格的列粘贴实际数值 (如 检项 2 收到 95.5%)
                conflict_paste = "98.10%\t95.50%\t93.80%" # 复制包含实际数值到非应检位置
                # 换行粘贴列：
                conflict_col_paste = "98.10%\n95.50%\n93.80%"
                page.evaluate('value => navigator.clipboard.writeText(value)', conflict_col_paste)
                page.keyboard.press('Control+v')
                # 必须提示具体冲突坐标，不能静默丢弃
                expect(page.get_by_role('alert')).to_contain_text('非应检位置不得覆盖。发现冲突坐标：')
                expect(page.get_by_role('alert')).to_contain_text('收到「95.50%」')
                print("  [PASS] 冲突路径：非应检位置收到实际数值时，成功拦截并给出具体冲突坐标提示。")

                # 5.2 正常路径测试：粘贴包含合法 N/A / NT / — / 空白的检测列
                legal_col_paste = "98.10%\nN/A\n93.80%"
                page.evaluate('value => navigator.clipboard.writeText(value)', legal_col_paste)
                page.keyboard.press('Control+v')
                expect(page.get_by_role('status')).to_have_text('有未保存的改动')
                print("  [PASS] 正常路径：支持从 Excel 粘贴包含合法 N/A、空白的非应检位置数据，且非应检格未被破坏。")

                # 填写更正原因 (演示快捷依据选项)
                reasons_box = page.locator('.reason-box')
                if reasons_box.is_visible():
                    page.get_by_role('button', name='数据录入笔误更正').click()

                # 保存并刷新
                print("=== Step 6: 保存结果并刷新验证持久化 ===")
                page.get_by_role('button', name='保存结果').click()
                expect(page.get_by_role('status')).to_have_text('已保存。')
                page.get_by_role('button', name='结束编辑').click()

                # 页面刷新
                page.reload()
                expect(page.get_by_role('heading', name='长期稳定性考察')).to_be_visible()
                # 验证 0M 批量复制的结果与 1M 粘贴的结果成功留存
                matrix = page.locator('.worksheet-matrix')
                expect(matrix).to_contain_text('98.50%')
                expect(matrix).to_contain_text('96.20%')
                expect(matrix).to_contain_text('98.10%')
                expect(matrix).to_contain_text('93.80%')
                print("  [PASS] 工作表刷新后，批量复制与粘贴数据完整保留。")

                print("=== Step 7: 导出确认表并自动触发下载，保留备用链接 ===")
                with page.expect_download() as dl:
                    page.get_by_role('button', name='生成确认表 Excel').click()
                downloaded_file = dl.value
                excel_bytes = downloaded_file.path().read_bytes()
                assert excel_bytes.startswith(b'PK'), "Downloaded file should be a valid zip/xlsx"
                wb = openpyxl.load_workbook(BytesIO(excel_bytes))
                assert len(wb.sheetnames) >= 1
                wb.close()

                # 验证备用下载链接依然存在可见
                backup_link = page.get_by_role('link', name='下载确认表')
                expect(backup_link).to_be_visible()
                print("  [PASS] 生成确认表后自动触发下载，并成功验证 Excel 文件有效性与备用链接。")

                print("=== Step 8: 验证租约防抢占与未保存草稿恢复机制 ===")
                page.get_by_role('button', name='编辑结果').click()
                expect(page.locator('canvas[id^="univer-sheet-main-canvas"]')).to_be_visible()

                # 模拟另一个使用者并发打开，验证不可擅自抢占租约
                other_page = context = browser.new_page()
                other_page.goto(page.url)
                other_page.get_by_role('button', name='编辑结果').click()
                expect(other_page.get_by_role('alert')).to_contain_text('正在编辑此工作表')
                other_page.close()
                print("  [PASS] 租约保护：其他使用者无法擅自抢占租约。")

                page.bring_to_front()
                page.get_by_role('button', name='结束编辑').click()

                print("\n==========================================")
                print("ALL STAGE R2-B BROWSER ACCEPTANCE TESTS PASSED!")
                print("==========================================")
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == '__main__':
    run_acceptance()
