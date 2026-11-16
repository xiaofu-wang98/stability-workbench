"""Run from the repository root; no HTTP server or Node dependency needed."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from stability_hub.contracts import ContractSchema
from stability_hub.workflow_contracts import WorkflowSchema
from stability_hub.v5_contracts import V5Schema


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "contracts/v1/schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / 'v5-schema.json').write_text(
        json.dumps(V5Schema.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    target.write_text(
        json.dumps(ContractSchema.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target.parent / "workflow-schema.json").write_text(
        json.dumps(WorkflowSchema.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    from stability_hub.source_contracts import SourcePackage
    (target.parent / 'source-import-schema.json').write_text(
        json.dumps(SourcePackage.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    from stability_hub.api import create_app
    from stability_hub.permissions import Role, actor_for_role
    from stability_hub.storage import Store
    with TemporaryDirectory() as folder:
        app = create_app(Store(Path(folder)), [], actor_for_role('schema', 'Schema generator', Role.TECHNICIAN))
        (target.parent / 'openapi.json').write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == "__main__":
    main()
