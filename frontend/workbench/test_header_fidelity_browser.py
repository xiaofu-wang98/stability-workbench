"""Verify the three R1.1 research headers through the workbench and downloaded Excel."""
import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path
import re

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

BATCHES = {"IP281": ["IP281DPC20240801"], "IP275": ["IP275DPC20241101", "IP275DPC20241102"]}
HASHES = {
    "IP275": "962a0b6bc355e1eeb42179a368fd2b75aa319ae4769b7a106ed5b477bdeef4a5",
    "IP281": "bf07cbcde18b792e7dc3ac0a62c8bf4f2013405ad37de39f56eeed03b203bc44",
}
LABELS = {
    "product_name": "产品中英文名称",
    "equipment": "设备名称及编号",
    "protocol_number": "稳定性试验方案编号及版本",
    "specification_text": "交付规格",
    "location": "稳定性开展地点",
    "storage_location": "存放位置",
}


def run(page, url, home, out, baseline_exports):
    def read(path):
        response = page.request.get(url + "/api/v1" + path, timeout=90000)
        assert response.ok, response.text()
        return response.json()

    def go(path):
        page.goto(url + "/#/" + path)
        page.wait_for_load_state("networkidle")

    def controlled(project, batches):
        go("deliveries?project=" + project)
        page.locator("label").filter(has_text="文件类型").locator(".n-base-selection").click()
        page.get_by_text("常熟受控版式 Excel", exact=True).last.click()
        select = page.locator("label").filter(has_text="研究表").locator(".n-base-selection")
        for batch in batches:
            option = page.get_by_text(re.compile("^" + re.escape(batch) + " ·")).last
            if not option.is_visible():
                select.click()
            option.click()
        page.get_by_role("heading", name="从已有结果生成").click()
        page.get_by_role("button", name="预览范围").click()
        expect(page.get_by_text(re.compile(f"范围内 {len(batches)} 张研究表"))).to_be_visible()
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/controlled-workbook")) as saved:
            page.get_by_role("button", name="生成文件").click()
        assert saved.value.ok, saved.value.text()
        with page.expect_download() as downloaded:
            page.get_by_role("link", name="下载 Excel", exact=True).click()
        path = out / f"{project}-controlled-R1.1.xlsx"
        downloaded.value.save_as(path)
        page.screenshot(path=out / f"{project}-download.png")
        return path, saved.value.json()["download_url"]

    runtime = read("/runtime")
    assert Path(runtime["data_home"]).resolve() == home.resolve()
    evidence = {"runtime": runtime, "worksheets": {}, "workbooks": {}}

    for project, batches in BATCHES.items():
        sha = HASHES[project]
        source_file = home / "sources" / (sha + ".xlsx")
        assert hashlib.sha256(source_file.read_bytes()).hexdigest() == sha
        summaries = read("/projects/" + project + "/worksheets")
        coords = []
        with closing(load_workbook(source_file, read_only=True, data_only=True)) as original:
            source = original["DP长期"]
            for index, batch in enumerate(batches):
                sheet_id = next(s["id"] for s in summaries if s["batch"] == batch and s["condition"] == "5℃±3℃ · 避光")
                path = "/worksheets/" + sheet_id
                before = read(path)
                go(path.lstrip("/"))
                page.get_by_role("button", name="编辑结果", exact=True).click()
                page.get_by_role("button", name="核对研究表头", exact=True).click()
                page.get_by_text("查看历史表头来源", exact=True).click()
                form = page.locator("form.extra-form")
                if project == "IP281":
                    expected = {field: source[cell].value.strip() for field, cell in
                                (("product_name", "D1"), ("equipment", "D2"),
                                 ("protocol_number", "F2"), ("specification_text", "H1"))}
                    expected["location"] = "常熟工厂"
                    basis = "隔离演练。对照冻结原件 DP长期!D1/D2/F2/H1/J3，确认原文与已勾选地点。"
                    want = ("product_name", "protocol_number", "specification_text")
                else:
                    source_cell = ("G2", "F44")[index]
                    expected = {field: "" for field in LABELS}
                    expected["storage_location"] = source[source_cell].value
                    want = ("storage_location",)
                    basis = f"隔离演练。原件 DP长期!{source_cell} 标为存放位置；旧地点清空，厂区及其他未注明字段待确认。"
                    expect(form.get_by_text("旧计划的地点字段待核对", exact=False)).to_be_visible()
                    expect(form.get_by_text("原表标题，仅供核对", exact=False)).to_be_visible()
                    for key in ("product_name", "equipment", "protocol_number", "specification_text", "location"):
                        expect(form.get_by_label(LABELS[key], exact=True)).to_have_value("")
                        expect(form.get_by_label(LABELS[key], exact=True)).to_have_attribute("placeholder", "待确认")
                for field in want:
                    form.get_by_role("button", name="带入来源" + LABELS[field], exact=True).click()
                for key, value in expected.items():
                    expect(form.get_by_label(LABELS[key], exact=True)).to_have_value(value)
                form.get_by_label("修改依据").fill(basis)
                form.screenshot(path=out / (batch + "-确认依据.png"))
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/header-metadata")) as saved:
                    form.get_by_role("button", name="保存研究表头", exact=True).click()
                assert saved.value.ok, saved.value.text()
                expect(page.get_by_text("已保存研究表头。", exact=True)).to_be_visible()
                page.get_by_role("button", name="结束编辑", exact=True).click()
                page.reload()
                panel = page.get_by_label("稳定性研究信息")
                expect(panel).to_contain_text("Trastuzumab" if project == "IP281" else expected["storage_location"])
                panel.screenshot(path=out / (batch + "-刷新后.png"))
                go(path.lstrip("/"))
                after = read(path)
                assert after["cells"] == before["cells"] and after["identity"] == before["identity"]
                assert all(after.get(field, "") == value for field, value in expected.items())
                evidence["worksheets"][batch] = {
                    "id": sheet_id, "basis": basis, "before_revision": before["revision"], "after_revision": after["revision"],
                    "source_sha256": sha, "confirmed_fields": expected, "adopted": sum(bool(c.get("adopted")) for c in after["cells"]),
                }
                coords.extend(f"{c}{(1, 43)[index] + row}" for c, row in
                              (("D", 0), ("F", 0), ("H", 0), ("B", 1), ("D", 1), ("F", 1), ("H", 1), ("C", 2), ("J", 2), ("K", 2), ("L", 2)))
            path, download_url = controlled(project, batches)
            old = baseline_exports / f"{project}-controlled-R1.xlsx"
            with closing(load_workbook(path)) as book, closing(load_workbook(old)) as baseline:
                grid = book.active
                if project == "IP281":
                    for c in ("D1", "D2", "F2", "H1"):
                        assert grid[c].value == source[c].value.strip(), c
                    assert grid["J3"].value == "☑ 常熟工厂"
                    assert grid["I43"].value == source["I49"].value.strip()
                    assert source["A45"].value.strip() in grid["A13"].value
                else:
                    for start, c in ((1, "G2"), (43, "F44")):
                        assert grid[f"L{start+2}"].value == source[c].value
                        assert grid[f"J{start+2}"].value == "□ 未填写 / Unspecified"
                        for column, delta in (("D", 0), ("H", 0), ("D", 1), ("F", 1)):
                            assert grid[f"{column}{start+delta}"].value is None
                evidence["workbooks"][project] = {
                    "export_id": download_url.split("/")[-2], "filename": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "baseline_sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
                    "headers": {c: {"before": baseline.active[c].value, "after": grid[c].value} for c in dict.fromkeys(coords)},
                }
        (out / "browser-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline-exports", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    assert args.home.resolve() != (Path(__file__).resolve().parents[2] / "data")
    args.out.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as pw:
        with closing(pw.chromium.launch(channel="msedge", headless=True)) as browser:
            page = browser.new_page(accept_downloads=True, viewport={"width": 1500, "height": 1050})
            page.set_default_timeout(45000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                evidence = run(page, f"http://127.0.0.1:{args.port}", args.home, args.out, args.baseline_exports)
                assert not errors, errors
                evidence["status"] = "PASS"
                (args.out / "browser-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")
                print("PASS: browser confirmed and downloaded all three R1.1 research headers")
            except Exception:
                page.screenshot(path=args.out / "failure.png")
                raise


if __name__ == "__main__":
    main()
