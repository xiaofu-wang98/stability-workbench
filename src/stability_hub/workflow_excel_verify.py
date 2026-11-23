"""Re-read Excel identities, coverage, state/style and original evidence independently."""

import hashlib
import json
import posixpath
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils import get_column_letter
from lxml import etree

from .mapping import map_corrected
from .models import ImportContext, ProjectConfig, RawObservation, RecordKey, StabilityRecord, digest
from .verification import check_original_fields, expected_text, reread_source_rows


def value_text(version):
    if version is None:
        return ""
    result = json.loads(version["record_json"])["result"]
    text = expected_text(result).strip()
    if result["status"] == "numeric" and result["unit"]:
        text += "%" if result["unit"] == "%" else " " + result["unit"]
    if version["state"] == "pending":
        text += "（待确认）"
    return text


def original_numeric_cells(path):
    """Independent OOXML style/value evidence; never call the intake adapter."""
    def resolve(base,target):
        return target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(base),target))
    ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    with ZipFile(path) as archive:
        def read(part):
            return etree.fromstring(archive.read(part),etree.XMLParser(resolve_entities=False,no_network=True))
        rels = {r.get('Id'):r.get('Target') for r in read('xl/_rels/workbook.xml.rels')}
        styles = read('xl/styles.xml')
        custom = {int(n.get('numFmtId')):n.get('formatCode') for n in styles.findall('.//{*}numFmt')}
        formats = [custom.get(int(x.get('numFmtId','0')),BUILTIN_FORMATS.get(int(x.get('numFmtId','0'))))
                   for x in styles.find('{*}cellXfs')]
        result = {}
        for sheet in read('xl/workbook.xml').findall('.//{*}sheet'):
            part = resolve('xl/workbook.xml',rels[sheet.get(ns+'id')])
            for cell in read(part).findall('.//{*}c'):
                value = cell.find('{*}v')
                if value is not None:
                    result[(sheet.get('name'),cell.get('r'))] = {'value':value.text,'type':cell.get('t','n'),
                        'format':formats[int(cell.get('s','0'))],'formula':cell.find('{*}f') is not None}
        return result


def same_excel_number_literal(original, extracted):
    """Match exact decimals or the numeric reader's canonical binary64 spelling.

    Excel may serialize 0.017 as 1.7000000000000001E-2. This is not a
    tolerance: a different representable number or an integer change still fails.
    """
    from decimal import Decimal
    if Decimal(original) == Decimal(extracted):
        return True
    return (any(c in original for c in '.Ee') and
            Decimal(str(float(original))) == Decimal(extracted))


def verify_workbook(path, snapshot, *, conn=None):
    errors = []
    def associated(record, link):
        key = RecordKey.model_validate(link['key'])
        changed = {k for k,v in record.key.model_dump().items() if key.model_dump()[k] != v}
        if (changed - {'item','method','method_version','presentation','study_id'} or not link.get('actor_id') or
                not link.get('reason') or not link.get('fingerprint') or link.get('event_sequence',0) <= 0):
            errors.append('Invalid explicit identity association')
        return record.model_copy(update={'key':key})
    if digest({k: v for k, v in snapshot.items() if k != "snapshot_id"}) != snapshot["snapshot_id"]:
        errors.append("Snapshot hash differs")
    expected = {}
    sources = {}
    for entry in snapshot["entries"]:
        current, adopted = entry["display"], entry["adopted"]
        record = StabilityRecord.model_validate_json(current["record_json"])
        sid = record.key.slot_id
        if sid != current["slot_id"] or sid in expected:
            errors.append("Invalid/duplicate snapshot identity")
        if adopted and (adopted["slot_id"] != sid or adopted["state"] not in {"confirmed", "legacy"}):
            errors.append("Invalid adopted identity/state")
        expected[sid] = (current, adopted, record)
        for version in (current, adopted):
            if version:
                for source in json.loads(version["source_json"]):
                    sources[source["source_id"]] = source
    original = {}
    numeric_originals = {}
    for sid, (current, adopted, _) in expected.items():
        for version in (current, adopted):
            if version is None:
                continue
            record = StabilityRecord.model_validate_json(version["record_json"])
            physical = StabilityRecord.model_validate_json(version.get('original_record_json',version['record_json']))
            if version.get('identity_association'):
                if (physical.key.slot_id != version.get('physical_slot_id') or
                        associated(physical,version['identity_association']) != record):
                    errors.append('Historical identity overlay differs')
            for source in json.loads(version["source_json"]):
                try:
                    if source.get("kind") == "worksheet_entry":
                        from .normalize import normalize_worksheet_result
                        if normalize_worksheet_result(source["raw_text"], source["unit"], source["result_type"]) != physical.result:
                            errors.append("Worksheet result differs: " + sid)
                        continue
                    file = Path(source["stored_path"])
                    if file not in original:
                        original[file] = (hashlib.sha256(file.read_bytes()).hexdigest(), reread_source_rows(file, archive_only=bool(source.get('raw_json', {}).get('evidence'))))
                    sha, tables = original[file]
                    if sha != source["document_hash"]:
                        errors.append("Original source hash differs")
                    raw = RawObservation.model_validate(source["raw_json"])
                    if not check_original_fields(tables, raw):
                        errors.append("Original source fields/location differ")
                    if raw.result_anchor.get('basis'):
                        anchor=raw.result_anchor
                        original_text=raw.revisions.get('result',{}).get('active_text',raw.fields.get('result','')) if anchor.get('text_basis')=='active_revision' else raw.fields.get('result','')
                        lines=original_text.splitlines()
                        selected=original_text[anchor['start_char']:anchor['end_char']] if 'start_char' in anchor else '\n'.join(line for line in lines[anchor['start_line']:anchor['end_line']] if line.strip())
                        if selected != anchor['text'] or anchor['source'] != raw.location.model_dump(mode='json') or anchor['basis'] != raw.reporting_basis:
                            errors.append('Reported result source anchor differs')
                    from .review_issues import resolved_raw
                    interpreted_raw, _ = resolved_raw(None,source['source_id'],raw,source.get('issue_decisions',[]))
                    derived = map_corrected(interpreted_raw, ImportContext.model_validate(source["context_json"]),
                                            ProjectConfig.model_validate(source["config_json"]),
                                            source.get("corrections", {}), source.get("acknowledge_warning", False), source_facts=True,
                                            source_scope=source.get('identity_scope',''), conn=conn)
                    if not physical.result.interpretation and not raw.source_value:
                        # Old immutable versions predate format provenance. Compare
                        # their full original payload, without inventing new history.
                        derived = derived.model_copy(update={'result':derived.result.model_copy(update={'interpretation':{}})})
                    if source.get('identity_association'):
                        derived = associated(derived,source['identity_association'])
                    if derived != physical:
                        errors.append("Source interpretation differs: " + sid)
                    # Independent arithmetic check: do not validate the 100x conversion
                    # merely by calling the same mapping function twice.
                    if record.result.interpretation.get('rule') == 'numeric_percent':
                        from decimal import Decimal
                        if file not in numeric_originals:
                            numeric_originals[file] = original_numeric_cells(file)
                        coordinate = get_column_letter(raw.location.column)+str(raw.location.row)
                        actual = numeric_originals[file].get((raw.location.section,coordinate))
                        if (not actual or actual['type']!='n' or actual['formula'] or actual['format']!=raw.number_format
                                or not same_excel_number_literal(actual['value'],raw.fields['result'])):
                            errors.append('Original percentage format/value differs: ' + sid)
                        if (record.result.unit != '%' or
                                record.result.value != Decimal(raw.fields['result']) * Decimal('100') or
                                record.result.interpretation.get('factor') != '100'):
                            errors.append('Percentage conversion basis differs: ' + sid)
                except (OSError, ValueError, KeyError) as exc:
                    errors.append("Source verification failed: " + str(exc))
    book = load_workbook(path, data_only=False)
    try:
        def check_cell(cell, text, pending):
            if str(cell.value or "") != text:
                errors.append(f"Value mismatch: {cell.parent.title}!{cell.coordinate}")
            is_red = cell.font.color is not None and cell.font.color.type == "rgb" and cell.font.color.rgb == "FFDC2626"
            if bool(pending) != is_red:
                errors.append(f"Pending style mismatch: {cell.parent.title}!{cell.coordinate}")

        if not {"Export", "Records", "Sources", "Unsubmitted"} <= set(book.sheetnames):
            return errors + ["Missing required sheets"]
        for sheet in book:
            for row in sheet:
                if any(cell.data_type == "f" for cell in row):
                    errors.append("Unexpected executable Excel formula")
        metadata = {row[0].value: row[1].value for row in book["Export"] if len(row) > 1}
        if metadata.get("snapshot_id") != snapshot["snapshot_id"] or metadata.get("revision") != str(snapshot["revision"]):
            errors.append("Export revision metadata differs")
        if (metadata.get('project') != snapshot['project'] or metadata.get('orientation') != snapshot['orientation']
                or json.loads(metadata.get('filters', '{}')) != snapshot['filters']):
            errors.append('Export scope differs')
        rows = list(book["Records"].iter_rows(min_row=2))
        seen = set()
        for row in rows:
            sid = row[0].value
            if sid not in expected or sid in seen:
                errors.append("Records identity/coverage differs")
                continue
            seen.add(sid)
            current, adopted, record = expected[sid]
            key_fields = ("project", "sample_type", "batch", "presentation", "study", "study_id", "condition", "month", "item", "method", "method_version")
            if [str(c.value or "") for c in row[1:12]] != [(record.key.time_label if record.key.day is not None else str(record.key.month) if record.key.month is not None else "") if f == "month" else str(getattr(record.key, f)) for f in key_fields]:
                errors.append("Records key fields differ")
            check_cell(row[12], value_text(current), current["state"] == "pending")
            check_cell(row[15], value_text(adopted), False)
            if row[13].value != current["id"] or (row[16].value or "") != (adopted["id"] if adopted else "") or row[14].value != current["state"]:
                errors.append("Records version/state differs")
            result = record.result
            source_ids = ';'.join(s['source_id'] for s in json.loads(current['source_json']))
            audit = [result.raw_text, str(result.value) if result.value is not None else '', result.comparator or '',
                     result.unit, current['detection_id'], str(current['upload_sequence']), source_ids]
            if [str(c.value or '') for c in row[17:24]] != audit:
                errors.append('Records raw result/identity/source differs')
            if len(row) < 26 or row[25].value != record.key.purpose:
                errors.append("Records purpose differs")
            if len(row)<28 or (row[26].value or "") != record.key.reporting_basis or json.loads(row[27].value or "[]") != [s.model_dump(mode="json") for s in record.standards]:
                errors.append("Records standard association differs")
            if row[24].value is not None:
                from decimal import Decimal
                if result.comparator != 'eq' or Decimal(str(row[24].value)) != result.value:
                    errors.append('Numeric convenience value differs')
        if seen != set(expected):
            errors.append("Records missing results")
        matrix_seen = {False: set(), True: set()}
        for sheet in book:
            if not sheet.title.startswith(("Display", "Adopted")):
                continue
            adopted_view = sheet.title.startswith("Adopted")
            layout_row = next(r[0].row for r in sheet if r[0].value == "layout")
            meta = {sheet.cell(r, 1).value: sheet.cell(r, 2).value for r in range(1, layout_row)}
            layout = sheet.cell(layout_row, 2).value
            header_row = layout_row+1
            for row in sheet.iter_rows(min_row=header_row+1):
                if layout == "stability":
                    batch, item, method, version, unit = [c.value for c in row[:5]]
                    entries = [(ci, batch, item, method, version, unit, str(sheet.cell(header_row, ci).value)) for ci in range(6, sheet.max_column+1)]
                elif layout == "release_items":
                    item, method, version, unit = [c.value or "" for c in row[:4]]
                    entries = [(ci, sheet.cell(header_row, ci).value, item, method, version, unit, None) for ci in range(5, sheet.max_column+1)]
                elif layout == "release_batches":
                    entries = [(ci, row[0].value, *json.loads(sheet.cell(header_row, ci).value), None) for ci in range(2, sheet.max_column+1)]
                else:
                    errors.append("Unknown matrix layout")
                    continue
                for ci, batch, item, method, version, unit, month in entries:
                    day = int(month[:-1]) if isinstance(month, str) and month.endswith("D") else None
                    month = int(month[:-1]) if isinstance(month, str) and month.endswith("M") else None
                    key = RecordKey(**meta, batch=batch, item=item, method=method, method_version=version, month=month, day=day)
                    sid = key.slot_id
                    cell = row[ci-1]
                    if sid not in expected:
                        if cell.value:
                            errors.append("Unexpected populated matrix cell")
                        continue
                    if sid in matrix_seen[adopted_view]:
                        errors.append("Duplicate matrix identity")
                    matrix_seen[adopted_view].add(sid)
                    current, adopted, record = expected[sid]
                    if (unit or "") != record.result.unit:
                        errors.append("Matrix unit differs")
                    value = adopted if adopted_view else current
                    check_cell(cell, value_text(value), value is not None and value["state"] == "pending")
        if any(seen != set(expected) for seen in matrix_seen.values()):
            errors.append("Matrix coverage differs")
        actual_sources = {row[0].value: row for row in book["Sources"].iter_rows(min_row=2)}
        direct_sources = {sid: source for sid, source in sources.items() if source.get("kind") == "worksheet_entry"}
        if set(actual_sources) != set(sources) - set(direct_sources):
            errors.append("Source coverage differs")
        expected_direct = {tuple(str(source[key]) for key in
                            ("source_id", "worksheet_id", "row_id", "point_id", "plan_version", "raw_text", "unit", "result_type"))
                           for source in direct_sources.values()}
        actual_direct = [tuple(str(value) if value is not None else "" for value in row)
                         for row in book["WorksheetEntries"].iter_rows(min_row=2, values_only=True)] if "WorksheetEntries" in book else []
        if set(actual_direct) != expected_direct or len(actual_direct) != len(expected_direct):
            errors.append("Worksheet entry coverage differs")
        for sid, source in sources.items():
            if sid in actual_sources:
                row = actual_sources[sid]
                if row[1].value != source["filename"] or row[2].value != source["document_hash"] or json.loads(row[3].value) != source["raw_json"]["location"]:
                    errors.append("Source reference differs")
                if json.loads(row[4].value) != source['raw_json']['fields'] or json.loads(row[5].value) != source.get('corrections', {}):
                    errors.append('Source raw fields/corrections differ')
                if len(row) < 7 or json.loads(row[6].value or '{}') != source.get('identity_association',{}):
                    errors.append('Source identity association differs')
        expected_links = {}
        for entry in snapshot['entries']:
            for version in (entry['display'],entry['adopted']):
                if version and version.get('identity_association'):
                    link = version['identity_association']
                    expected_links[version['id']] = (version['id'],version['physical_slot_id'],version['slot_id'],str(link['event_sequence']),
                        link['actor_id'],link['reason'],json.loads(version['original_record_json'])['key'],link['key'])
        if expected_links or 'IdentityLinks' in book:
            actual_links = {}
            if 'IdentityLinks' in book:
                for row in book['IdentityLinks'].iter_rows(min_row=2,values_only=True):
                    if row[0] in actual_links:
                        errors.append('Duplicate identity association output')
                    actual_links[row[0]] = (*row[:3],str(row[3]),*row[4:6],json.loads(row[6]),json.loads(row[7]))
            if actual_links != expected_links:
                errors.append('Identity association output differs')
        drafts = {row[0].value: row for row in book['Unsubmitted'].iter_rows(min_row=2)}
        if snapshot.get('pending_source_regions'):
            if 'SourceStatus' not in book:
                errors.append('Missing unresolved worksheet status')
            else:
                actual = [(r[0],r[1],r[2],json.loads(''.join(str(v) for v in r[4:] if v is not None)))
                          for r in book['SourceStatus'].iter_rows(min_row=2,values_only=True)]
                wanted = [(s['upload_id'],s['filename'],s['section'],s['regions']) for s in snapshot['pending_source_regions']]
                if actual != wanted:
                    errors.append('Unresolved worksheet status differs')
        if set(drafts) != {d['id'] for d in snapshot['drafts']}:
            errors.append('Unsubmitted coverage differs')
        for draft in snapshot['drafts']:
            if draft['id'] in drafts:
                row = drafts[draft['id']]
                if row[1].value != draft['filename'] or row[3].value != draft['raw_json'] or row[4].value != draft['corrections_json']:
                    errors.append('Unsubmitted data differs')
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        errors.append("Workbook structure invalid: " + str(exc))
    finally:
        book.close()
    return errors
