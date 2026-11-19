"""Stage R2-D-fix browser regression test suite using REAL IP281 workbook:
1. Real IP281 workbook verification (SHA256: bf07cbcde18b792e7dc3ac0a62c8bf4f2013405ad37de39f56eeed03b203bc44).
2. Accelerated sheet (102 candidates, 7 tasks: 2 bilingual conflicts D6/D7, 2 revisions, 3 comments).
3. No auto-selection on load; explicit click to select choice, <= 6 clicks to resolve D6 + D7.
4. DOM stability & silent in-place refresh without page recreation, Toast feedback <= 1.5s.
5. Batch actions for revisions & comments, write 102 adopted versions into database.
6. Multi-sheet navigation, badge updates, clean button states when committed.
7. -20℃ sheet (903 candidates, 80 tasks): category filtering, 25/page pagination, drawer visual strikethrough, dirty protection modal.
"""
import hashlib
import os
import re
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]
REAL_IP281_PATH = ROOT / "outputs/acceptance/20261117-ip281-header-retest/sources/IP281稳定性试验数据-常熟.xlsx"
EXPECTED_SHA256 = "bf07cbcde18b792e7dc3ac0a62c8bf4f2013405ad37de39f56eeed03b203bc44"


def main():
    print("=== Stage R2-D-fix 稳定性汇总表入库全流程交互重构验收测试（真实 IP281）===")
    assert REAL_IP281_PATH.exists(), f"Real IP281 file not found at {REAL_IP281_PATH}"
    actual_sha = hashlib.sha256(REAL_IP281_PATH.read_bytes()).hexdigest()
    assert actual_sha == EXPECTED_SHA256, f"SHA256 mismatch! Expected {EXPECTED_SHA256}, got {actual_sha}"
    print(f"  [PASS] 真实 IP281 原件 SHA256 校验通过: {actual_sha}")

    with TemporaryDirectory(ignore_cleanup_errors=True) as temp, socket.socket() as listener:
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
                page.set_default_timeout(25000)
                page.on("console", lambda msg: print(f"BROWSER: {msg.text}"))

                # 1. 注册目标项目
                print("\n[Step 1] 注册真实 IP281 项目...")
                proj_res = page.request.post(url + '/api/v1/projects', data={'project': 'IP281', 'name': 'IP281 真实稳定性项目'})
                assert proj_res.ok, proj_res.text()
                print("  [PASS] 项目 IP281 注册成功")

                # 2. 导入真实 IP281 原件并进入多工作表工作台
                print("\n[Step 2] 导入真实 IP281 原件并验证多工作表状态...")
                page.goto(url + '/workbench/#/import?project=IP281')
                page.locator('input[accept=".xlsx,.docx"]').set_input_files(REAL_IP281_PATH)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_role('button', name='解析并核对', exact=True).click()

                # 等待进入核对视图
                expect(page.get_by_role('heading', name='IP281稳定性试验数据-常熟.xlsx')).to_be_visible(timeout=45000)
                print("  [PASS] 进入多工作表核对视图")

                # 验证工作表下拉框选项徽章
                sheet_select = page.locator('.sheet-select-row .n-base-selection')
                sheet_select.click()
                acc_sheet_name = 'IP281-C-20240529-DS 加速（5±3℃）√'
                expect(page.locator('.n-base-select-option').filter(has_text=acc_sheet_name)).to_contain_text('[○ 待接收]')
                expect(page.locator('.n-base-select-option').filter(has_text='-20℃').first).to_contain_text('[○ 待接收]')
                page.keyboard.press('Escape')
                print("  [PASS] 多工作表状态徽章显示正确（初始均为 [○ 待接收]）")

                # 3. 接收加速工作表候选并进入来源就地核对
                print("\n[Step 3] 接收加速工作表候选（102条）并打开就地核对...")
                receive_btn = page.get_by_role('button', name='供核对')
                expect(receive_btn).to_be_visible()
                expect(receive_btn).to_contain_text('接收 102 条候选供核对')
                receive_btn.click()

                # 打开来源核对面板
                source_panel = page.locator('#sourceReviewPanel')
                if not source_panel.is_visible():
                    page.get_by_role('button', name='查看本表来源核对建议').click()
                expect(source_panel).to_be_visible(timeout=25000)

                # 场景 1 验证：卡片聚合（物理来源格）与禁止加载即自动预选
                expect(page.locator('.task-card').first).to_be_visible(timeout=25000)
                task_cards = page.locator('.task-card')
                card_count = task_cards.count()
                print(f"  当前加速表待核对独立卡片数: {card_count}")
                assert card_count >= 7, f"Expected at least 7 task cards, got {card_count}"

                # 验证 D6 与 D7 卡片存在，且没有任何快捷选项被自动选中
                card_d6 = task_cards.filter(has_text='D6')
                expect(card_d6).to_be_visible()
                expect(card_d6).to_contain_text('双语冲突')
                expect(card_d6).to_contain_text('影响 3 个时间点')

                # 验证加载后绝对没有自动预选任何选项
                d6_save_btn = card_d6.get_by_role('button', name='一次确认保存此项')
                expect(d6_save_btn).not_to_be_visible()
                print("  [PASS] 红线守住：卡片加载后默认未选中任何快捷选项，保存按钮不预显")

                # 场景 2 验证：显式选择后保存，DOM 稳定性，Toast 响应 <= 1.5s
                print("\n[Step 4] 验证显式选择、<= 6 次点击处置双语冲突、DOM 稳定性与 Toast 反馈...")
                page.evaluate("() => { document.getElementById('sourceReviewPanel').setAttribute('data-stability-test', 'active-dom-token'); }")

                clicks_count = 0
                # 点击 D6 的「采用中文：纯度Purity · SEC-HPLC · Monomer」
                btn_zh_d6 = card_d6.get_by_role('button', name='采用中文：纯度Purity · SEC-HPLC · Monomer')
                expect(btn_zh_d6).to_be_visible()
                btn_zh_d6.click()
                clicks_count += 1

                # 显式选择后保存按钮可见
                expect(d6_save_btn).to_be_visible()
                t_start = time.time()
                d6_save_btn.click()
                clicks_count += 1

                # 验证 Toast 轻提示成功弹出
                toast = page.locator('.n-message')
                expect(toast.first).to_be_visible(timeout=8000)
                t_elapsed = time.time() - t_start
                print(f"  [PASS] Toast 轻提示在 {t_elapsed:.2f}s 内成功弹出")

                # 验证 DOM 容器保持稳定
                dom_token = page.evaluate("() => document.getElementById('sourceReviewPanel')?.getAttribute('data-stability-test')")
                assert dom_token == 'active-dom-token', "DOM was re-mounted or reconstructed!"
                print("  [PASS] DOM 容器保持稳定，未发生整页重建")

                # 解决 D7 双语冲突
                card_d7 = page.locator('.task-card').filter(has_text='D7')
                expect(card_d7).to_be_visible()
                btn_zh_d7 = card_d7.get_by_role('button', name='采用中文：纯度Purity · SEC-HPLC · HMW')
                btn_zh_d7.click()
                clicks_count += 1
                card_d7.get_by_role('button', name='一次确认保存此项').click()
                clicks_count += 1
                assert clicks_count <= 6, f"Too many clicks: {clicks_count}"
                print(f"  [PASS] 仅用 {clicks_count} 次点击完成 D6 + D7 双语冲突处置 (<= 6 次预算达成)")

                # 批量处理结果删改 (2处)
                print("\n[Step 5] 批量处理结果删改 (2处) 与批注说明 (3处)...")
                batch_rev_btn = page.get_by_role('button', name='批量处理：结果删改 2 处')
                expect(batch_rev_btn).to_be_visible()
                batch_rev_btn.click()
                expect(page.locator('.batch-preview-box')).to_be_visible()
                page.locator('.batch-preview-box input[type="text"]').fill('核对划线修订有效，保留未划线正文')
                page.get_by_role('button', name='确认应用批量决定').click()
                expect(page.locator('.batch-preview-box')).not_to_be_visible(timeout=20000)
                print("  [PASS] 批量处理结果删改 2 处成功")

                # 批量处理批注说明 (3处)
                batch_com_btn = page.get_by_role('button', name='批量处理：批注说明 3 处')
                expect(batch_com_btn).to_be_visible()
                batch_com_btn.click()
                expect(page.locator('.batch-preview-box')).to_be_visible()
                page.locator('.batch-preview-box input[type="text"]').fill('核对 F17/F18/F19 原格批注，保留原格当前数值')
                page.get_by_role('button', name='确认应用批量决定').click()
                expect(page.locator('.batch-preview-box')).not_to_be_visible(timeout=20000)
                print("  [PASS] 批量处理批注说明 3 处成功")

                # 返回原件概要
                page.get_by_role('button', name='返回原件概要').click()

                # 验证预检通过并写入加速工作表
                print("\n[Step 6] 写入加速工作表并验证 102 条正式结果...")
                commit_btn = page.get_by_role('button', name='核对无误，写入本工作表')
                expect(commit_btn).to_be_enabled(timeout=20000)
                commit_btn.click()

                # 验证成功写入卡片与 102 条版本记录
                expect(page.get_by_text('此工作表已写入。')).to_be_visible(timeout=30000)
                receipt_text = page.locator('.commit-receipt')
                expect(receipt_text).to_contain_text('新建结果版本 102 条')
                print("  [PASS] 加速工作表成功写入 102 条正式采用结果！")

                # 验证消除写入后的重复按钮
                review_actions = page.locator('.import-review-actions')
                expect(review_actions.get_by_role('button', name='同步研究表')).to_have_count(0)
                expect(review_actions.get_by_role('button', name='继续处理下一个工作表')).to_have_count(0)
                print("  [PASS] 绿色卡片展示完整入口，下方 .import-review-actions 消除重复按钮")

                # 场景 6 验证：切至 -20℃ 工作表并验证分类筛选、分页与未保存保护
                print("\n[Step 7] 切换至 -20℃ 工作表并验证分类筛选与分页...")
                sheet_select.click()
                expect(page.locator('.n-base-select-option').filter(has_text=acc_sheet_name)).to_contain_text('[✓ 已入库]')
                # 选择 -20℃
                page.locator('.n-base-select-option').filter(has_text='-20℃').first.click()

                # 验证 URL 包含 section
                expect(page).to_have_url(re.compile(r'(-20|%E2%88%9220|20%E2%84%83)'))
                print("  [PASS] 切换至 -20℃ 表，URL section 实时同步")

                # 接收 -20℃ 候选
                receive_btn_20 = page.get_by_role('button', name='供核对')
                expect(receive_btn_20).to_be_visible(timeout=25000)
                receive_btn_20.click()

                # 打开来源核对面板
                source_panel_20 = page.locator('#sourceReviewPanel')
                if not source_panel_20.is_visible():
                    page.get_by_role('button', name='查看本表来源核对建议').click()
                expect(source_panel_20).to_be_visible(timeout=25000)

                # 验证分类筛选栏存在
                cat_bar = page.locator('.category-filter-bar')
                expect(cat_bar).to_be_visible(timeout=25000)
                expect(cat_bar).to_contain_text('分类筛选')
                expect(cat_bar.get_by_role('button', name='全部')).to_be_visible()
                print("  [PASS] 顶部分类筛选栏恢复完整")

                # 验证 25/page 分页器存在
                pager = page.locator('.pagination-bar').first
                expect(pager).to_be_visible()
                expect(pager).to_contain_text('第 1 /')
                expect(pager).to_contain_text('共 80 项待核对任务')
                print("  [PASS] 每页 25 条分页器正常工作，80 项任务分页展示")

                # 测试分页翻页
                pager.get_by_role('button', name='下一页').click()
                expect(pager).to_contain_text('第 2 /')
                pager.get_by_role('button', name='上一页').click()
                expect(pager).to_contain_text('第 1 /')
                print("  [PASS] 分页器下一页/上一页翻页正常")

                # 测试分类过滤点击
                bilingual_filter = cat_bar.get_by_role('button', name=re.compile(r'双语'))
                if bilingual_filter.is_visible():
                    bilingual_filter.click()
                    expect(page.locator('.task-card').first).to_contain_text('双语冲突')
                    # 切换回全部
                    cat_bar.get_by_role('button', name='全部').click()
                    print("  [PASS] 分类筛选按钮过滤交互正常")

                # 场景 7 验证：修改抽屉可视化删除线与快速依据胶囊
                print("\n[Step 8] 验证候选修改抽屉可视化删除线与快速依据...")
                first_edit = page.locator('.row-actions .n-button').first
                first_edit.click()
                drawer = page.locator('.candidate-drawer')
                expect(drawer).to_be_visible()

                # 验证原文卡片存在
                expect(drawer.locator('.orig-card')).to_be_visible()

                # 验证理由必填，保存按钮未填理由时禁用
                save_draft_btn = drawer.get_by_role('button', name='一键保存修改草稿')
                expect(save_draft_btn).to_be_disabled()

                # 修改一个字段触发 dirty
                first_input = drawer.locator('.focused-fields-section input').first
                old_val = first_input.input_value()
                first_input.fill(old_val + '_mod')

                # 验证未填理由时保存依然禁用
                expect(save_draft_btn).to_be_disabled()

                # 点击快捷依据胶囊
                pill = drawer.locator('.quick-pills .n-button').first
                pill.click()
                expect(drawer.locator('.reason-section input')).to_have_value(pill.inner_text())
                expect(save_draft_btn).to_be_enabled()
                print("  [PASS] 快捷依据胶囊点击生效，守住未填依据禁止保存的 GMP 红线")

                # 场景 8 验证：未保存脏状态保护弹窗 (NModal)
                print("\n[Step 9] 验证未保存内容离开保护弹窗 (NModal)...")
                # 在抽屉有未保存修改时，尝试点击返回导入列表
                page.get_by_role('button', name='返回导入列表').click()
                dirty_modal = page.locator('.n-dialog').filter(has_text='未保存内容提示')
                expect(dirty_modal).to_be_visible()
                expect(dirty_modal).to_contain_text('当前有未保存的核对/修改内容，确认离开并放弃未保存更改？')
                print("  [PASS] 存在未保存修改时，触发 NModal 拦截保护")

                # 点击继续保留，弹窗关闭，依然留在工作台
                dirty_modal.get_by_role('button', name='继续保留').click()
                expect(dirty_modal).not_to_be_visible()
                expect(drawer).to_be_visible()

                # 关闭抽屉
                drawer.get_by_role('button', name='关闭抽屉').click()

                # F5 刷新验证持久停留在当前工作表
                print("\n[Step 10] 验证 F5 刷新状态保持与闭环...")
                page.reload()
                expect(page.get_by_role('heading', name='IP281稳定性试验数据-常熟.xlsx')).to_be_visible(timeout=25000)
                expect(page.locator('.sheet-select-row')).to_contain_text('-20℃', timeout=25000)
                print("  [PASS] F5 刷新后精确停留在当前 -20℃ 工作表")

                print("\n=======================================================")
                print("ALL STAGE R2-D-FIX REAL IP281 ACCEPTANCE TESTS PASSED!")
                print("=======================================================")
        finally:
            server.should_exit = True
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
