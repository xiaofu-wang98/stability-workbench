import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable

from . import __version__
from .mapping import map_observation, prepare_observation
from .models import ImportContext, ProjectConfig, StabilityRecord, digest
from .normalize import NeedsReview
from .parsing import parse_source
from .reporting import write_reports
from .storage import Store
from .verification import verify_bundle

EventSink = Callable[[str, dict], None]


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Hub:
    def __init__(self, home: Path | str, on_event: EventSink | None = None):
        self.store = Store(Path(home))
        self.on_event = on_event or (lambda phase, data: None)

    def import_file(self, path: Path | str, config: ProjectConfig, context: ImportContext, *, legacy_compatibility=False) -> dict:
        path = Path(path)
        if path.suffix.lower() == '.xlsx' and not legacy_compatibility:
            raise ValueError('V5 Excel requires preview and worksheet commit; legacy automatic import is disabled')
        if path.suffix.lower() not in {".xlsx", ".docx"}:
            raise ValueError("Only .xlsx and .docx are supported")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        identity = {
            "sha256": sha,
            "config": config.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "parser_version": "long-table-v1",
        }
        import_id = digest(identity)
        self.on_event("reading", {"file": path.name})
        with self.store.connect() as conn:
            if conn.execute("SELECT 1 FROM workflow_projects WHERE project=?", (config.project,)).fetchone():
                raise ValueError("V4 project requires preview and human confirmation; legacy import is disabled")
            previous = conn.execute(
                "SELECT id, summary_json FROM imports WHERE document_hash=? AND project=?",
                (sha, config.project),
            ).fetchone()
            if previous:
                if previous["id"] != import_id:
                    raise ValueError(
                        "File already imported with different context/config; explicit reprocessing is not yet supported"
                    )
                return {**json.loads(previous["summary_json"]), "duplicate": True}
        frozen = self.store.home / "sources" / f"{sha}{path.suffix.lower()}"
        try:
            with frozen.open("xb") as target:
                target.write(data)
        except FileExistsError:
            if hashlib.sha256(frozen.read_bytes()).hexdigest() != sha:
                raise ValueError("Stored source hash mismatch") from None
        parsed = parse_source(frozen)
        counts: Counter = Counter()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM workflow_projects WHERE project=?", (config.project,)).fetchone():
                raise ValueError("V4 project requires human confirmation; legacy import is disabled")
            # Recheck under the write lock: UI reruns/concurrent invocations must be idempotent.
            prior = conn.execute(
                "SELECT id, summary_json FROM imports WHERE document_hash=? AND project=?",
                (sha, config.project),
            ).fetchone()
            if prior:
                if prior["id"] != import_id:
                    raise ValueError("Concurrent import used a different context/config")
                return {**json.loads(prior["summary_json"]), "duplicate": True}
            conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?)", (sha, path.name, str(frozen)))
            conn.execute(
                "INSERT INTO imports(id,document_hash,project,config_json,context_json,summary_json) VALUES(?,?,?,?,?,?)",
                (import_id, sha, config.project, config.model_dump_json(), context.model_dump_json(), "{}"),
            )
            for raw in parsed.observations:
                raw = prepare_observation(raw, parsed.metadata)
                oid = digest({"import_id": import_id, "location": raw.location.model_dump()})
                record = None
                sid = None
                prior_id = None
                code = None
                try:
                    record = map_observation(raw, context, config, conn=conn)
                    sid = record.key.slot_id
                    if record.result.status in {"blank", "placeholder"}:
                        status, detail = "ignored", "No analytical result; existing history preserved"
                    else:
                        existing = conn.execute(
                            """SELECT o.id,o.record_json FROM effective e
                            JOIN observations o ON o.id=e.observation_id WHERE e.slot_id=?""",
                            (sid,),
                        ).fetchone()
                        if existing is None:
                            status, detail = "accepted", "New result"
                        else:
                            prior_id = existing["id"]
                            old = StabilityRecord.model_validate_json(existing["record_json"])
                            if record.result.equivalent(old.result):
                                status, detail = (
                                    "equivalent",
                                    "Additional source; existing display precision retained",
                                )
                            else:
                                status, code, detail = (
                                    "conflict",
                                    "different_result",
                                    "Existing and incoming results differ; history preserved",
                                )
                except NeedsReview as exc:
                    status, code, detail = "pending", exc.code, str(exc)
                conn.execute(
                    "INSERT INTO observations(id,import_id,raw_json,record_json,slot_id,status,issue_code,detail,prior_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        import_id,
                        raw.model_dump_json(),
                        record.model_dump_json() if record else None,
                        sid,
                        status,
                        code,
                        detail,
                        prior_id,
                    ),
                )
                if status == "accepted":
                    conn.execute("INSERT INTO effective VALUES(?,?)", (sid, oid))
                counts[status] += 1
            summary = {
                "import_id": import_id,
                "source_hash": sha,
                "file": path.name,
                "project": config.project,
                "observations": sum(counts.values()),
                "counts": dict(counts),
                "duplicate": False,
            }
            conn.execute("UPDATE imports SET summary_json=? WHERE id=?", (json.dumps(summary), import_id))
        self.on_event("imported", summary)
        return summary

    def list_issues(self, project: str) -> list[dict]:
        return self.store.issues(project)

    def export(self, project: str, output_root: Path | str) -> Path:
        with self.store.connect() as conn:
            if conn.execute("SELECT 1 FROM workflow_projects WHERE project=?", (project,)).fetchone():
                raise ValueError("V4 project requires workflow Excel export; legacy export is disabled")
        if self.store.issues(project):
            raise ValueError(
                "Project has unresolved observations/conflicts; inspect `issues` before exporting"
            )
        snapshot = self.store.snapshot(project)
        if not snapshot:
            raise ValueError("No accepted records to export")
        snapshot_id = digest(snapshot)
        export_id = uuid.uuid4().hex
        root = Path(output_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{export_id}.staging"
        published = root / export_id
        staging.mkdir()
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO exports(id,project,snapshot_id,state,output_path,detail) VALUES(?,?,?,?,?,?)",
                (export_id, project, snapshot_id, "building", str(staging), ""),
            )
            previous = conn.execute(
                "SELECT output_path FROM exports WHERE project=? AND state='published' ORDER BY rowid DESC LIMIT 1",
                (project,),
            ).fetchone()
            import_summaries = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT summary_json FROM imports WHERE project=? ORDER BY id", (project,)
                )
            ]
            excluded = [
                dict(row)
                for row in conn.execute(
                    """
                SELECT o.id, o.import_id, o.status, o.detail, o.raw_json
                FROM observations o JOIN imports i ON i.id=o.import_id
                WHERE i.project=? AND o.status='ignored' ORDER BY o.id
            """,
                    (project,),
                )
            ]
        before = {}
        if previous:
            old_path = Path(previous["output_path"]) / "records.json"
            if old_path.exists():
                before = {e["slot_id"]: e["record"] for e in json.loads(old_path.read_text(encoding="utf-8"))}
        changes = [
            {
                "slot_id": e["slot_id"],
                "action": "added"
                if e["slot_id"] not in before
                else ("unchanged" if before[e["slot_id"]] == e["record"] else "changed"),
                "before": before.get(e["slot_id"]),
                "after": e["record"],
                "observation_id": e["observation_id"],
            }
            for e in snapshot
        ]
        manifest = {
            "schema_version": 1,
            "software_version": __version__,
            "template_version": "standard-v1",
            "export_id": export_id,
            "project": project,
            "snapshot_id": snapshot_id,
            "state": "building",
            "changes": changes,
            "events": [],
            "output_hashes": {},
            "imports": import_summaries,
            "excluded_observations": excluded,
        }

        def event(phase, **payload):
            manifest["events"].append({"phase": phase, **payload})
            save_json(staging / "manifest.json", manifest)
            self.on_event(phase, payload)

        try:
            save_json(staging / "records.json", snapshot)
            event("writing", records=len(snapshot))
            write_reports(snapshot, staging, project, snapshot_id)
            event("verifying")
            verification = verify_bundle(staging, snapshot)
            save_json(staging / "verification.json", verification)
            if not verification["passed"]:
                raise ValueError("Independent verification failed: " + "; ".join(verification["errors"]))
            # Detect any import completed while the export was being constructed.
            if self.store.issues(project) or digest(self.store.snapshot(project)) != snapshot_id:
                raise ValueError("Project changed during export; retry against the new snapshot")
            manifest["output_hashes"] = {
                name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
                for name in ("stability.docx", "stability.xlsx", "records.json", "verification.json")
            }
            manifest["state"] = "verified"
            event("verified", records=len(snapshot))
            staging.rename(published)
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE exports SET state='published',output_path=? WHERE id=?",
                    (str(published), export_id),
                )
            self.on_event("published", {"path": str(published)})
            return published
        except Exception as exc:
            with self.store.connect() as conn:
                conn.execute("UPDATE exports SET state='failed',detail=? WHERE id=?", (str(exc), export_id))
            if staging.exists():
                manifest["state"] = "failed"
                manifest["error"] = str(exc)
                save_json(staging / "manifest.json", manifest)
            self.on_event("failed", {"error": str(exc)})
            raise
