"""Exercise the embedded workbook against a temporary local application."""

import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading

import uvicorn
from playwright.sync_api import expect, sync_playwright

from stability_hub.api import create_app
from stability_hub.models import ItemDefinition, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parents[2]


def main():
    config = ProjectConfig(project="WORKSHEET-TEST", items=(
        ItemDefinition(id="appearance", label="外观", result_type="qualitative", method="目视"),
        ItemDefinition(id="purity", label="纯度", unit="%", method="SEC"),
    ))
    actor = actor_for_role("browser", "浏览器验收", Role.TECHNICIAN)
    with TemporaryDirectory() as directory, socket.socket() as listener:
        app = create_app(Store(Path(directory)), [config], actor, frontend=ROOT / "app.html")
        listener.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                cache = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
                executable = next(cache.glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
                browser = pw.chromium.launch(headless=True, executable_path=str(executable))
                ctx = browser.new_context(viewport={"width": 1440, "height": 950}, permissions=["clipboard-read", "clipboard-write"])
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(address) else route.abort())
                page.goto(address + "/?project=WORKSHEET-TEST", wait_until="networkidle")
                expect(page.locator("#workspaceName")).to_contain_text(config.project)
                page.get_by_role("button", name="研究工作表", exact=True).click()
                dialog = page.locator(".business-worksheet-dialog")
                state = dialog.get_by_role("status")
                expect(state).to_have_text("此项目还没有研究工作表。")
                dialog.get_by_role("button", name="新建", exact=True).click()
                fields = {"name": "浏览器长期研究表", "batch": "B01", "presentation": "1mg",
                          "study_id": "PLAN-01", "condition": "5C", "start_date": "2024-01-31", "points": "0M, 12M"}
                for field, value in fields.items():
                    dialog.locator(f'[name="{field}"]').fill(value)
                dialog.locator('[name="items"]').select_option(["appearance", "purity"])
                dialog.get_by_role("button", name="保存计划").click()
                expect(state).to_have_text("只读")
                canvas = dialog.locator('canvas[id^="univer-sheet-main-canvas"]')
                expect(canvas).to_be_visible()
                dialog.get_by_role("button", name="编辑", exact=True).click()
                expect(state).to_have_text("正在编辑")

                # Paste a plain-text Excel-compatible block containing an annotated NA and a comparison.
                box = canvas.bounding_box()
                assert box
                page.mouse.click(box["x"] + 285, box["y"] + 117)
                page.evaluate("value => navigator.clipboard.writeText(value)", 'NA*\t"澄清\n无异物"\n<0.1\t98.20%')
                page.keyboard.press("Control+v")
                expect(state).to_have_text("有未保存的结果改动。")
                page.mouse.move(box["x"] + 16, box["y"] + 140)
                page.mouse.down()
                page.mouse.move(box["x"] + 16, box["y"] + 166, steps=8)
                page.mouse.up()
                page.mouse.move(box["x"] + 400, box["y"] + 11)
                page.mouse.down()
                page.mouse.move(box["x"] + 432, box["y"] + 11, steps=8)
                page.mouse.up()
                dialog.get_by_role("button", name="合并或拆分标题").click()
                dialog.get_by_role("button", name="保存", exact=True).click()
                expect(state).to_have_text("已保存。")
                sheet_id = dialog.locator('[data-ui="select"]').input_value()
                path = address + "/api/v1/worksheets/" + sheet_id
                result = ctx.request.get(path).json()
                assert {cell["raw"] for cell in result["cells"]} == {"NA*", "澄清\n无异物", "<0.1", "98.20%"}, result
                assert result["layout"]["title_merge_to"] == 2
                assert result["layout"]["row_heights"], result["layout"]
                assert "1" in result["layout"]["column_widths"], result["layout"]
                dialog.get_by_role("button", name="关闭", exact=True).click()
                page.reload(wait_until="networkidle")
                page.get_by_role("button", name="研究工作表", exact=True).click()
                dialog.locator('[data-ui="select"]').select_option(sheet_id)
                expect(state).to_have_text("只读")
                expect(dialog.get_by_role("button", name="保存", exact=True)).to_be_disabled()
                assert ctx.request.get(path).json()["layout"]["title_merge_to"] == 2
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    print("PASS: V3 worksheet creation, multiline paste, row/column resize, merge, save, reload and read-only controls.")


if __name__ == "__main__":
    main()
