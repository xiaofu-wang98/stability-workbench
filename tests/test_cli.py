import json
import os
import subprocess
import sys
from pathlib import Path


def test_installed_cli_import_export_verify_and_duplicate(tmp_path, config, context, xlsx):
    source = xlsx([["Protein content", "3M", "2.10", "mg/ml"]])
    (tmp_path / "project.json").write_text(config.model_dump_json(), encoding="utf8")
    (tmp_path / "context.json").write_text(context.model_dump_json(), encoding="utf8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def call(*args):
        result = subprocess.run(
            [sys.executable, "-m", "stability_hub", "--home", str(tmp_path / "db"), *args],
            capture_output=True,
            text=True,
            encoding="utf8",
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    args = (
        "import",
        str(source),
        "--config",
        str(tmp_path / "project.json"),
        "--context",
        str(tmp_path / "context.json"),
    )
    preview = call("preview", *args[1:])
    assert preview["counts"]["new"] == 1 and not preview["can_commit"]
    assert call("records", "--project", config.project) == []
    assert call(*args,'--legacy-compatibility')["counts"] == {"accepted": 1}
    assert call(*args,'--legacy-compatibility')["duplicate"]
    output = call("export", "--project", config.project, "--output", str(tmp_path / "out"))["output"]
    assert call("verify", output)["passed"]
