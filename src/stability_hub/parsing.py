"""Strict v1 long-table adapters. Unsupported structure is never silently skipped."""

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from openpyxl import load_workbook

from .models import Location, RawObservation, digest
from .xlsx_matrix import parse_matrix_sheet, source_cells

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADER_ALIASES = {
    "project": ("project", "项目"),
    "sample_type": ("sample type", "sample_type", "样品类型"),
    "purpose": ("purpose", "用途"),
    "batch": ("batch", "batch no.", "批号"),
    "presentation": ("presentation", "规格/包装"),
    "study": ("study", "study type", "研究类型"),
    "study_id": ("study id", "study_id", "研究编号"),
    "condition": ("condition", "条件"),
    "time_point": ("time point", "time_point", "时间点"),
    "test_item": ("test item", "test_item", "检测项", "检测项目"),
    "result": ("result", "结果", "检测结果"),
    "unit": ("unit", "单位"),
    "method": ("method", "方法"),
    "method_version": ("method version", "method_version", "方法版本"),
    "source_number": ("report number", "report no.", "test number", "检测编号", "报告编号", "原文件编号"),
    "test_date": ("test date", "检测日期", "检测完成日期"),
}
ALIASES = {alias.casefold(): key for key, aliases in HEADER_ALIASES.items() for alias in aliases}


class UnsupportedFormat(ValueError):
    pass


@dataclass
class ParsedDocument:
    metadata: dict[str, str]
    observations: list[RawObservation]
    coverage: tuple[dict, ...] = ()


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def header_key(value: str) -> str | None:
    return ALIASES.get(" ".join(value.split()).casefold())


def identify(rows: list[list[str]], section: str) -> dict[int, str] | None:
    columns = {i: key for i, v in enumerate(rows[0]) if (key := header_key(v))}
    if {"test_item", "result"} <= set(columns.values()):
        if len(set(columns.values())) != len(columns):
            raise UnsupportedFormat(f"Duplicate/merged column labels in {section}")
        if any(v.strip() and i not in columns for i, v in enumerate(rows[0])):
            raise UnsupportedFormat(f"Unknown column in {section}; v1 requires documented headers")
        return columns
    return None


def add_metadata(rows: list[list[str]], metadata: dict, section: str) -> None:
    for row in rows:
        if not any(v.strip() for v in row):
            continue
        if len(row) != 2 or not (key := header_key(row[0])) or key in {"test_item", "result", "unit"}:
            raise UnsupportedFormat(
                f"Unsupported table/sheet {section}; expected result columns or key/value metadata"
            )
        if key in metadata and metadata[key] != row[1]:
            raise UnsupportedFormat(f"Conflicting document metadata: {key}")
        metadata[key] = row[1]


def parse_xlsx(path: Path, *, allow_partial=False) -> ParsedDocument:
    book = load_workbook(path, data_only=False, rich_text=True)
    observations: list[RawObservation] = []
    metadata: dict[str, str] = {}
    coverage = []
    try:
        for sheet in book:
            try:
                matrix = parse_matrix_sheet(sheet)
            except ValueError as exc:
                if not allow_partial:
                    raise
                coverage.append({'section':sheet.title,'adapter':'unrecognized','result_cells':0,'note':str(exc)})
                continue
            if matrix is not None:
                rows, summary = matrix
                observations.extend(rows)
                coverage.append(summary)
                continue
            actual = source_cells(sheet)
            width_bound = max((col for row, col in actual), default=0)
            row_bound = max((row for row, col in actual), default=0)
            if not width_bound:
                coverage.append({'section': sheet.title, 'adapter': 'empty_or_chart', 'result_cells': 0,
                                 'note': '无单元格检测结果；图表及其附件保留在冻结原件。'})
                continue
            if row_bound * width_bound > 200000:
                if allow_partial:
                    coverage.append({'section':sheet.title,'adapter':'unrecognized','result_cells':0})
                    continue
                raise UnsupportedFormat('Sparse sheet requires manual region selection')
            populated = [row for row in sheet.iter_rows(max_row=row_bound, max_col=width_bound) if any(c.value is not None for c in row)]
            if not populated:
                continue
            width = max(i + 1 for row in populated for i, c in enumerate(row) if c.value is not None)
            values = [[cell_text(c.value) for c in row[:width]] for row in populated]
            try:
                columns = identify(values, sheet.title)
            except UnsupportedFormat:
                if not allow_partial:
                    raise
                coverage.append({'section':sheet.title,'adapter':'unrecognized','result_cells':0})
                continue
            if columns is None:
                try:
                    add_metadata(values, metadata, sheet.title)
                    coverage.append({'section':sheet.title,'adapter':'metadata','result_cells':0})
                except UnsupportedFormat:
                    if not allow_partial:
                        raise
                    coverage.append({'section':sheet.title,'adapter':'unrecognized','result_cells':0})
                continue
            coverage.append({'section':sheet.title,'adapter':'long_table','result_cells':len(populated)-1})
            for row, texts in zip(populated[1:], values[1:], strict=True):
                fields = {key: texts[i] for i, key in columns.items()}
                if not fields.get("test_item", "").strip() and not fields.get("result", "").strip():
                    continue
                cell = row[next(i for i, k in columns.items() if k == "result")]
                hazards = []
                if any(c.data_type == "e" for c in row):
                    hazards.append("Excel error cell in observation")
                if isinstance(cell.value, bool):
                    hazards.append("Boolean result is not an analytical number")
                if any(
                    row[i].data_type == "f" or row[i].comment for i, key in columns.items() if key != "result"
                ):
                    hazards.append("Formula/comment in identity or method requires review")
                if any(c.coordinate in area for c in row[:width] for area in sheet.merged_cells.ranges):
                    hazards.append("Merged result/identity cells require a dedicated adapter")
                observations.append(
                    RawObservation(
                        fields=fields,
                        evidence={key: Location(format="xlsx", section=sheet.title, row=row[i].row, column=row[i].column) for i,key in columns.items()},
                        location=Location(
                            format="xlsx", section=sheet.title, row=cell.row, column=cell.column
                        ),
                        comment=cell.comment.text if cell.comment else "",
                        formula=cell_text(cell.value) if cell.data_type == "f" else "",
                        number_format=cell.number_format,
                        hazards=tuple(hazards),
                    )
                )
        from .xlsx_revisions import annotate
        observations = [annotate(raw, book[raw.location.section]) for raw in observations]
        from .numeric_formats import source_value
        observations = [raw.model_copy(update={'source_value': source_value(
            book[raw.location.section].cell(raw.location.row, raw.location.column))}) for raw in observations]
        from .worksheet_regions import inventory
        summaries = {s['section']:s for s in coverage}
        coverage = [inventory(sheet,observations,summaries.get(sheet.title,{'adapter':'empty','result_cells':0})) for sheet in book]
        for chart in book.chartsheets:
            coords = ['chartsheet:'+chart.title]
            coverage.append({'section':chart.title,'adapter':'chart_sheet','result_cells':0,'regions':[
                {'id':digest([chart.title,coords]),'section':chart.title,'coordinates':coords,
                 'hidden':chart.sheet_state!='visible','role':'unclassified','revision':1,
                 'basis':'Chart sheet preserved in original; explicit auxiliary classification required'}]})
        from .worksheet_regions import unsupported_drawings
        drawings = unsupported_drawings(path)
        for summary in coverage:
            summary['regions'].extend(drawings.get(summary['section'],[]))
    finally:
        book.close()
    if not observations and not allow_partial:
        raise UnsupportedFormat("No result observations found")
    project_refs = {o.fields['project']: o.evidence['project'] for o in observations
                    if o.adapter == 'stability_matrix' and o.fields.get('project', '').strip() and 'project' in o.evidence}
    if len(project_refs) == 1:
        # Legacy tabs may omit the workbook's explicit project label. Carry only an
        # unambiguous project, retaining its real source coordinate; never batch/study.
        project, reference = next(iter(project_refs.items()))
        observations = [o.model_copy(update={'fields':{**o.fields,'project':project},
                         'evidence':{**o.evidence,'project':reference}})
                        if o.adapter == 'stability_matrix' and not o.fields.get('project','').strip() else o
                        for o in observations]
    return ParsedDocument(metadata, observations, tuple(coverage))


def parse_docx(path: Path) -> ParsedDocument:
    with ZipFile(path) as archive:
        root = etree.fromstring(
            archive.read("word/document.xml"), parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    metadata: dict[str, str] = {}
    observations: list[RawObservation] = []
    body = root.find(W + "body")
    for ti, table in enumerate(body.findall(W + "tbl"), 1):
        xml_rows = table.findall(W + "tr")
        rows = [["".join(c.itertext(W + "t")) for c in row.findall(W + "tc")] for row in xml_rows]
        if not rows or not any(v.strip() for row in rows for v in row):
            continue
        columns = identify(rows, f"table {ti}")
        if columns is None:
            add_metadata(rows, metadata, f"table {ti}")
            continue
        for ri, (xml_row, texts) in enumerate(zip(xml_rows[1:], rows[1:], strict=True), 2):
            if len(texts) != len(rows[0]):
                raise UnsupportedFormat(f"Nonrectangular table {ti}, row {ri}")
            fields = {key: texts[i] for i, key in columns.items()}
            if not fields.get("test_item", "").strip() and not fields.get("result", "").strip():
                continue
            hazards = []
            if any(
                xml_row.find(".//" + W + tag) is not None
                for tag in (
                    "gridSpan",
                    "vMerge",
                    "ins",
                    "del",
                    "moveFrom",
                    "moveTo",
                    "fldChar",
                    "commentRangeStart",
                )
            ):
                hazards.append("Merged/revised/annotated Word row requires a dedicated adapter")
            observations.append(
                RawObservation(
                    fields=fields,
                    location=Location(
                        format="docx",
                        section=f"table {ti}",
                        row=ri,
                        column=next(i + 1 for i, k in columns.items() if k == "result"),
                    ),
                    hazards=tuple(hazards),
                )
            )
    if not observations:
        raise UnsupportedFormat("No result observations found")
    return ParsedDocument(metadata, observations)


def parse_source(path: Path, *, allow_partial=False) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        parsed = parse_xlsx(path,allow_partial=allow_partial)
    elif suffix == ".docx":
        parsed = parse_docx(path)
    else:
        raise UnsupportedFormat("Only .xlsx and .docx are supported")
    from .matrix_fields import IDENTITY_VERSION
    from .normalize import SEMANTIC_VERSION
    from .standards import VERSION, expand
    parsed.observations = [r.model_copy(update={'source_value': {
        **r.source_value, 'identity_rule_version': IDENTITY_VERSION, 'semantic_rule_version': SEMANTIC_VERSION,
        'standards_rule_version': VERSION}}) for r in parsed.observations]
    parsed.observations = [child for raw in parsed.observations for child in expand(raw)]
    return parsed
