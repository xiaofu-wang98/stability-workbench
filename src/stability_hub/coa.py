"""Versioned sample release values keyed to the archived specification rows."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from zipfile import BadZipFile

from docx import Document
from docx.shared import Inches
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from lxml.etree import XMLSyntaxError
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from pydantic import BaseModel, Field, StringConstraints

from .permissions import Capability, require
from .protocol_preview import _cell_text
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, project_row, remember, replay, uid

Label = Annotated[str, StringConstraints(strip_whitespace=True, max_length=250)]
Result = Annotated[str, StringConstraints(max_length=4000)]
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class SampleInput(BaseModel):
    batch: Annotated[Label, Field(min_length=1)]
    material_stage: Annotated[Label, Field(min_length=1)]
    sample_type: Literal['DS', 'DP', 'placebo']
    presentation: Label = ''
    packaging: Label = ''
    purpose: Annotated[Label, Field(min_length=1)]


class CoaSave(BaseModel):
    revision: int = Field(ge=0)
    version: int = Field(ge=0)
    idempotency_key: Annotated[Label, Field(min_length=1)]
    values: dict[str, Result] = Field(max_length=500)
    upload_id: Label = ''


def ensure_sample(conn, project, command, actor):
    fields = tuple(command.model_dump().values())
    matches = conn.execute("""SELECT * FROM sample_entities WHERE project=? AND batch=? AND material_stage=?
                              AND sample_type=? AND presentation=? AND packaging=? AND purpose=? AND deleted_at IS NULL""",
                           (project, *fields)).fetchall()
    if len(matches) > 1:
        raise WorkflowError('sample_ambiguous', '存在多个相同属性的样品，请选择已有样品。', 409)
    if matches:
        return dict(matches[0])
    sample_id = uid('sample')
    conn.execute("""INSERT INTO sample_entities(id,project,batch,material_stage,sample_type,presentation,packaging,purpose,source)
                    VALUES (?,?,?,?,?,?,?,?,'manual')""", (sample_id, project, *fields))
    bump(conn, project)
    event(conn, project, sample_id, 'sample_create', actor, '登记样品', command.model_dump())
    return dict(conn.execute('SELECT * FROM sample_entities WHERE id=?', (sample_id,)).fetchone())


def _key(row):
    return f"{row['table']}:{row['row']}"


def _document(data):
    try:
        return Document(BytesIO(data))
    except (BadZipFile, KeyError, ValueError, XMLSyntaxError) as exc:
        raise WorkflowError('coa_document', '无法读取 COA Word 文件。') from exc


def _standard(conn, specification_id, sample):
    spec = conn.execute('SELECT * FROM specifications WHERE id=?', (specification_id,)).fetchone()
    if spec is None:
        raise WorkflowError('not_found', '质量标准不存在。', 404)
    preview = json.loads(spec['preview_json'])
    scope = preview.get('material_scope')
    if (scope in {'DS', 'DP', 'placebo'} and scope != sample['sample_type']
            or scope == 'reference' and sample['material_stage'] != '参考品'):
        raise WorkflowError('coa_scope', '质量标准的适用对象与所选样品不一致。')
    if preview.get('project') and preview['project'] != sample['project']:
        raise WorkflowError('coa_scope', '质量标准与所选样品的项目不一致。')
    return spec, preview


def _tables(spec, preview):
    if preview.get('scan_id'):
        raise WorkflowError('coa_layout', '此标准来自扫描件，COA 原版式往返需要 Word 标准。')
    if hashlib.sha256(spec['source']).hexdigest() != spec['id']:
        raise WorkflowError('source_integrity', '质量标准原件校验失败。', 409)
    doc = _document(spec['source'])
    indices = sorted({row['table'] for row in preview['rows']})
    return doc, [doc.tables[index - 1] for index in indices], indices


def render_coa(spec, preview, sample, version, values):
    doc, tables, indices = _tables(spec, preview)
    doc.core_properties.identifier = f"stability-coa:{sample['id']}:{spec['id']}:{version}"
    retained = {table._tbl for table in tables}
    for element in list(doc.element.body):
        if element not in retained and element.tag != doc.element.body.sectPr.tag:
            doc.element.body.remove(element)
    for index, table in zip(indices, tables, strict=True):
        allowed_rows = {row['row'] for row in preview['rows'] if row['table'] == index}
        table.add_column(Inches(1.2))
        for number, row in enumerate(table.rows, 1):
            if number == 1:
                row.cells[-1].text = sample['batch']
            elif number in allowed_rows:
                row.cells[-1].text = values.get(f'{index}:{number}', '')
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def render_coa_excel(spec, preview, sample, version, values):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "质量标准与放行(COA)"

    # 元数据隐藏表
    meta_ws = wb.create_sheet(title="_stability_coa")
    meta_ws.sheet_state = "hidden"
    meta_ws.append(["sample_id", "specification_id", "version", "batch"])
    meta_ws.append([sample["id"], spec["id"], version, sample["batch"]])

    # 表头列
    headers = ["序号", "类别", "检测项目", "检测方法", "方法编号(SOP)", "质量标准(Acceptance Criteria)", f"{sample['batch']} (放行结果)"]
    ws.append(headers)

    # 样式配置
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center

    for seq, r in enumerate(preview["rows"], 1):
        key = f"{r['table']}:{r['row']}"
        val = values.get(key, "")
        cat = r.get("category", "")
        item = r.get("item", "")
        method = r.get("method", "")
        sop = r.get("sop", "")
        crit = r.get("criterion", "")
        row_data = [seq, cat, item, method, sop, crit, val]
        ws.append(row_data)
        current_row = ws.max_row
        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=current_row, column=col_idx)
            c.border = thin_border
            c.alignment = align_center if col_idx in (1, 5) else align_left

    # 列宽自适应调整
    col_widths = [8, 16, 25, 20, 18, 40, 25]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def read_coa_excel(spec, preview, sample, version, data):
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=False)
    except Exception as exc:
        raise WorkflowError("coa_document", "无法读取 COA Excel 文件。") from exc
    try:
        if "_stability_coa" not in wb.sheetnames or "质量标准与放行(COA)" not in wb.sheetnames:
            raise WorkflowError("coa_structure", "请使用本系统下载的 COA Excel 模板，保留样品标识及标准列。")
        meta = wb["_stability_coa"]
        sample_id, spec_id, source_version, batch = [meta.cell(2, col).value for col in range(1, 5)]
        if sample_id != sample["id"] or spec_id != spec["id"] or batch != sample["batch"]:
            raise WorkflowError("coa_scope", "COA 对应其他样品或标准。", 409)
        if type(source_version) is not int or not 0 <= source_version <= version:
            raise WorkflowError("coa_stale", "COA 版本无效，请重新下载当前版本。", 409)
        ws = wb["质量标准与放行(COA)"]
        expected = openpyxl.load_workbook(BytesIO(render_coa_excel(spec, preview, sample, version, {})))
        try:
            template = expected["质量标准与放行(COA)"]
            if ws.max_row != template.max_row or ws.max_column != 7:
                raise WorkflowError("coa_structure", "COA 行列结构已变化，请仅填写放行结果列。")
            for row in range(1, ws.max_row + 1):
                for col in range(1, 8 if row == 1 else 7):
                    if (ws.cell(row, col).value or "") != (template.cell(row, col).value or ""):
                        raise WorkflowError("coa_structure", f"COA 第 {row} 行标准内容或顺序已变化，请使用原模板。")
        finally:
            expected.close()
        values = {}
        for index, criterion in enumerate(preview["rows"], 2):
            cell = ws.cell(index, 7)
            if cell.data_type in {"f", "e"}:
                raise WorkflowError("coa_structure", f"第 {index} 行请填写明确结果，不能使用公式或错误值。")
            value = str(cell.value).strip() if cell.value is not None else ""
            if value:
                values[_key(criterion)] = value
        return values, source_version
    finally:
        wb.close()


def read_coa(spec, preview, sample, version, data):
    _, expected_tables, indices = _tables(spec, preview)
    document = _document(data)
    identifier = document.core_properties.identifier
    source_version = None
    if identifier.startswith('stability-coa:'):
        prefix = f"stability-coa:{sample['id']}:{spec['id']}:"
        suffix = identifier.removeprefix(prefix)
        if not suffix.isascii() or not suffix.isdecimal() or len(suffix) > 12:
            raise WorkflowError('coa_scope', 'COA 对应其他样品或标准。', 409)
        source_version = int(suffix)
        if source_version > version:
            raise WorkflowError('coa_stale', 'COA 的版本超前，请重新下载当前版本。', 409)
    received = document.tables
    if len(received) != len(expected_tables):
        raise WorkflowError('coa_structure', 'COA 表格数量与所选标准不同，请使用该标准下载的 COA。')
    allowed = {_key(row) for row in preview['rows']}
    values = {}
    for index, expected, actual in zip(indices, expected_tables, received, strict=True):
        if len(expected.rows) != len(actual.rows) or len(actual.columns) != len(expected.columns) + 1:
            raise WorkflowError('coa_structure', f'标准表 {index} 的行列结构发生变化。')
        for number, (left, right) in enumerate(zip(expected.rows, actual.rows, strict=True), 1):
            cells = right.cells
            if (not cells or left.grid_cols_before != right.grid_cols_before
                    or left.grid_cols_after != right.grid_cols_after
                    or [_cell_text(cell) for cell in left.cells] != [_cell_text(cell) for cell in cells[:-1]]):
                raise WorkflowError('coa_structure', f'标准表 {index} 第 {number} 行内容发生变化。')
            if number == 1 and _cell_text(cells[-1]) != sample['batch']:
                raise WorkflowError('coa_batch', 'COA 最右列的批号与所选样品不一致。')
            value = '\n'.join(paragraph.text for paragraph in cells[-1].paragraphs).strip()
            key = f'{index}:{number}'
            if number > 1 and value:
                if key not in allowed or len(value) > 4000:
                    raise WorkflowError('coa_result', f'请核对标准表 {index} 第 {number} 行的放行结果。')
                values[key] = value
    return values, source_version


def _check_upload_version(values, source_version, existing, version):
    if source_version != version and any(existing.get(key) != value for key, value in values.items()):
        if source_version is not None:
            raise WorkflowError('coa_stale', 'COA 导出后已有新的放行结果，请下载当前版本并核对修改。', 409)


def router_for(store, actor_dependency, project_dependency):
    router = APIRouter(prefix='/api/v1')

    def sample_for(conn, sample_id):
        sample = conn.execute('SELECT * FROM sample_entities WHERE id=? AND deleted_at IS NULL', (sample_id,)).fetchone()
        if sample is None:
            raise WorkflowError('not_found', '样品不存在。', 404)
        return sample

    def current(conn, sample_id, specification_id):
        row = conn.execute('''SELECT version,values_json FROM coa_versions
                              WHERE sample_id=? AND specification_id=? ORDER BY version DESC LIMIT 1''',
                           (sample_id, specification_id)).fetchone()
        return (row['version'], json.loads(row['values_json'])) if row else (0, {})

    @router.get('/projects/{project}/samples')
    def samples(project: project_dependency, operator: actor_dependency, batch: str = ''):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            project_row(conn, project)
            return [dict(row) for row in conn.execute(
                'SELECT * FROM sample_entities WHERE project=? AND batch=? AND deleted_at IS NULL ORDER BY created_at,id', (project, batch.strip()))]

    @router.post('/projects/{project}/samples', status_code=201)
    def add_sample(project: project_dependency, command: SampleInput, operator: actor_dependency):
        require(operator, Capability.CONFIRM_FACT)
        with store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            project_row(conn, project)
            return ensure_sample(conn, project, command, operator)

    route = '/samples/{sample_id}/coa/{specification_id}'

    @router.get(route)
    def get_coa(sample_id: str, specification_id: str, operator: actor_dependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            sample = sample_for(conn, sample_id)
            spec, preview = _standard(conn, specification_id, sample)
            version, values = current(conn, sample_id, specification_id)
            return {'version': version, 'values': values, 'revision': project_row(conn, sample['project'])['revision'],
                    'word_supported': not preview.get('scan_id')}

    @router.get(route + '/file')
    def download(sample_id: str, specification_id: str, operator: actor_dependency, format: Literal['docx', 'xlsx'] = 'docx'):
        require(operator, Capability.EXPORT)
        with store.read() as conn:
            sample = sample_for(conn, sample_id)
            spec, preview = _standard(conn, specification_id, sample)
            version, values = current(conn, sample_id, specification_id)
        if format == 'xlsx':
            content_bytes = render_coa_excel(spec, preview, sample, version, values)
            media = XLSX
            ext = 'xlsx'
        else:
            content_bytes = render_coa(spec, preview, sample, version, values)
            media = DOCX
            ext = 'docx'
        return Response(content_bytes, media_type=media,
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(f"COA-{sample['batch']}.{ext}", safe='')})

    @router.get('/coa-sources/{source_id}/file')
    def coa_original(source_id: str, operator: actor_dependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            source = conn.execute('SELECT filename,source FROM coa_sources WHERE id=?', (source_id,)).fetchone()
        if source is None:
            raise WorkflowError('not_found', '找不到 COA 原件。', 404)
        if hashlib.sha256(source['source']).hexdigest() != source_id:
            raise WorkflowError('source_integrity', 'COA 原件校验失败。', 409)
        media = XLSX if source['filename'].lower().endswith('.xlsx') else DOCX
        return Response(source['source'], media_type=media,
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(source['filename'], safe='')})

    @router.post(route + '/preview')
    def preview_upload(sample_id: str, specification_id: str, operator: actor_dependency,
                       file: Annotated[UploadFile, File()]):
        require(operator, Capability.PREVIEW)
        filename = Path(file.filename or '').name
        ext = Path(filename).suffix.lower()
        data = file.file.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024 or ext not in ('.docx', '.xlsx'):
            raise WorkflowError('coa_document', '请选择不超过 20 MB 的 Word 或 Excel COA。')
        with store.read() as conn:
            sample = sample_for(conn, sample_id)
            spec, preview = _standard(conn, specification_id, sample)
            version, existing = current(conn, sample_id, specification_id)
            if ext == '.xlsx':
                values, source_version = read_coa_excel(spec, preview, sample, version, data)
            else:
                values, source_version = read_coa(spec, preview, sample, version, data)
            _check_upload_version(values, source_version, existing, version)
            revision = project_row(conn, sample['project'])['revision']
        upload_id = hashlib.sha256(data).hexdigest()
        with store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO coa_sources VALUES (?,?,?)', (upload_id, filename, data))
        return {'upload_id': upload_id, 'version': version, 'revision': revision, 'existing': existing, 'values': values}

    @router.post(route)
    def save_coa(sample_id: str, specification_id: str, command: CoaSave, operator: actor_dependency):
        require(operator, Capability.CONFIRM_FACT)
        payload = {'coa': [sample_id, specification_id], **command.model_dump(exclude={'idempotency_key'})}
        with store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            sample = sample_for(conn, sample_id)
            project = sample['project']
            prior = replay(conn, project, operator, command.idempotency_key, payload)
            if prior is not None:
                return prior
            check_revision(conn, project, command.revision)
            spec, preview = _standard(conn, specification_id, sample)
            version, existing = current(conn, sample_id, specification_id)
            if version != command.version:
                raise WorkflowError('coa_stale', '放行结果已更新，请重新打开 COA 核对。', 409)
            submitted = {key: value.strip() for key, value in command.values.items() if value.strip()}
            if submitted.keys() - {_key(row) for row in preview['rows']}:
                raise WorkflowError('coa_result', '放行结果包含未列入此标准的条款。')
            if command.upload_id:
                source = conn.execute('SELECT filename,source FROM coa_sources WHERE id=?', (command.upload_id,)).fetchone()
                if source is None or hashlib.sha256(source['source']).hexdigest() != command.upload_id:
                    raise WorkflowError('coa_source', '找不到可校验的 COA 上传原件。')
                if source['filename'].lower().endswith('.xlsx'):
                    original, source_version = read_coa_excel(spec, preview, sample, version, source['source'])
                else:
                    original, source_version = read_coa(spec, preview, sample, version, source['source'])
                if submitted != original:
                    raise WorkflowError('coa_source', '待保存结果与上传的 COA 原件不一致。')
                _check_upload_version(submitted, source_version, existing, version)
            changes = {key: value for key, value in submitted.items() if existing.get(key) != value}
            if changes.keys() & existing.keys():
                require(operator, Capability.CORRECT_RESULT)
            revision = command.revision
            if changes:
                version += 1
                existing.update(changes)
                conn.execute('''INSERT INTO coa_versions(sample_id,specification_id,version,values_json,changed_json,source_id,actor_id)
                                VALUES (?,?,?,?,?,?,?)''',
                             (sample_id, specification_id, version, encoded(existing), encoded(changes),
                              command.upload_id or None, operator.id))

                # 同步写入 sample_release_results 表
                for key, val in changes.items():
                    tbl_str, row_str = key.split(':')
                    tbl_num, row_num = int(tbl_str), int(row_str)
                    comp = conn.execute("""
                        SELECT item_id FROM specification_assay_components
                        WHERE project=? AND specification_id=? AND table_number=? AND row_number=?
                    """, (project, specification_id, tbl_num, row_num)).fetchone()
                    if comp:
                        item_id = comp['item_id']
                        comp_info = conn.execute("""
                            SELECT unit, reporting_basis FROM assay_components
                            WHERE project=? AND item_id=?
                        """, (project, item_id)).fetchone()
                        u = comp_info['unit'] if comp_info else ''
                        rb = comp_info['reporting_basis'] if comp_info else ''
                        conn.execute("""
                            INSERT OR REPLACE INTO sample_release_results
                            (sample_id, project, item_id, specification_id, coa_version, raw_value, unit, reporting_basis, source_id, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (sample_id, project, item_id, specification_id, version, val, u, rb, command.upload_id or None))

                revision = bump(conn, project)
                event(conn, project, sample_id, 'coa_save', operator, '登记放行结果',
                      {'specification_id': specification_id, 'version': version, 'rows': list(changes)})
            response = {'version': version, 'revision': revision, 'values': existing, 'changed': len(changes)}
            remember(conn, project, operator, command.idempotency_key, payload, response)
            return response

    return router
