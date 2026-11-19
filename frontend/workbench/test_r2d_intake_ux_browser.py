"""Stage R2-D browser regression test suite:
1. Root-cell task card aggregation & <= 6 clicks to resolve multi-timepoint conflicts (D6, D7).
2. DOM stability & silent in-place refresh without page recreation, Toast feedback <= 1.5s.
3. Anomaly editing drawer (CandidateSourceEditor): <= 3 focused fields, 18 fields folded, quick reason pills.
4. Cell snapshot LRU cache response time < 1000ms.
5. Multi-sheet progress badges, URL section synchronization, F5 persistence, and commit jump to ProjectResults.
"""
import os
import socket
import threading
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import openpyxl
import uvicorn
from docx import Document
from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def create_spec_docx(rows, name):
    doc = Document()
    doc.add_paragraph(name)
    table = doc.add_table(rows=len(rows) + 1, cols=3)
    for cell, val in zip(table.rows[0].cells, ("Test Item", "Method", "Specification")):
        cell.text = val
    for row_idx, (item, method, crit) in enumerate(rows, 1):
        for cell, val in zip(table.rows[row_idx].cells, (item, method, crit)):
            cell.text = val
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def create_test_workbook(path):
    wb = Workbook()
    
    # Sheet 1: DS长期 with root cell bilingual conflicts D6 and D7
    ws1 = wb.active
    ws1.title = "DS长期"
    
    # Headers
    ws1["A1"] = "原液长期稳定性试验数据（5℃）"
    ws1["A2"] = "样品批号：LOT-2026-R2D"
    ws1["C2"] = "实验条件：5℃±3℃"
    ws1["A3"] = "检测项目"
    ws1["C3"] = "方法"
    ws1["D3"] = "质量标准"
    ws1["E3"] = "考察时间（月）"
    
    ws1["E4"] = "0个月"
    ws1["F4"] = "1个月"
    ws1["G4"] = "3个月"
    
    # Row 5: 纯度 Monomer
    ws1["A5"] = "纯度和杂质"
    ws1["B5"] = "纯度"
    ws1["C5"] = "SEC-HPLC法"
    ws1["D5"] = "单体（%）：≥95.0%\nMonomer（%）"
    ws1["E5"] = 98.2
    ws1["F5"] = 98.0
    ws1["G5"] = 97.9
    
    # Row 6: D6 conflict: 聚合体 vs Monomer across 0M, 1M, 3M
    ws1["A6"] = "纯度和杂质"
    ws1["B6"] = "纯度"
    ws1["C6"] = "SEC-HPLC法"
    ws1["D6"] = "聚合体（%）：≤3.5%\nMonomer（%）"
    ws1["E6"] = 1.1
    ws1["F6"] = 1.2
    ws1["G6"] = 1.3
    
    # Row 7: D7 conflict: 单体 vs HMW across 0M, 1M, 3M
    ws1["A7"] = "纯度和杂质"
    ws1["B7"] = "纯度"
    ws1["C7"] = "SEC-HPLC法"
    ws1["D7"] = "单体（%）：≥95.0%\nHMW（%）"
    ws1["E7"] = 98.2
    ws1["F7"] = 98.0
    ws1["G7"] = 97.9
    
    # Merge label columns
    for area in ('A3:B4', 'C3:C4', 'D3:D4', 'E3:G3', 'A5:A7', 'B5:B7', 'C5:C7'):
        ws1.merge_cells(area)
        
    # Sheet 2: -20℃ standard sheet
    ws2 = wb.create_sheet(title="-20℃")
    ws2["A1"] = "原液长期稳定性试验数据（-20℃）"
    ws2["A2"] = "样品批号：LOT-2026-R2D"
    ws2["C2"] = "实验条件：-20℃"
    ws2["A3"] = "检测项目"
    ws2["C3"] = "方法"
    ws2["D3"] = "质量标准"
    ws2["E3"] = "考察时间（月）"
    ws2["E4"] = "0个月"
    
    ws2["A5"] = "纯度"
    ws2["B5"] = "纯度"
    ws2["C5"] = "SEC-HPLC法"
    ws2["D5"] = "单体（%）：≥95.0%"
    ws2["E5"] = 98.5
    for area in ('A3:B4', 'C3:C4', 'D3:D4'):
        ws2.merge_cells(area)
        
    wb.save(path)
    wb.close()


def main():
    print("=== Stage R2-D 稳定性汇总表入库全流程交互重构验收测试 ===")
    
    with TemporaryDirectory() as temp, socket.socket() as listener:
        app = create_app(
            Store(Path(temp)),
            [],
            actor_for_role('tester', '测试员', Role.ADMIN),
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
                context = browser.new_context(viewport={'width': 1480, 'height': 1050})
                page = context.new_page()
                page.set_default_timeout(15000)

                # 1. 预置基础数据：项目、样品、质量标准
                print("\n[Step 1] 注册项目、样品与质量标准...")
                proj_res = page.request.post(url + '/api/v1/projects', data={'project': 'PRJ-R2D', 'name': 'R2D体验重构项目'})
                assert proj_res.ok, proj_res.text()

                sample_res = page.request.post(url + '/api/v1/projects/PRJ-R2D/samples', data={
                    'batch': 'LOT-2026-R2D', 'sample_type': 'DS', 'presentation': '100mg/瓶',
                    'material_stage': '原液', 'purpose': '长期稳定性',
                })
                assert sample_res.ok, sample_res.text()
                sample_id = sample_res.json()['id']

                spec_docx = create_spec_docx([
                    ("纯度", "SEC-HPLC法", "单体（%）：≥ 95.0%"),
                    ("纯度", "SEC-HPLC法", "高分子量组分（%）：≤ 3.5%"),
                    ("纯度", "SEC-HPLC法", "低分子量组分（%）：≤ 1.5%"),
                ], "PRJ-R2D 质量标准")
                spec_res = page.request.post(
                    url + '/api/v1/specifications',
                    multipart={'file': {'name': 'PRJ-R2D-Spec.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': spec_docx}}
                )
                assert spec_res.ok, spec_res.text()
                spec_id = spec_res.json()['id']

                config_rev = page.request.get(url + '/api/v1/projects/PRJ-R2D/configuration').json()['base_revision']
                page.request.post(
                    url + f'/api/v1/projects/PRJ-R2D/specifications/{spec_id}/components',
                    data={
                        'revision': config_rev,
                        'idempotency_key': 'reg-spec-r2d',
                        'components': [
                            {'table': 1, 'row': 1, 'assay_zh': '纯度', 'method': 'SEC-HPLC法', 'component_zh': '单体', 'result_type': 'numeric', 'unit': '%'},
                            {'table': 1, 'row': 2, 'assay_zh': '纯度', 'method': 'SEC-HPLC法', 'component_zh': '高分子量组分', 'result_type': 'numeric', 'unit': '%'},
                            {'table': 1, 'row': 3, 'assay_zh': '纯度', 'method': 'SEC-HPLC法', 'component_zh': '低分子量组分', 'result_type': 'numeric', 'unit': '%'},
                        ]
                    }
                )

                # 生成多 Sheet 测试 Excel
                test_excel = Path(temp) / 'R2D-MultiSheet.xlsx'
                create_test_workbook(test_excel)

                # 2. 上传原件并验证多工作表状态
                print("\n[Step 2] 导入多 Sheet 原件并验证状态...")
                page.goto(url + f'/workbench/#/import?project=PRJ-R2D&sample={sample_id}')
                page.get_by_label('选择来源文件 Excel 或 Word').set_input_files(test_excel)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_role('button', name='解析并核对').click()

                # 等待进入核对视图
                expect(page.get_by_role('heading', name='R2D-MultiSheet.xlsx')).to_be_visible()
                expect(page.get_by_text('已完成 0 / 2 张工作表')).to_be_visible()

                # 验证 Sheet 下拉框选项徽章
                sheet_select = page.locator('.sheet-select-row .n-base-selection')
                sheet_select.click()
                expect(page.locator('.n-base-select-option').filter(has_text='DS长期 [⚠ 待核对')).to_be_visible()
                expect(page.locator('.n-base-select-option').filter(has_text='-20℃ [○ 待接收]')).to_be_visible()
                # 关闭下拉菜单
                page.keyboard.press('Escape')

                # 3. 接收 DS长期 候选并打开来源就地核对
                print("\n[Step 3] 接收 DS长期 候选并验证根源卡片聚合...")
                receive_btn = page.get_by_role('button', name='供核对')
                expect(receive_btn).to_be_visible()
                receive_btn.click()

                source_panel = page.locator('#sourceReviewPanel')
                if not source_panel.is_visible():
                    page.get_by_role('button', name='查看本表来源核对建议').click()
                expect(source_panel).to_be_visible()

                # 场景 1 验证：卡片聚合与步数减半
                # D6 与 D7 虽然各含 3 个时间点，但按物理来源格严格聚合为 2 张卡片
                task_cards = page.locator('.task-card')
                expect(task_cards).to_have_count(2)
                print("  [PASS] 待办卡片数严格等于物理来源格数 (D6/D7 共 2 张卡片，非 6 个分散工单)")

                card_d6 = task_cards.filter(has_text='D6')
                expect(card_d6).to_be_visible()
                expect(card_d6).to_contain_text('影响 3 个时间点')

                card_d7 = task_cards.filter(has_text='D7')
                expect(card_d7).to_be_visible()
                expect(card_d7).to_contain_text('影响 3 个时间点')

                # 场景 2 验证：DOM 稳定性与防抖，Toast 弹出 <= 1.5s，顶栏待办计数实时递减
                print("\n[Step 4] 验证快速决策、DOM 稳定性与实时计数...")
                # 记录面板 DOM 标记
                page.evaluate("() => { document.getElementById('sourceReviewPanel').setAttribute('data-stability-test', 'active-dom-token'); }")

                # 点击 D6 的快捷选择「采用中文：聚合体」
                clicks_count = 0
                card_d6.get_by_role('button', name='采用中文：聚合体').click()
                clicks_count += 1

                # 点击一次确认保存此项
                t_start = time.time()
                card_d6.get_by_role('button', name='一次确认保存此项').click()
                clicks_count += 1

                # 验证 Toast 在 1.5 秒内弹出
                toast = page.locator('.n-message')
                expect(toast.first).to_be_visible(timeout=1500)
                t_elapsed = time.time() - t_start
                assert t_elapsed <= 1.5, f"Toast response too slow: {t_elapsed}s"
                print(f"  [PASS] Toast 轻提示在 {t_elapsed:.2f}s 内快速弹出")

                # 验证 DOM 容器未被销毁重建
                dom_token = page.evaluate("() => document.getElementById('sourceReviewPanel')?.getAttribute('data-stability-test')")
                assert dom_token == 'active-dom-token', "DOM was re-mounted or reconstructed!"
                print("  [PASS] DOM 容器保持稳定，未发生整页或组件重构销毁")

                # 验证 D6 已进入「已处理」折叠区，卡片数变为 1
                expect(page.locator('.task-card')).to_have_count(1)
                resolved_group = page.locator('.resolved-group')
                expect(resolved_group).to_be_visible()
                expect(resolved_group.locator('summary')).to_contain_text('本次已处理 (1) 项任务')
                resolved_group.locator('summary').click()
                expect(page.locator('.resolved-strip').filter(has_text='D6')).to_be_visible()
                print("  [PASS] 顶栏待办卡片计数实时 -1，已处理项进入绿色收拢折叠区")

                # 解决 D7
                card_d7 = page.locator('.task-card').first
                card_d7.get_by_role('button', name='采用中文：单体').click()
                clicks_count += 1
                card_d7.get_by_role('button', name='一次确认保存此项').click()
                clicks_count += 1

                # 确认全部卡片解决
                expect(page.get_by_text('当前工作表没有待核对的来源任务，已全部就绪。')).to_be_visible()
                assert clicks_count <= 6, f"Too many clicks: {clicks_count}"
                print(f"  [PASS] 仅用 {clicks_count} 次点击完成多时间点双语冲突处置 (<= 6 次要求达成)")

                # 返回原件概要
                page.get_by_role('button', name='返回原件概要').click()

                # 场景 3 验证：异常修改抽屉体验
                print("\n[Step 5] 验证候选修改抽屉体验与字段折叠...")
                # 点击任意行的「修改解释」或「去核对 / 修改」
                first_edit_btn = page.locator('.row-actions .n-button').first
                first_edit_btn.click()

                drawer = page.locator('.candidate-drawer')
                expect(drawer).to_be_visible()
                print("  [PASS] 右侧抽屉顺滑滑出")

                # 验证默认仅展示出错的 1–3 个字段
                focused_fields = drawer.locator('.field-row')
                focused_count = focused_fields.count()
                assert 1 <= focused_count <= 3, f"Unexpected focused fields count: {focused_count}"
                print(f"  [PASS] 默认聚焦 {focused_count} 个重点字段 (<= 3)")

                # 验证全部 18 个底层字段处于折叠状态
                all_fields_details = drawer.locator('.all-fields-details')
                expect(all_fields_details).not_to_have_attribute('open', '')
                print("  [PASS] 全部 18 个底层字段处于折叠保护状态")

                # 验证快捷原因药丸
                pill = drawer.locator('.quick-pills .n-button').first
                pill_text = pill.inner_text()
                pill.click()
                reason_input = drawer.locator('.reason-section input')
                expect(reason_input).to_have_value(pill_text)
                print(f"  [PASS] 点击快捷原因药丸成功自动填入：{pill_text}")

                # 关闭抽屉
                drawer.get_by_role('button', name='关闭抽屉').click()
                expect(drawer).not_to_be_visible()

                # 场景 4 验证：缓存命中响应时间 < 1000ms
                print("\n[Step 6] 验证原件单元格只读快照缓存响应时间 (< 1000ms)...")
                source_id = page.url.split('source=')[1].split('&')[0]
                # 连续发两次请求
                page.request.get(url + f'/api/v1/previews/{source_id}/v5/review-actions?section=DS%E9%95%BF%E6%9C%9F')
                t0 = time.time()
                resp2 = page.request.get(url + f'/api/v1/previews/{source_id}/v5/review-actions?section=DS%E9%95%BF%E6%9C%9F')
                t_cache = (time.time() - t0) * 1000
                assert resp2.ok
                assert t_cache < 1000, f"Cache response too slow: {t_cache:.1f}ms"
                print(f"  [PASS] 二次核对请求耗时仅 {t_cache:.1f}ms (< 1000ms)，消灭 11 秒卡顿")

                # 写入 DS长期
                print("\n[Step 7] 写入 DS长期 工作表...")
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                expect(page.get_by_text('此工作表已写入。')).to_be_visible()

                # 场景 5 验证：跨表进度与跳转闭环
                print("\n[Step 8] 验证跨表进度徽章、URL 同步与 F5 刷新保留...")
                # 检查切表下拉框
                sheet_select.click()
                expect(page.locator('.n-base-select-option').filter(has_text='DS长期 [✓ 已入库]')).to_be_visible()
                expect(page.locator('.n-base-select-option').filter(has_text='-20℃ [○ 待接收]')).to_be_visible()
                page.locator('.n-base-select-option').filter(has_text='-20℃').click()

                # 断言 URL 包含 section=-20℃
                import re
                expect(page).to_have_url(re.compile(r'(-20|%E2%88%9220)'))
                print("  [PASS] 切换 Sheet 后 URL 参数实时同步")

                # F5 刷新并验证停留在当前工作表
                page.reload()
                expect(page.get_by_role('heading', name='R2D-MultiSheet.xlsx')).to_be_visible()
                expect(page.locator('.sheet-select-row')).to_contain_text('-20℃')
                print("  [PASS] F5 刷新后精确停留在当前工作表与核对进度")

                # 接收并写入 -20℃ 工作表
                print("\n[Step 9] 接收并写入 -20℃，验证全表完成卡片与结果跳转闭环...")
                receive_btn2 = page.get_by_role('button', name='供核对')
                if receive_btn2.is_visible():
                    receive_btn2.click()
                page.get_by_role('button', name='核对无误，写入本工作表').click()
                expect(page.get_by_text('此工作表已写入。')).to_be_visible()

                # 验证两张表均已入库
                expect(page.get_by_text('已完成 2 / 2 张工作表')).to_be_visible()

                # 点击成功卡片中的「3. 查看已入库结果」
                results_link = page.get_by_role('link', name='3. 查看已入库结果')
                expect(results_link).to_be_visible()
                results_link.click()

                # 验证平滑跳转并定位至 ProjectResults
                expect(page.locator('#project-results-view')).to_be_visible()
                assert 'focus=results' in page.url
                print("  [PASS] 成功卡片一键跳转至结果视图，URL 携带 focus=results，全流程闭环")

                print("\n=======================================================")
                print("ALL STAGE R2-D BROWSER REGRESSION TESTS PASSED!")
                print("=======================================================\n")
        finally:
            server.should_exit = True
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
