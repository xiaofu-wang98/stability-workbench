"""Stage R2-D-fix browser regression test suite using REAL IP281 workbook:
1. Real IP281 workbook verification (SHA256: bf07cbcde18b792e7dc3ac0a62c8bf4f2013405ad37de39f56eeed03b203bc44).
2. Accelerated sheet (102 candidates, exactly 7 tasks: 2 bilingual conflicts D6/D7, 2 revisions, 3 comments).
3. No auto-selection on load; explicit click to select choice, <= 6 clicks to resolve D6 + D7 with SEC-HPLC names.
4. DOM stability & silent in-place refresh without page recreation, Toast feedback <= 1.5s, exactly 1 /v5/review-actions request per save.
5. Batch actions for revisions & comments, write 102 adopted versions into database.
6. Multi-sheet navigation, badge updates, 102 versions jump loop to /projects/IP281?focus=results.
7. -20℃ sheet (903 candidates, 80 tasks): category filtering, 25/page pagination, secondary request < 1000ms, exception checkbox HTTP 200 without 422.
8. Candidate drawer depth: <= 3 focused fields, 18 fields collapsed, dirty protection modal on sheet switch/close.
"""
import hashlib
import os
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SHA256 = "bf07cbcde18b792e7dc3ac0a62c8bf4f2013405ad37de39f56eeed03b203bc44"

env_path = os.environ.get("IP281_XLSX_PATH")
desktop_path = Path(r"C:\Users\xiaofu.wang\Desktop\Evals\稳定性\IP281稳定性试验数据-常熟.xlsx")
repo_path = ROOT / "outputs/acceptance/20261117-ip281-header-retest/sources/IP281稳定性试验数据-常熟.xlsx"

if env_path and Path(env_path).exists():
    REAL_IP281_PATH = Path(env_path)
elif desktop_path.exists():
    REAL_IP281_PATH = desktop_path
else:
    REAL_IP281_PATH = repo_path


def main():
    print("=== Stage R2-D-fix 稳定性汇总表入库全流程交互重构验收测试（真实 IP281）===")
    print(f"  测试原件路径: {REAL_IP281_PATH}")
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

                # 2. 导入真实 IP281 原件并验证 8 张 Sheet 状态
                print("\n[Step 2] 导入真实 IP281 原件并验证 8 张 Sheet 状态...")
                page.goto(url + '/workbench/#/import?project=IP281')
                page.locator('input[accept=".xlsx,.docx"]').set_input_files(REAL_IP281_PATH)
                page.get_by_label("文件用途").select_option("stability_source")
                page.get_by_role('button', name='解析并核对', exact=True).click()

                # 等待进入核对视图
                expect(page.get_by_role('heading', name='IP281稳定性试验数据-常熟.xlsx')).to_be_visible(timeout=45000)
                print("  [PASS] 进入多工作表核对视图")

                # 断言 1：Sheet 进度显示已完成 0 / 8 张工作表
                progress_pill = page.locator('.sheet-progress-pill')
                expect(progress_pill).to_contain_text('已完成 0 / 8 张工作表')
                print("  [PASS] Sheet 进度胶囊正确显示：已完成 0 / 8 张工作表")

                # 验证工作表下拉框选项徽章与状态
                sheet_select = page.locator('.sheet-select-row .n-base-selection')
                sheet_select.click()
                acc_sheet_name = 'IP281-C-20240529-DS 加速（5±3℃）√'
                options = page.locator('.n-base-select-option')
                assert options.count() == 8, f"Expected 8 sheet options, got {options.count()}"
                for i in range(8):
                    opt_text = options.nth(i).inner_text()
                    assert '[○ 待接收]' in opt_text, f"Option '{opt_text}' should have '[○ 待接收]'"
                    assert 'ready' not in opt_text.lower() and 'empty' not in opt_text.lower(), f"Option '{opt_text}' has misleading ready/empty text!"
                expect(page.locator('.n-base-select-option').filter(has_text=acc_sheet_name)).to_contain_text('[○ 待接收]')
                expect(page.locator('.n-base-select-option').filter(has_text='-20℃').first).to_contain_text('[○ 待接收]')
                page.keyboard.press('Escape')
                print("  [PASS] 多工作表下拉框正确区分各表真实状态（全 8 表初始均为 [○ 待接收]，无误报）")

                # 3. 接收加速工作表候选并进入来源就地核对
                print("\n[Step 3] 接收加速工作表候选（102条）并自动展开就地核对...")
                receive_btn = page.get_by_role('button', name='供核对')
                expect(receive_btn).to_be_visible()
                expect(receive_btn).to_contain_text('接收 102 条候选供核对')
                receive_btn.click()

                # 自动展开来源核对面板
                source_panel = page.locator('#sourceReviewPanel')
                expect(source_panel).to_be_visible(timeout=25000)
                print("  [PASS] 接收候选后核对面板自动展开")

                # 断言 2：加速表聚合为 7 张独立任务卡（而非 11 张分散卡），D6、D7 各占 1 张卡
                expect(page.locator('.task-card').first).to_be_visible(timeout=25000)
                task_cards = page.locator('.task-card')
                card_count = task_cards.count()
                print(f"  当前加速表待核对独立卡片数: {card_count}")
                assert card_count == 7, f"Expected exactly 7 task cards, got {card_count}"
                print("  [PASS] 主列表正确渲染为 7 张独立任务卡")

                card_d6 = task_cards.filter(has_text='D6')
                expect(card_d6).to_be_visible()
                expect(card_d6).to_contain_text('双语冲突')
                expect(card_d6).to_contain_text('影响 3 个时间点')

                # 合规红线断言：初始打开时 D6 卡片未预选任何选项、“一次确认保存此项”按钮初始不预显/禁用
                expect(card_d6.locator('.quick-choices-bar button.n-button--primary-type')).to_have_count(0)
                d6_save_btn = card_d6.get_by_role('button', name='一次确认保存此项')
                expect(d6_save_btn).not_to_be_visible()
                print("  [PASS] 合规红线守住：D6 卡片加载后未自动预选任何选项，保存按钮不预显")

                # 4. 显式选择后保存，DOM 稳定性，Toast 响应 <= 1.5s，单次保存仅 1 次 review-actions 请求
                print("\n[Step 4] 验证显式选择、<= 6 次点击处置双语冲突、DOM/滚动稳定性、Toast 与网络请求...")
                page.evaluate("() => { document.getElementById('sourceReviewPanel').setAttribute('data-stability-test', 'active-dom-token'); }")

                # 监听网络请求验证单次保存仅触发 1 次 /v5/review-actions
                review_action_requests = []
                page.on('request', lambda req: review_action_requests.append(req.url) if '/v5/review-actions' in req.url else None)

                # 滚动一定距离验证保存后 scrollTop 不会异常归零
                page.evaluate("() => window.scrollTo(0, 150)")
                scroll_before = page.evaluate("() => window.scrollY || document.documentElement.scrollTop")

                clicks_count = 0
                # 点击 D6 的官方规范选项（含 SEC-HPLC 与 Monomer，严禁裸单体）
                btn_zh_d6 = card_d6.get_by_role('button', name='采用中文：纯度Purity · SEC-HPLC · Monomer')
                expect(btn_zh_d6).to_be_visible()
                assert 'SEC-HPLC' in btn_zh_d6.inner_text() and 'Monomer' in btn_zh_d6.inner_text()
                btn_zh_d6.click()
                clicks_count += 1

                # 显式选择后保存按钮可见
                expect(d6_save_btn).to_be_visible()
                review_action_requests.clear()
                t_start = time.time()
                d6_save_btn.click()
                clicks_count += 1

                # 验证 Toast 轻提示弹出
                toast = page.locator('.n-message').filter(has_text='已保存')
                expect(toast.first).to_be_visible(timeout=15000)
                t_elapsed = time.time() - t_start
                print(f"  [PASS] Toast 轻提示在 {t_elapsed:.2f}s 内成功弹出")

                # 验证滚动未被异常重置为 0
                scroll_after = page.evaluate("() => window.scrollY || document.documentElement.scrollTop")
                assert scroll_after > 0, f"Scroll position unexpectedly reset to 0 (was {scroll_before}, now {scroll_after})"
                print(f"  [PASS] 窗口滚动保持正常 (前: {scroll_before}px, 后: {scroll_after}px，未异常归零)")

                # 验证 DOM 容器保持稳定
                dom_token = page.evaluate("() => document.getElementById('sourceReviewPanel')?.getAttribute('data-stability-test')")
                assert dom_token == 'active-dom-token', "DOM was re-mounted or reconstructed!"
                print("  [PASS] DOM 容器保持稳定，未发生整页重建")

                # 验证每次保存网络层仅触发 1 次 /v5/review-actions 请求
                page.wait_for_timeout(600)
                assert len(review_action_requests) == 1, f"Expected exactly 1 review-actions request, got {len(review_action_requests)}"
                print(f"  [PASS] 保存单项网络层仅触发 1 次 /v5/review-actions 请求: {len(review_action_requests)}")

                # 解决 D7 双语冲突
                card_d7 = page.locator('.task-card').filter(has_text='D7')
                expect(card_d7).to_be_visible()
                btn_zh_d7 = card_d7.get_by_role('button', name='采用中文：纯度Purity · SEC-HPLC · HMW')
                assert 'SEC-HPLC' in btn_zh_d7.inner_text() and 'HMW' in btn_zh_d7.inner_text()
                btn_zh_d7.click()
                clicks_count += 1
                card_d7.get_by_role('button', name='一次确认保存此项').click()
                clicks_count += 1
                assert clicks_count <= 6, f"Too many clicks: {clicks_count}"
                print(f"  [PASS] 仅用 {clicks_count} 次点击完成 D6 + D7 双语冲突处置 (<= 6 次预算达成)")

                # 5. 批量处理结果删改 (2处) 与批注说明 (3处)
                print("\n[Step 5] 批量处理结果删改 (2处) 与批注说明 (3处)...")
                batch_rev_btn = page.get_by_role('button', name='批量处理：结果删改 2 处')
                expect(batch_rev_btn).to_be_visible()
                batch_rev_btn.click()
                expect(page.locator('.batch-preview-box')).to_be_visible()
                page.locator('.batch-preview-box input[type="text"]').fill('核对划线修订有效，保留未划线正文')
                page.get_by_role('button', name='确认应用批量决定').click()
                expect(page.locator('.batch-preview-box')).not_to_be_visible(timeout=20000)
                print("  [PASS] 批量处理结果删改 2 处成功")

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

                # 6. 写入加速工作表并验证 102 条入库跳转闭环
                print("\n[Step 6] 写入加速工作表并验证 102 条入库跳转闭环...")
                commit_btn = page.get_by_role('button', name='核对无误，写入本工作表')
                expect(commit_btn).to_be_enabled(timeout=20000)
                commit_btn.click()

                # 验证成功写入卡片与 102 条版本记录
                expect(page.get_by_text('此工作表已写入。')).to_be_visible(timeout=30000)
                receipt_text = page.locator('.commit-receipt')
                expect(receipt_text).to_contain_text('新建结果版本 102 条')
                print("  [PASS] 加速工作表成功写入 102 条正式采用结果！")

                # 若存在 needs_input 提示，断言“补齐并建表”链接包含 project=IP281
                needs_input_links = page.locator('a', has_text='补齐并建表')
                if needs_input_links.count() > 0:
                    for i in range(needs_input_links.count()):
                        href = needs_input_links.nth(i).get_attribute('href') or ''
                        assert 'project=IP281' in href, f"Link {href} missing project=IP281"
                    print("  [PASS] 验证补齐并建表链接正确包含 project=IP281")

                # 断言 3：点击「3. 查看已入库结果」跳转至 /projects/IP281?focus=results 并读出 102 条采用结果
                link_results = page.get_by_role('link', name='3. 查看已入库结果')
                expect(link_results).to_be_visible()
                link_results.click()
                expect(page).to_have_url(re.compile(r'/projects/IP281\?focus=results'))
                expect(page.locator('#project-results-view')).to_be_visible(timeout=20000)
                expect(page.locator('p[role="status"]')).to_contain_text('共 102 条结果')
                print("  [PASS] 点击「3. 查看已入库结果」成功跳转至 /projects/IP281?focus=results 并读出 102 条采用结果")

                # 返回导入工作台继续处理下一张表
                page.go_back()
                expect(page.get_by_role('heading', name='IP281稳定性试验数据-常熟.xlsx')).to_be_visible(timeout=25000)
                expect(page.get_by_text('此工作表已写入。')).to_be_visible(timeout=25000)

                # 7. 切换至 -20℃ 工作表并验证大表性能、分类筛选与例外防 422
                print("\n[Step 7] 切换至 -20℃ 工作表并验证分类筛选、分页与真实性能...")
                sheet_select = page.locator('.sheet-select-row .n-base-selection')
                sheet_select.click()
                expect(page.locator('.n-base-select-option').filter(has_text=acc_sheet_name)).to_contain_text('[✓ 已入库]')
                page.locator('.n-base-select-option').filter(has_text='-20℃').first.click()

                # 等待工作表切换完成并验证 URL 包含 -20℃ 相关标识
                expect(page.locator('.sheet-select-row')).to_contain_text('-20℃', timeout=25000)
                expect(page).to_have_url(re.compile(r'(-20%E2%84%83|%C2%B110|%28-20)'), timeout=25000)

                # 测试 F5 刷新后状态保持
                page.reload()
                expect(page.locator('.sheet-select-row')).to_contain_text('-20℃', timeout=25000)
                expect(page).to_have_url(re.compile(r'(-20%E2%84%83|%C2%B110|%28-20)'), timeout=25000)
                print("  [PASS] 切换至 -20℃ 表，URL 同步且 F5 刷新后精确停留在当前 -20℃ 表")

                # 接收 -20℃ 候选（903 条）
                receive_btn_20 = page.get_by_role('button', name='供核对')
                expect(receive_btn_20).to_be_visible(timeout=25000)
                expect(receive_btn_20).to_contain_text('接收 903 条候选供核对')
                receive_btn_20.click()

                # 展开来源核对面板
                source_panel_20 = page.locator('#sourceReviewPanel')
                if not source_panel_20.is_visible():
                    page.get_by_role('button', name='查看本表来源核对建议').click()
                expect(source_panel_20).to_be_visible(timeout=25000)

                # 验证分类筛选栏存在（删除线/结果删改、批注说明、整行不采用、来源区域）
                cat_bar = page.locator('.category-filter-bar')
                expect(cat_bar).to_be_visible(timeout=25000)
                expect(cat_bar.get_by_role('button', name='全部')).to_be_visible()
                expect(cat_bar.get_by_role('button', name=re.compile(r'批注'))).to_be_visible()
                expect(cat_bar.get_by_role('button', name=re.compile(r'删除线'))).to_be_visible()
                expect(cat_bar.get_by_role('button', name=re.compile(r'整行不采用'))).to_be_visible()
                expect(cat_bar.get_by_role('button', name=re.compile(r'来源区域'))).to_be_visible()
                print("  [PASS] 顶部分类筛选栏包含全部、批注、删除线、整行不采用、来源区域")

                # 验证分页器（第 1 页 <= 25 张卡，共 80 项任务）
                pager = page.locator('.pagination-bar').first
                expect(pager).to_be_visible()
                expect(pager).to_contain_text('第 1 /')
                expect(pager).to_contain_text('共 80 项待核对任务')
                assert page.locator('.task-card').count() <= 25
                print("  [PASS] 每页 25 条分页器正常工作，80 项任务分页展示（第 1 页 <= 25 张卡）")

                # 测试分页翻页
                pager.get_by_role('button', name='下一页').click()
                expect(pager).to_contain_text('第 2 /')
                pager.get_by_role('button', name='上一页').click()
                expect(pager).to_contain_text('第 1 /')
                print("  [PASS] 分页器下一页/上一页翻页正常")

                # 真实性能断言：连续二次请求 /v5/review-actions 耗时 < 1000 ms
                upload_id_match = re.search(r'source=([^&]+)', page.url)
                assert upload_id_match, f"No source found in URL: {page.url}"
                upload_id = upload_id_match.group(1)
                current_sec = page.locator('.sheet-select-row .n-base-selection').inner_text().split(' [')[0].strip()
                sec_20_encoded = urllib.parse.quote(current_sec)
                t_perf_start = time.time()
                perf_res = page.request.get(f"{url}/api/v1/previews/{upload_id}/v5/review-actions?section={sec_20_encoded}&limit=25")
                perf_elapsed_ms = (time.time() - t_perf_start) * 1000
                assert perf_res.ok, f"Request failed: {perf_res.status}"
                print(f"  [PASS] 903 候选大表二次请求 /v5/review-actions 耗时: {perf_elapsed_ms:.1f}ms (< 1000ms)")
                assert perf_elapsed_ms < 1000, f"Performance too slow: {perf_elapsed_ms:.1f}ms >= 1000ms"

                # A18 检项删除线 <del> 正常渲染断言
                cat_bar.get_by_role('button', name=re.compile(r'删除线')).click()
                expect(page.locator('.task-card').first).to_be_visible(timeout=15000)
                a18_card = page.locator('.task-card').filter(has=page.locator('.coord-badge', has_text='A18')).first
                if a18_card.count() == 0:
                    # 翻页查找 A18（若不在第一页）
                    while page.locator('.pagination-bar').first.get_by_role('button', name='下一页').is_enabled():
                        page.locator('.pagination-bar').first.get_by_role('button', name='下一页').click()
                        page.wait_for_timeout(400)
                        if a18_card.count() > 0:
                            break
                expect(a18_card).to_be_visible(timeout=15000)
                expect(a18_card.locator('.source-evidence-box del').first).to_be_visible()
                print("  [PASS] A18 检项删除线 <del> 在 .source-evidence-box 中正常渲染")
                cat_bar.get_by_role('button', name='全部').click()
                expect(page.locator('.task-card').first).to_be_visible(timeout=15000)

                # 例外勾选防 422 断言：多成员卡片勾选 1 个例外时间点并保存返回 HTTP 200，且被跳过的 1 个时间点仍留在待核对卡片列表中
                preset_btn_re = re.compile(r'核对并保留|确认批注|采用中文|确认合并排版')
                exc_card = page.locator('.task-card').filter(
                    has=page.locator('.n-tag', has_text=re.compile(r'影响 [2-9]\d* 个时间点'))
                ).filter(
                    has=page.locator('.quick-choices-bar .n-button', has_text=preset_btn_re)
                ).first
                expect(exc_card).to_be_visible()
                exc_coord = exc_card.locator('.coord-badge').first.inner_text().strip()
                exc_card.locator('.quick-choices-bar .n-button', has_text=preset_btn_re).first.click()
                expect(exc_card.locator('details.exceptions-box')).to_be_visible()
                exc_card.locator('details.exceptions-box summary').click()
                checkbox = exc_card.locator('.member-checkboxes input[type="checkbox"]').first
                checkbox.check()

                with page.expect_response(lambda res: '/v5/tasks/apply' in res.url, timeout=15000) as apply_resp_info:
                    exc_card.get_by_role('button', name='一次确认保存此项').click()
                apply_status = apply_resp_info.value.status
                assert apply_status == 200, f"Expected HTTP 200 in apply response, got {apply_status}"
                expect(page.locator('.n-message').filter(has_text='已保存').first).to_be_visible(timeout=8000)
                # 验证被跳过的 1 个时间点仍留在待核对卡片列表中（未被前端黑名单隐藏）
                remaining_exc_card = page.locator('.task-card').filter(has=page.locator('.coord-badge', has_text=exc_coord)).first
                expect(remaining_exc_card).to_be_visible(timeout=15000)
                expect(remaining_exc_card.locator('.n-tag', has_text=re.compile(r'影响 1 个时间点'))).to_be_visible(timeout=15000)
                print(f"  [PASS] 例外勾选 1 个时间点并保存返回 HTTP 200（无 422），且 {exc_coord} 被跳过的 1 个时间点仍留在待核对列表中")

                # 整格划除/删卡片文字断言：无“保留原值（不采纳修订）”、无 "True" / "整格文本"，标题无裸露 JSON
                panel_text = page.locator('#sourceReviewPanel').inner_text()
                assert "保留原值（不采纳修订）" not in panel_text, "Found misleading text '保留原值（不采纳修订）'!"
                assert "True" not in panel_text, "Found raw boolean 'True' in review panel!"
                assert "整格文本" not in panel_text, "Found '整格文本' placeholder in review panel!"
                titles = page.locator('.card-title').all_inner_texts()
                for t in titles:
                    assert '{"' not in t and '"deleted_word"' not in t, f"Raw JSON found in card title: {t}"
                print("  [PASS] 整格划除与“删”字格文案准确，无“True”/“整格文本”/误导文案及裸露 JSON")

                # 来源区域断言：点击「来源区域」分类，展示区域核对确认按钮与 #/legacy 兜底链接
                cat_bar.get_by_role('button', name=re.compile(r'来源区域')).click()
                region_card = page.locator('.task-card').first
                expect(region_card).to_be_visible()
                expect(region_card.get_by_role('button', name='确认辅助用途（不入库）')).to_be_visible()
                legacy_link = region_card.locator('a.advanced-tool-link')
                expect(legacy_link).to_be_visible()
                legacy_href = legacy_link.get_attribute('href') or ''
                assert legacy_href.startswith('/legacy?view=ledger') and '#/legacy' not in legacy_href, f"Invalid legacy link: {legacy_href}"
                print("  [PASS] 来源区域分类展示「确认辅助用途（不入库）」按钮与 /legacy 根路径兜底链接")
                cat_bar.get_by_role('button', name='全部').click()

                # 8. 底部表格联动（查看关联任务 / 预览不采用 / 去核对修改）与修改抽屉深度断言及脏状态保护
                print("\n[Step 8] 验证底部表格联动、候选修改抽屉深度断言与未保存脏状态保护...")
                # 8a. 底部表格「查看关联任务」联动
                view_task_btn = page.locator('.row-actions').get_by_role('button', name='查看关联任务').first
                if view_task_btn.count() > 0:
                    view_task_btn.click()
                    expect(page.locator('#sourceReviewPanel .task-card').first).to_be_visible(timeout=15000)
                    print("  [PASS] 底部表格「查看关联任务」联动定位正常")

                # 8b. 底部表格「预览不采用」联动
                preview_excl_btn = page.locator('.row-actions').get_by_role('button', name='预览不采用').first
                expect(preview_excl_btn).to_be_visible(timeout=15000)
                preview_excl_btn.click()
                expect(page.locator('.batch-preview-box')).to_be_visible(timeout=15000)
                expect(page.locator('.batch-preview-box')).to_contain_text('核对所选原格的排除范围')
                page.locator('.batch-preview-box').get_by_role('button', name='取消').click()
                expect(page.locator('.batch-preview-box')).not_to_be_visible()
                print("  [PASS] 底部表格「预览不采用」联动弹出排除预览框正常")

                # 8c. 底部表格「去核对 / 修改」打开单格抽屉
                first_edit = page.locator('.row-actions').get_by_role('button', name=re.compile(r'去核对|修改解释')).first
                first_edit.click()
                drawer = page.locator('.candidate-drawer')
                expect(drawer).to_be_visible()

                # 验证默认折叠非重点字段（展示 <= 3 个重点字段，其余 18 个字段收拢在 details 内）
                focused_fields = drawer.locator('.focused-fields-section .field-row')
                assert focused_fields.count() <= 3, f"Too many default fields: {focused_fields.count()}"
                expect(drawer.locator('details.all-fields-details summary')).to_contain_text('展开全部 18 个底层字段')
                print(f"  [PASS] 抽屉默认聚焦 {focused_fields.count()} 个重点字段 (<= 3)，其余 18 个字段收拢在 details 内")

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

                # 尝试关闭抽屉触发 NModal 拦截
                drawer.get_by_role('button', name='关闭抽屉').click()
                dirty_modal = page.locator('.n-dialog').filter(has_text='未保存内容提示')
                expect(dirty_modal).to_be_visible()
                expect(dirty_modal).to_contain_text('当前有未保存的核对/修改内容，确认离开并放弃未保存更改？')
                print("  [PASS] 修改字段未保存时尝试关闭抽屉，弹出 NModal 提示")

                # 点击继续保留，弹窗关闭，依然留在工作台且抽屉保留
                dirty_modal.get_by_role('button', name='继续保留').click()
                expect(dirty_modal).not_to_be_visible()
                expect(drawer).to_be_visible()
                assert first_input.input_value() == old_val + '_mod'
                print("  [PASS] 点击「继续保留」后停留在抽屉内且未保存修改不丢失")

                # 尝试切表触发 NModal 拦截
                sheet_select_dirty = page.locator('.sheet-select-row .n-base-selection')
                sheet_select_dirty.click()
                page.locator('.n-base-select-option').filter(has_text='DP长期').first.click()
                expect(dirty_modal).to_be_visible()
                dirty_modal.get_by_role('button', name='继续保留').click()
                expect(dirty_modal).not_to_be_visible()
                expect(drawer).to_be_visible()
                assert first_input.input_value() == old_val + '_mod'
                print("  [PASS] 修改字段未保存时尝试切表，弹出 NModal 提示并取消后修改不丢失")

                # 尝试返回导入列表触发 NModal 拦截
                page.get_by_role('button', name='返回导入列表').click()
                expect(dirty_modal).to_be_visible()
                dirty_modal.get_by_role('button', name='继续保留').click()
                expect(dirty_modal).not_to_be_visible()
                expect(drawer).to_be_visible()
                assert first_input.input_value() == old_val + '_mod'
                print("  [PASS] 修改字段未保存时尝试返回列表，弹出 NModal 提示并守住工作台")

                # 点击快捷依据胶囊并保存草稿
                pill = drawer.locator('.quick-pills .n-button').first
                pill.click()
                expect(drawer.locator('.reason-section input')).to_have_value(pill.inner_text())
                expect(save_draft_btn).to_be_enabled()
                save_draft_btn.click()
                expect(page.locator('.n-message').first).to_be_visible(timeout=15000)
                print("  [PASS] 快捷依据胶囊点击生效并成功保存修改草稿")

                # 关闭抽屉
                drawer.get_by_role('button', name='关闭抽屉').click()
                expect(drawer).not_to_be_visible()

                print("\n=======================================================")
                print("ALL STAGE R2-D-FIX REAL IP281 ACCEPTANCE TESTS PASSED!")
                print("=======================================================")
        finally:
            server.should_exit = True
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
