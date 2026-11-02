import argparse
import json
import sys
from pathlib import Path

from .intake import preview_file
from .models import ImportContext, ProjectConfig
from .permissions import Actor, Capability
from .service import Hub
from .verification import verify_bundle


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Incremental stability records and verified Word/Excel reports"
    )
    parser.add_argument("--home", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("preview", "Preview candidates without saving analytical results"),
        ("import", "Legacy trusted import: automatically adopts new slots; not the V4 UI workflow"),
    ):
        ingest = sub.add_parser(name, help=help_text)
        ingest.add_argument("source", type=Path)
        ingest.add_argument("--config", type=Path, required=True)
        ingest.add_argument("--context", type=Path, required=True)
        if name == 'import':
            ingest.add_argument('--legacy-compatibility',action='store_true',help='Explicit historical compatibility only; not V5 worksheet intake')
    for name in ("records", "issues"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
    export = sub.add_parser("export")
    export.add_argument("--project", required=True)
    export.add_argument("--output", type=Path, default=Path("outputs"))
    verify = sub.add_parser("verify")
    verify.add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    def progress(phase, payload):
        print(json.dumps({"phase": phase, **payload}, ensure_ascii=False), file=sys.stderr)

    try:
        if args.command == "verify":
            result = verify_bundle(args.folder)
            emit(result)
            return 0 if result["passed"] else 1
        hub = Hub(args.home, progress)
        if args.command in {"preview", "import"}:
            config = ProjectConfig.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
            context = ImportContext.model_validate_json(args.context.read_text(encoding="utf-8-sig"))
            if args.command == "preview":
                actor = Actor(id="local-cli", display_name="Local CLI", capabilities={Capability.PREVIEW})
                preview = preview_file(hub.store, args.source, config, context, actor=actor)
                emit(preview.model_dump(mode="json"))
                return 0
            result = hub.import_file(args.source, config, context,legacy_compatibility=args.legacy_compatibility)
            emit(result)
            return 2 if result["counts"].get("pending") or result["counts"].get("conflict") else 0
        if args.command == "records":
            emit(hub.store.snapshot(args.project))
        elif args.command == "issues":
            emit(hub.list_issues(args.project))
        elif args.command == "export":
            emit({"output": str(hub.export(args.project, args.output))})
        return 0
    except (ValueError, OSError) as exc:
        emit({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
