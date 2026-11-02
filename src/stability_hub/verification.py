"""Independent Office readback: XML readers; no writer/planner imports."""

import hashlib
import json
import posixpath
from decimal import InvalidOperation
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from lxml import etree
from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, from_excel, from_ISO8601

from .models import ImportContext, ProjectConfig, RawObservation, StabilityRecord, digest
from .mapping import map_observation
from .parsing import HEADER_ALIASES  # vocabulary only, not parsing or cell-mapping functions

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def xml(data):
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(n.itertext(S + "t")) for n in xml(archive.read("xl/sharedStrings.xml"))]
        rels = {n.get("Id"): n.get("Target") for n in xml(archive.read("xl/_rels/workbook.xml.rels"))}
        sheets = {}
        for sheet in xml(archive.read("xl/workbook.xml")).find(S + "sheets"):
            target = rels[sheet.get(R + "id")]
            name = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            root = xml(archive.read(name))
            rows = []
            for row in root.findall(".//" + S + "sheetData/" + S + "row"):
                ri = int(row.get("r"))
                while len(rows) < ri:
                    rows.append([])
                for cell in row.findall(S + "c"):
                    if cell.find(S + "f") is not None:
                        raise ValueError("Unexpected formula in generated report")
                    letters = "".join(c for c in cell.get("r") if c.isalpha())
                    ci = 0
                    for letter in letters:
                        ci = ci * 26 + ord(letter.upper()) - 64
                    while len(rows[ri - 1]) < ci:
                        rows[ri - 1].append("")
                    v = cell.findtext(S + "v", "")
                    if cell.get("t") == "s":
                        v = strings[int(v)]
                    elif cell.get("t") == "inlineStr":
                        v = "".join(cell.itertext(S + "t"))
                    rows[ri - 1][ci - 1] = v
            sheets[sheet.get("name")] = rows
        return sheets


def read_docx(path: Path) -> list[list[list[str]]]:
    with ZipFile(path) as archive:
        root = xml(archive.read("word/document.xml"))
    return [
        [
            ["".join(cell.itertext(W + "t")) for cell in row.findall(W + "tc")]
            for row in table.findall(W + "tr")
        ]
        for table in root.find(W + "body").findall(W + "tbl")
    ]


def expected_text(result: dict) -> str:
    if result["status"] == "numeric":
        prefixes = {"eq": "", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
        return prefixes[result["comparator"]] + result["value"]
    if result["status"] in ("na", "nd", "complies"):
        return {"na": "N/A", "nd": "ND", "complies": "Complies"}[result["status"]]
    return result["raw_text"]


def reread_source_rows(path: Path, archive_only=False):
    """Separate source read path: no invocation of the ingestion parser."""
    if path.suffix.lower() == ".docx":
        doc = None if archive_only else Document(path)
        result = {
            f"table {i}": [
                (ri, [c.text.replace("\n", "") for c in row.cells]) for ri, row in enumerate(table.rows, 1)
            ]
            for i, table in enumerate(doc.tables if doc is not None else [], 1)
        }
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.startswith('word/') or not name.endswith('.xml'):
                    continue
                for ti, table in enumerate(xml(archive.read(name)).iter(W + 'tbl'), 1):
                    rows = []
                    for ri, row in enumerate(table.findall(W + 'tr'), 1):
                        values = []
                        for cell in row.findall(W + 'tc'):
                            tokens = []
                            for node in cell.iter():
                                if node.tag in (W+'t', '{http://schemas.openxmlformats.org/officeDocument/2006/math}t'):
                                    tokens.append(node.text or '')
                                elif node.tag == W+'tab':
                                    tokens.append('\t')
                                elif node.tag in (W+'br', W+'cr') or (node.tag == W+'p' and tokens):
                                    tokens.append('\n')
                            values.append(''.join(tokens))
                        rows.append((ri, values))
                    result[f'ooxml:{name}#{ti}'] = rows
        return result
    with ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.itertext(S+'t')) for n in xml(archive.read('xl/sharedStrings.xml'))]
        rels = {n.get('Id'): n.get('Target') for n in xml(archive.read('xl/_rels/workbook.xml.rels'))}
        style_root = xml(archive.read('xl/styles.xml')) if 'xl/styles.xml' in archive.namelist() else None
        formats = {int(n.get('numFmtId')): n.get('formatCode') for n in (style_root.findall(S+'numFmts/'+S+'numFmt') if style_root is not None else [])}
        date_styles = {i for i, n in enumerate(style_root.find(S+'cellXfs') if style_root is not None else [])
                       if is_date_format(formats.get(int(n.get('numFmtId', '0')), BUILTIN_FORMATS.get(int(n.get('numFmtId', '0')), '')))}
        properties = xml(archive.read('xl/workbook.xml')).find(S+'workbookPr')
        epoch = MAC_EPOCH if properties is not None and properties.get('date1904') in {'1', 'true'} else WINDOWS_EPOCH
        sheets = {}
        for sheet in xml(archive.read('xl/workbook.xml')).find(S+'sheets'):
            target = rels[sheet.get(R+'id')]
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            rows = []
            for row in xml(archive.read(part)).findall('.//'+S+'sheetData/'+S+'row'):
                values = {}
                for cell in row.findall(S+'c'):
                    letters = ''.join(c for c in cell.get('r') if c.isalpha())
                    col = 0
                    for letter in letters:
                        col = col*26+ord(letter.upper())-64
                    value = cell.findtext(S+'v', '')
                    if cell.find(S+'f') is not None:
                        value = '='+cell.findtext(S+'f', '')
                    elif cell.get('t') == 's':
                        value = strings[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(cell.itertext(S+'t'))
                    elif cell.get('t') == 'd':
                        value = str(from_ISO8601(value))
                    elif int(cell.get('s', '0')) in date_styles and value and cell.get('t') in (None, 'n'):
                        value = str(from_excel(float(value), epoch))
                    elif cell.get('t') == 'b':
                        value = 'True' if value == '1' else 'False'
                    elif cell.get('t') in (None, 'n') and value:
                        # Excel numeric cells store binary64 lexical forms. Match the
                        # shortest round-trip value, not incidental XML float tails.
                        value = str(float(value)) if any(ch in value for ch in '.eE') else str(int(value))
                    if value != '':
                        values[col] = value
                if values:
                    rows.append((int(row.get('r')), [values.get(c, '') for c in range(1, max(values)+1)]))
            sheets[sheet.get('name')] = rows
        return sheets



def check_original_fields(tables, raw: RawObservation) -> bool:
    if raw.adapter == 'manual_projection':
        try:
            rows = dict(tables[raw.location.section])
            return (raw.evidence.get('result') == raw.location and set(raw.fields) == set(raw.evidence) and
                    all(loc.section == raw.location.section and
                        rows[loc.row][loc.column-1] == raw.fields[name] for name,loc in raw.evidence.items()))
        except (KeyError,IndexError):
            return False
    if raw.adapter == 'stability_matrix':
        if set(raw.fields) != set(raw.evidence) or raw.evidence.get('result') != raw.location:
            return False
        try:
            indexed = {section: dict(rows) for section, rows in tables.items()}
            return all((indexed[loc.section].get(loc.row, [])[loc.column-1]
                        if len(indexed[loc.section].get(loc.row, [])) >= loc.column else '') == raw.fields[name]
                       for name, loc in raw.evidence.items())
        except (KeyError, IndexError):
            return False
    if raw.location.section.startswith('ooxml:'):
        if set(raw.fields) != set(raw.evidence) or raw.evidence.get('result') != raw.location:
            return False
        try:
            return all(tables[loc.section][loc.row-1][1][loc.column-1] == raw.fields[name]
                       for name, loc in raw.evidence.items())
        except (KeyError, IndexError):
            return False
    aliases = {
        " ".join(alias.split()).casefold(): key for key, values in HEADER_ALIASES.items() for alias in values
    }
    metadata = {}
    source_fields = None
    for section, rows in tables.items():
        if not rows:
            continue
        header = {i: aliases.get(" ".join(v.split()).casefold()) for i, v in enumerate(rows[0][1])}
        if {"test_item", "result"} <= set(header.values()):
            if section == raw.location.section:
                for ri, row in rows[1:]:
                    if ri == raw.location.row:
                        if header.get(raw.location.column - 1) != "result":
                            return False
                        source_fields = {key: row[i] for i, key in header.items() if key and i < len(row) and row[i].strip()}
                        source_fields["result"] = row[raw.location.column - 1]
        else:
            for _, row in rows:
                key = aliases.get(" ".join(row[0].split()).casefold())
                if key and len(row) > 1:
                    metadata[key] = row[1]
    return source_fields is not None and {**metadata, **source_fields} == {k:v for k,v in raw.fields.items() if v.strip() or k == "result"}


def _key_from_matrix(meta, header, rows):
    labels = {
        "Project": "project",
        "Sample type": "sample_type",
        "Batch": "batch",
        "Presentation": "presentation",
        "Study": "study",
        "Study ID": "study_id",
        "Condition": "condition",
    }
    if len(meta) != 7 or {r[0] for r in meta} != set(labels):
        raise ValueError("Invalid study metadata")
    base = {labels[row[0]]: row[1] for row in meta}
    if header[:4] != ["Item", "Method", "Version", "Unit"]:
        raise ValueError("Invalid result table header")
    if len(set(header[4:])) != len(header[4:]):
        raise ValueError("Duplicate time columns")
    result = {}
    for row in rows:
        if len(row) != len(header):
            raise ValueError("Result row width changed")
        for index, time in enumerate(header[4:], 4):
            if not row[index]:
                continue
            if time == "Release":
                month = None
            elif time.endswith("M") and time[:-1].isdigit():
                month = int(time[:-1])
            else:
                raise ValueError("Invalid time point")
            key = {**base, "item": row[0], "method": row[1], "method_version": row[2], "month": month}
            sid = digest(key)
            if sid in result:
                raise ValueError("Duplicate result")
            result[sid] = (row[index], row[3])
    return result


def verify_bundle(folder: Path, snapshot: list[dict] | None = None) -> dict:
    errors = []
    try:
        on_disk = json.loads((folder / "records.json").read_text(encoding="utf-8"))
        if snapshot is None:
            snapshot = on_disk
        elif on_disk != snapshot:
            errors.append("records.json differs from selected database snapshot")
        if not snapshot:
            errors.append("Empty export snapshot")
        manifest_path = folder / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("snapshot_id") != digest(on_disk):
                errors.append("Manifest snapshot identity mismatch")
            for name, expected_hash in manifest.get("output_hashes", {}).items():
                if name not in {"stability.docx", "stability.xlsx", "records.json", "verification.json"}:
                    errors.append("Unexpected manifest output filename")
                    continue
                if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected_hash:
                    errors.append(f"Output hash mismatch: {name}")
        else:
            errors.append("Missing export manifest")
        expected = {}
        source_cache = {}
        original_tables = {}
        for entry in snapshot:
            record = StabilityRecord.model_validate(entry["record"])
            if record.key.slot_id != entry["slot_id"] or entry["slot_id"] in expected:
                errors.append("Invalid/duplicate record identity in snapshot")
            expected[entry["slot_id"]] = (expected_text(entry["record"]["result"]), record.result.unit)
            if not entry["sources"]:
                errors.append("Record has no source evidence")
            for source in entry["sources"]:
                path = Path(source["stored_path"])
                if path not in source_cache:
                    source_cache[path] = hashlib.sha256(path.read_bytes()).hexdigest()
                    original_tables[path] = reread_source_rows(path)
                if source_cache[path] != source["document_hash"]:
                    errors.append(f"Source hash mismatch: {source['filename']}")
                raw = RawObservation.model_validate(source["raw_json"])
                if not check_original_fields(original_tables[path], raw):
                    errors.append(
                        f"Original source fields/location differ from stored evidence: {source['filename']}"
                    )
                # Revalidate source interpretation; output readers and matrix binding are independent.
                rederived = map_observation(
                    raw,
                    ImportContext.model_validate(source["context_json"]),
                    ProjectConfig.model_validate(source["config_json"]),
                )
                if rederived.key != record.key or not rederived.result.equivalent(record.result):
                    errors.append(f"Source interpretation mismatch: {entry['slot_id']}")
        tables = read_docx(folder / "stability.docx")
        if len(tables) % 2:
            errors.append("Unexpected number of Word tables")
        actual_word = {}
        for i in range(0, len(tables) - 1, 2):
            data = _key_from_matrix(tables[i], tables[i + 1][0], tables[i + 1][1:])
            if set(data) & set(actual_word):
                errors.append("Duplicate Word records")
            actual_word.update(data)
        if actual_word != expected:
            errors.append("Word values/identities/coverage differ from snapshot")
        sheets = read_xlsx(folder / "stability.xlsx")
        if set(sheets) - {"Records", "Sources"} != {n for n in sheets if n.startswith("Study ")}:
            errors.append("Unexpected worksheet")
        actual_excel = {}
        for name, rows in sheets.items():
            if name.startswith("Study "):
                data = _key_from_matrix([row[:2] for row in rows[:7]], rows[8], rows[9:])
                if set(data) & set(actual_excel):
                    errors.append("Duplicate Excel study records")
                actual_excel.update(data)
        if actual_excel != expected:
            errors.append("Excel study values/identities/coverage differ from snapshot")
        summary = sheets.get("Records", [])
        header = [
            "Project",
            "Sample type",
            "Batch",
            "Presentation",
            "Study",
            "Study ID",
            "Condition",
            "Month",
            "Item",
            "Method",
            "Method version",
            "Result",
            "Status",
            "Value",
            "Comparator",
            "Unit",
            "Reference",
        ]
        if not summary or summary[0] != header or len(summary) != len(snapshot) + 1:
            errors.append("Invalid summary header/record count")
        else:
            by_ref = {e["observation_id"]: e for e in snapshot}
            seen = set()
            fields = [
                "project",
                "sample_type",
                "batch",
                "presentation",
                "study",
                "study_id",
                "condition",
                "month",
                "item",
                "method",
                "method_version",
            ]
            for row in summary[1:]:
                if len(row) != 17 or row[-1] not in by_ref or row[-1] in seen:
                    errors.append("Invalid/duplicate summary reference")
                    continue
                seen.add(row[-1])
                entry = by_ref[row[-1]]
                key, value = entry["record"]["key"], entry["record"]["result"]
                if row[:11] != ["" if key[f] is None else str(key[f]) for f in fields]:
                    errors.append("Summary business identity mismatch")
                want = [
                    expected_text(value),
                    value["status"],
                    value["value"] or "",
                    value["comparator"] or "",
                    value["unit"],
                ]
                if row[11:16] != want:
                    errors.append("Summary result/status/precision mismatch")
        expected_sources = []
        for entry in snapshot:
            for source in entry["sources"]:
                loc = source["raw_json"]["location"]
                expected_sources.append(
                    [
                        entry["observation_id"],
                        source["filename"],
                        source["document_hash"],
                        f"{loc['section']}; row {loc['row']}, column {loc['column']}",
                    ]
                )
        if sheets.get("Sources") != [["Reference", "Source file", "SHA-256", "Location"], *expected_sources]:
            errors.append("Source reference sheet mismatch")
    except (
        ValueError,
        KeyError,
        IndexError,
        OSError,
        InvalidOperation,
        BadZipFile,
        etree.XMLSyntaxError,
    ) as exc:
        errors.append(f"Readback failure: {exc}")
    return {
        "passed": not errors,
        "record_count": len(snapshot or []),
        "errors": errors,
        "checks": [
            "source_hashes",
            "original_source_fields",
            "source_interpretation",
            "word_matrix",
            "excel_matrix",
            "summary_identities_and_results",
            "source_references",
        ],
    }
