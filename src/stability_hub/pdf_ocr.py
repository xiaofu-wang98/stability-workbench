"""Own local OCR subprocesses and preserve page results and their source PDF."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from threading import Lock

from .workflow_common import WorkflowError


class PdfOcr:
    def __init__(self, home, code_root):
        self.directory = Path(home) / "pdf-ocr"
        self.worker = code_root / "scripts" / "ocr_pdf.py"
        self.python = code_root / ".venv-ocr" / "Scripts" / "python.exe"
        self.lock = Lock()
        self.processes = {}

    def _folder(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise WorkflowError("ocr_not_found", "找不到该扫描件。", 404)
        folder = self.directory / identifier
        if not (folder / "source.pdf").is_file():
            raise WorkflowError("ocr_not_found", "找不到该扫描件。", 404)
        return folder

    def _status(self, identifier, folder):
        process = self.processes.get(identifier)
        state_file = folder / "state"
        state = state_file.read_text(encoding="ascii") if state_file.exists() else "archived"
        if process is not None and (code := process.poll()) is not None:
            self.processes.pop(identifier)
            state = "completed" if code == 0 else "failed"
            state_file.write_text(state, encoding="ascii")
        elif process is None and state == "running":
            state = "interrupted"
        return state

    def archive(self, data, filename):
        if not data.startswith(b"%PDF-") or len(data) > 20 * 1024 * 1024:
            raise WorkflowError("invalid_pdf", "请选择不超过 20MB 的 PDF 文件。")
        identifier = hashlib.sha256(data).hexdigest()
        with self.lock:
            folder = self.directory / identifier
            folder.mkdir(parents=True, exist_ok=True)
            if not (folder / "source.pdf").exists():
                (folder / "name.json").write_text(json.dumps(Path(filename).name, ensure_ascii=False), encoding="utf-8")
                temporary = folder / "source.tmp"
                temporary.write_bytes(data)
                temporary.replace(folder / "source.pdf")
        return identifier

    def list(self):
        if not self.directory.exists():
            return []
        with self.lock:
            return [{"id": folder.name, "filename": json.loads((folder / "name.json").read_text(encoding="utf-8")),
                     "status": self._status(folder.name, folder)}
                    for folder in sorted(self.directory.iterdir()) if (folder / "source.pdf").is_file()]

    def original(self, identifier):
        folder = self._folder(identifier)
        source = folder / "source.pdf"
        if hashlib.sha256(source.read_bytes()).hexdigest() != identifier:
            raise WorkflowError("source_integrity", "PDF 原件校验失败。", 409)
        return source

    def filename(self, identifier):
        return json.loads((self._folder(identifier) / "name.json").read_text(encoding="utf-8"))

    def start(self, identifier):
        with self.lock:
            folder = self._folder(identifier)
            state = self._status(identifier, folder)
            if state in {"running", "completed"}:
                return {"id": identifier, "status": state}
            if not self.python.is_file():
                raise WorkflowError("ocr_not_installed", "请运行 setup-ocr.cmd 安装本地识别环境。", 503)
            for key in tuple(self.processes):
                if self._status(key, self._folder(key)) == "running":
                    raise WorkflowError("ocr_busy", "另一个扫描件正在识别。", 409)
            source = self.original(identifier)
            output = folder / "pages.jsonl"
            output.unlink(missing_ok=True)
            with (folder / "worker.log").open("wb") as log:
                process = subprocess.Popen([str(self.python), str(self.worker), str(source), str(output)],
                                           stdout=log, stderr=log,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.processes[identifier] = process
            (folder / "state").write_text("running", encoding="ascii")
            return {"id": identifier, "status": "running"}

    def read(self, identifier):
        with self.lock:
            folder = self._folder(identifier)
            state = self._status(identifier, folder)
            pages = self._pages(folder)
            for page in pages:
                page["review"] = self._review(folder, page["page"])
            return {"id": identifier, "status": state, "pages": pages,
                    "filename": json.loads((folder / "name.json").read_text(encoding="utf-8"))}

    @staticmethod
    def _pages(folder):
        path = folder / "pages.jsonl"
        return [json.loads(line) for line in path.read_bytes().splitlines(keepends=True)
                if line.endswith(b"\n")] if path.is_file() else []

    @staticmethod
    def _review(folder, number):
        path = folder / f"review-{number}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"revision": 0, "lines": {}}

    def save_review(self, identifier, number, revision, lines):
        with self.lock:
            folder = self._folder(identifier)
            if self._status(identifier, folder) != "completed":
                raise WorkflowError("ocr_review_unavailable", "识别完成后才能保存核对结果。", 409)
            page = next((page for page in self._pages(folder) if page["page"] == number), None)
            if page is None:
                raise WorkflowError("ocr_page_missing", "找不到对应的识别页。", 404)
            current = self._review(folder, number)
            if revision != current["revision"]:
                raise WorkflowError("ocr_review_changed", "此页核对内容已更新，请重新读取。", 409)
            if any(index < 0 or index >= len(page["lines"]) for index in lines):
                raise WorkflowError("ocr_line_missing", "选择的文字行不存在。")
            reviewed = {index: value for index, value in lines.items() if value != page["lines"][index]["text"]}
            result = {"revision": revision + 1, "lines": reviewed}
            temporary = folder / f"review-{number}.tmp"
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            temporary.replace(folder / f"review-{number}.json")
            return result

    def cancel(self, identifier):
        with self.lock:
            folder = self._folder(identifier)
            if self._status(identifier, folder) == "running":
                process = self.processes.pop(identifier)
                process.kill()
                process.wait()
                (folder / "state").write_text("cancelled", encoding="ascii")
            return {"id": identifier, "status": self._status(identifier, folder)}

    def close(self):
        with self.lock:
            for identifier in tuple(self.processes):
                folder = self._folder(identifier)
                if self._status(identifier, folder) == "running":
                    process = self.processes.pop(identifier)
                    process.kill()
                    process.wait()
                    (folder / "state").write_text("interrupted", encoding="ascii")
