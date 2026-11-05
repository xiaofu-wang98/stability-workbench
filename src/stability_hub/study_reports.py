"""Verified factual Word/Excel/PNG packages from immutable selected results."""
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Literal
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import Field

from .models import StabilityRecord, digest
from .permissions import Capability, require
from .workflow_contracts import AxisOptions, ExportRequest, ResultFilter
from .workflow_common import WorkflowError, check_revision, encoded, uid
from .result_snapshot import create_snapshot
from .result_queries import cells_from_rows
from .workflow_excel import write_workbook
from .workflow_excel_verify import verify_workbook
from .comparability import compare, StudyQuery, QuickCompare, comparison_snapshot, SCOPE_FIELDS, sample_id
from .trend_export import analysis_scope, chart_data, write_png


class ReportRequest(AxisOptions):
    project: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    kind: Literal['stability','comparability','quick'] = 'stability'
    quick: QuickCompare | None = None
    study_id: str | None = None
    mode: Literal['release','stability','all'] = 'all'
    filters: ResultFilter = Field(default_factory=ResultFilter)
    slot_ids: tuple[str,...] = ()
    include_charts: bool = True


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def human(value):
    if isinstance(value,str) and value.startswith('原文未注明@'):
        return '原文未注明（独立来源）'
    return {'unknown':'用途未注明','reference':'参考品','routine':'常规样品','not_reported':'版本未注明',
            'long_term':'长期稳定性','accelerated':'加速稳定性','stress':'应力试验','release':'放行'}.get(value,value)


def write_document(path,snapshot,charts):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = Inches(.7)
    sec.left_margin = sec.right_margin = Inches(.65)
    for name in ('Normal','Title','Heading 1','Heading 2'):
        style = doc.styles[name]
        style.font.name = 'Microsoft YaHei'
        style.font.color.rgb = RGBColor(0,0,0)
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    doc.styles['Normal'].font.size = Pt(9)
    doc.styles['Normal'].paragraph_format.space_after = Pt(5)
    for border in doc.styles.element.xpath('.//w:pBdr'):
        border.getparent().remove(border)
    quick = any(s.get('kind')=='quick' for s in snapshot.get('comparison_studies',[]))
    title = snapshot['project'] + (' 批次对比结果报告' if quick else ' 可比性研究结果报告' if snapshot.get('comparison_studies') else ' 稳定性检测结果报告')
    doc.add_paragraph(title,'Title')
    doc.add_paragraph(f"数据版本 {snapshot['revision']}。本报告汇总所选已提交事实，表格并列最新展示值与正式采用值；曲线仅采用正式确认的精确数值。缺失和限值结果不补零。不自动作合格、可比性或有效期结论。")
    names = {'batch':'批号','sample_type':'材料','presentation':'规格','purpose':'用途','study':'考察类型',
             'study_id':'原研究编号','condition':'条件','month':'月','day':'天','item':'检项','method':'方法',
             'method_version':'方法版本','reporting_basis':'报告体系'}
    filters = '；'.join(f'{names[k]}：{human(v)}' for k,v in snapshot['filters'].items() if v is not None) or '所选已提交结果'
    doc.add_paragraph(f"范围：{filters}。结果槽位 {len(snapshot['entries'])}。完整来源、标准原文、待提交及未知区域见同包 Excel；独立身份与范围证据见 snapshot.json。")
    records = [StabilityRecord.model_validate_json((e['adopted'] or e['display'])['record_json']) for e in snapshot['entries']]
    for study in snapshot.get('comparison_studies',[]):
        doc.add_heading(study['title'],1)
        doc.add_paragraph('分组依据：'+study['reason'])
        doc.add_paragraph('研究内显式对齐：'+('、'.join(names[k] for k in study['aligned_fields']) or '无')+'。'+study.get('alignment_reason',''))
        role_names = {'before':'变更前','after':'变更后','selected':'按批次对比','pending':'待分组','exclude':'不参与'}
        shown = set()
        for record in records:
            sid = sample_id(record)
            if sid in shown:
                continue
            shown.add(sid)
            role = next(m['role'] for m in study['members'] if m['sample_id']==sid)
            doc.add_paragraph(f"{role_names[role]}：{record.key.batch} · {record.key.sample_type} · {human(record.key.presentation)} · {human(record.key.purpose)}")
        pending = study.get('pending_sample_count',sum(m['role']=='pending' for m in study['members']))
        excluded = sum(m['role']=='exclude' for m in study['members'])
        doc.add_paragraph(f'本研究待分组 {pending} 个样品，不参与 {excluded} 个样品；以上样品不计入所选比较结果。')
    doc.add_heading('检测结果与来源',1)
    pairs = [(e['display'],e['adopted']) for e in snapshot['entries']]
    cells = cells_from_rows(pairs)
    expected = []
    # Narrow, repeating-header tables; identities stay as received even after local plot alignment.
    groups = defaultdict(list)
    for cell,entry in zip(cells,snapshot['entries'],strict=True):
        rec = StabilityRecord.model_validate_json(entry['display']['record_json'])
        k = rec.key
        groups[(k.sample_type,k.batch,k.presentation,k.purpose,k.study,k.study_id,k.condition,k.reporting_basis)].append((cell,rec))
    for identity,rows in groups.items():
        doc.add_heading(' · '.join(str(human(v)) for v in identity if v),2)
        table = doc.add_table(rows=1, cols=5)
        table.style = 'Table Grid'
        for c,t in zip(table.rows[0].cells,['检项与方法','时间','最新展示值','正式采用值','标准原文'],strict=True):
            c.text = t
            c.paragraphs[0].paragraph_format.keep_with_next = True
        repeat = OxmlElement('w:tblHeader')
        table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for cell,rec in sorted(rows,key=lambda pair:(pair[1].key.item,pair[1].key.method,pair[1].key.method_version,
                                                   pair[1].key.day if pair[1].key.day is not None else pair[1].key.month or 0)):
            label = snapshot['item_labels'].get(rec.key.item,rec.key.item)
            display = cell.display.text + ('（待确认）' if cell.display.pending_confirmation else '')
            adopted = cell.adopted.text if cell.adopted else '尚无正式采用值'
            standards = '\n'.join(s.criterion_text for s in rec.standards) or '未关联适用标准'
            item = label + (f'\n{rec.key.method}' if rec.key.method not in label else '')
            if rec.key.method_version != 'not_reported':
                item += '\n'+rec.key.method_version
            texts = [item, rec.key.time_label,display,adopted,standards]
            row = table.add_row()
            row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
            for c,t in zip(row.cells,texts,strict=True):
                c.text = t
                expected.append(t)
    if charts:
        doc.add_page_break()
        doc.add_heading('正式采用值趋势',1)
        for index,(label,image,data) in enumerate(charts):
            if index:
                doc.add_page_break()
            doc.add_heading(label,2)
            scope_note = doc.add_paragraph(' · '.join(str(human(v)) for v in data['scope'] if v))
            scope_note.paragraph_format.keep_with_next = True
            doc.add_picture(str(image),width=Inches(6.6))
            doc.add_paragraph(f"纵轴 {data['axis']['min']:g} 至 {data['axis']['max']:g}。{data['axis']['note']} 未绘制 {len(data['omitted'])} 项。")
    doc.add_heading('评价与后续处理',1)
    doc.add_paragraph('未评价。请结合适用的质量标准、研究方案及审核结论进行人工评价。来源标准仅按原文列示，未经适用性确认不作为本报告接受限度。')
    doc.save(path)
    restored = Document(path)
    actual = [c.text for t in restored.tables for row in t.rows[1:] for c in row.cells]
    if actual != expected:
        raise WorkflowError('report_verification','Word表格反读与所选结果不一致。',409)
    return {'word_cells_checked':len(expected)}


def create_report(flow,command,actor):
    require(actor,Capability.EXPORT)
    selected = command.slot_ids
    configs = []
    with flow.store.read() as conn:
        check_revision(conn,command.project,command.base_revision)
        if command.kind=='quick':
            if command.quick is None:
                raise WorkflowError('quick_required','请提供明确的快速对比范围。')
            if command.quick.base_revision!=command.base_revision:
                raise WorkflowError('stale_revision','快速对比与导出数据版本不同，请刷新。',409)
            from .quick_comparison import quick_compare
            view = quick_compare(conn,command.project,command.quick.model_copy(update={'include_cells':True,'group_id':None}))
            ids = [c['slot_id'] for c in view['cells']]
            if selected and (len(set(selected))!=len(selected) or not set(selected)<=set(ids)):
                raise WorkflowError('report_scope','所选结果不属于当前快速对比范围。',409)
            selected = selected or tuple(ids)
            if not selected:
                raise WorkflowError('empty_report','当前快速对比范围没有已提交结果。')
            configs = [view['study']]
        elif command.kind=='comparability':
            if not command.study_id:
                raise WorkflowError('study_required','请先选择已保存的可比研究。')
            modes = ('release','stability') if command.mode=='all' else (command.mode,)
            ids = []
            for mode in modes:
                view = compare(conn,command.project,command.study_id,StudyQuery(base_revision=command.base_revision,mode=mode))
                ids.extend(c['slot_id'] for c in view['cells'])
            configs = [{**view['study'],'pending_sample_count':len(view['pending_samples'])}]
            if selected and (len(set(selected))!=len(selected) or not set(selected)<=set(ids)):
                raise WorkflowError('report_scope','所选槽位不属于该研究与报告模式。',409)
            selected = selected or tuple(ids)
            if not selected:
                raise WorkflowError('empty_report','当前研究范围没有参与对比的已提交结果。')
        elif command.mode != 'all':
            raise WorkflowError('report_mode','检测台账报告请使用筛选条件确定范围。')
        request = ExportRequest(project=command.project,base_revision=command.base_revision,
                                slot_ids=selected,filters=command.quick.filters if command.kind=='quick' else command.filters)
        snapshot = create_snapshot(flow,conn,request)
    if not snapshot['entries']:
        raise WorkflowError('empty_report','所选范围没有已提交结果。')
    snapshot['comparison_studies'] = configs
    snapshot['axis'] = command.model_dump(include={'axis_mode','axis_min','axis_max'})
    snapshot['snapshot_id'] = digest({k:v for k,v in snapshot.items() if k!='snapshot_id'})
    export_id = uid('report')
    folder = flow.store.home / 'workflow-exports' / export_id
    folder.mkdir(parents=True)
    write_workbook(folder/'results.xlsx',snapshot)
    errors = verify_workbook(folder/'results.xlsx',snapshot)
    if errors:
        raise WorkflowError('report_verification','Excel反读失败：'+'; '.join(errors),409)
    charts = []
    if command.include_charts:
        groups = defaultdict(list)
        aligned = configs[0]['aligned_fields'] if configs else []
        for entry in snapshot['entries']:
            rec = StabilityRecord.model_validate_json((entry['adopted'] or entry['display'])['record_json'])
            scope = tuple('研究内显式对齐' if k in aligned else v for k,v in zip(SCOPE_FIELDS,analysis_scope(rec),strict=True))
            groups[digest(scope)].append((entry['display'],entry['adopted']))
        for gid,pairs in groups.items():
            sub = comparison_snapshot(command.project,command.base_revision,configs[0],pairs,snapshot['item_labels'],command) if configs else {
                **snapshot,'entries':[{'display':a,'adopted':b} for a,b in pairs]}
            data = chart_data(sub,aligned_fields=aligned)
            if data['points']:
                image = folder/f'{gid[:16]}.png'
                data = write_png(image,sub,aligned_fields=aligned)
                charts.append((data['item_label'],image,data))
    verification = write_document(folder/'report.docx',snapshot,charts)
    (folder/'snapshot.json').write_text(encoded(snapshot),encoding='utf-8')
    files = {p.name:_sha(p) for p in folder.iterdir() if p.is_file()}
    (folder/'verification.json').write_text(encoded({'passed':True,**verification,'files':files}),encoding='utf-8')
    path = folder/'report.zip'
    with ZipFile(path,'w',ZIP_DEFLATED) as package:
        for p in folder.iterdir():
            if p != path and p.is_file():
                package.write(p,p.name)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        check_revision(conn,command.project,command.base_revision)
        conn.execute('INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by) VALUES (?,?,?,?,?,?,?)',
                     (export_id,command.project,command.base_revision,str(path),_sha(path),encoded(snapshot),actor.id))
    return {'export_id':export_id,'project':command.project,'base_revision':command.base_revision,'state':'verified',
            'filename':'report.zip','download_url':f'/api/v1/reports/{export_id}/file',
            'word_download_url':f'/api/v1/reports/{export_id}/word','result_count':len(snapshot['entries']),
            'chart_count':len(charts),'snapshot_id':snapshot['snapshot_id']}
