"""Synthetic boundary cases complement, and never replace, saved real-source evidence."""

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from playwright.sync_api import expect


def run(page, out, report, store, upload):
    book = Workbook()
    names = [f"原表 {i + 1:02d} · 保留名称顺序" for i in range(20)]
    for i, name in enumerate(names):
        s = book.active if not i else book.create_sheet()
        s.title = name
        s.append(
            [
                "Project",
                "Sample type",
                "Batch",
                "Presentation",
                "Study",
                "Study id",
                "Condition",
                "Test item",
                "Method",
                "Time point",
                "Result",
                "Unit",
            ]
        )
        for item, old, new in [("SliceA", "5.5", "5.49"), ("SliceB", "5.4", "5.54")]:
            revision = CellRichText(
                TextBlock(InlineFont(strike=True), old), TextBlock(InlineFont(strike=False), "\n" + new)
            )
            s.append(
                ["V5-UI", "DP", f"B52-{i}", "10mg", "long_term", "S52", "5C", item, "UV", "0M", revision, "%"]
            )
        # Whole item deletion with an active result must remain a distinct exception.
        s.append(
            ["V5-UI", "DP", f"B52-{i}", "10mg", "long_term", "S52", "5C", "Exception", "UV", "0M", 98, "%"]
        )
        s["H4"].font = s["H4"].font.copy(strike=True)
    book.save(out / "v52-directory.xlsx")
    with store.connect() as conn:
        formal_before = conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0]
    upload("v52-directory.xlsx")
    buttons = page.locator('#v52SheetDirectory [data-v5="switchSheet"]')
    assert buttons.evaluate_all("(ns)=>ns.map(n=>n.dataset.id)") == names
    for i, name in enumerate(names):
        buttons.nth(i).click()
        expect(page.locator('#v52SheetDirectory [aria-current="page"]')).to_have_attribute("data-id", name)
    buttons.nth(0).click()
    checkbox = page.locator("#ledgerDataTable [data-select]").first
    checkbox.check()
    buttons.nth(1).click()
    buttons.nth(0).click()
    expect(checkbox).to_be_checked()
    page.locator('[data-v5="batchTasks"]').click()
    form = page.locator("#v5ActiveForm")
    expect(form.locator('[name="extend"]')).not_to_be_checked()
    form.get_by_role("button", name="预览精确范围", exact=True).click()
    expect(page.locator("#v5ScopePreview")).to_contain_text("2 处来源")
    form.locator('[name="extend"]').check()
    for control in form.locator('[name="sheet"]').all():
        if control.get_attribute("value") != names[1]:
            control.uncheck()
    form.get_by_role("button", name="预览精确范围", exact=True).click()
    expect(page.locator("#v5ScopePreview")).to_contain_text("4 处来源")
    assert "5.49" in page.locator("#v5ScopePreview").inner_text()
    assert "5.54" in page.locator("#v5ScopePreview").inner_text()
    page.screenshot(path=str(out / "v52-20-sheets-explicit-scope.png"))
    page.locator("#v5ApplyScope").click()
    expect(page.locator("#v4LedgerReviewMessage")).to_contain_text("本次已处理 4 处来源", timeout=20000)
    assert "已保存修改 2 处" in buttons.nth(0).inner_text()
    assert "已保存修改 2 处" in buttons.nth(1).inner_text()
    assert "已保存修改 0 处" in buttons.nth(2).inner_text()
    expect(page.locator("#v46ConfirmSheet")).to_be_disabled()
    assert any(
        t.get("exception") and t["fields"] == ["test_item"] for t in page.evaluate("window.v5Review.tasks")
    )
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == formal_before
    page.reload(wait_until="networkidle")
    expect(page.locator("#v52SheetDirectory")).to_contain_text("已保存修改 2 处")
    expect(page.locator("#ledgerDataTable [data-select]").first).to_be_checked()
    page.screenshot(path=str(out / "v52-20-sheets-saved-exceptions.png"))
    report["cases"]["V52-directory-cross-sheet"] = (
        "PASS 20 original names/order; one click per sheet; selection reload; explicit two-sheet draft scope; per-cell values; active-result exception; no formal writes"
    )
