"""Conservative single-item PNG from a frozen result snapshot; no guessed points."""
import hashlib
import json
from pathlib import Path

from .models import StabilityRecord, digest
from .workflow_common import WorkflowError,encoded


def freeze_plot_limits(conn, snapshot):
    records = {entry["display"]["slot_id"]: StabilityRecord.model_validate_json(
        (entry["adopted"] or entry["display"])["record_json"]) for entry in snapshot["entries"]}
    if not records:
        return {}
    limits = {}
    plans = {}
    for entry in conn.execute(
        """SELECT s.slot_id, w.id AS sheet_id, p.plan_json FROM business_worksheet_slots s
           JOIN business_worksheets w ON w.id=s.sheet_id
           JOIN business_worksheet_plans p ON p.sheet_id=w.id AND p.version=w.plan_version
           WHERE w.project=?""", (snapshot["project"],),
    ):
        record = records.get(entry["slot_id"])
        if record is None:
            continue
        if entry["sheet_id"] not in plans:
            plans[entry["sheet_id"]] = json.loads(entry["plan_json"])["rows"]
        row = next((row for row in plans[entry["sheet_id"]] if row["item_id"] == record.key.item
                    and row["method"] == record.key.method and row["method_version"] == record.key.method_version
                    and row["unit"] == record.result.unit), None)
        if row is None:
            continue
        criteria = [item for item in row.get("standards", []) if item.get("plot_limits")]
        if len(criteria) == 1:
            criterion = criteria[0]
            limits[entry["slot_id"]] = {**criterion["plot_limits"], "specification_id": criterion["specification_id"],
                                        "table": criterion["table"], "row": criterion["row"]}
    return limits


def analysis_scope(rec):
    return (rec.key.sample_type,rec.key.presentation,rec.key.study,rec.key.study_id,rec.key.condition,
            rec.key.item,rec.key.method,rec.key.method_version,rec.result.unit,
            'D' if rec.key.day is not None else 'M' if rec.key.month is not None else 'release',
            rec.key.purpose,rec.key.reporting_basis)


def chart_data(snapshot,view='adopted',axis=None,aligned_fields=()):
    records = [(entry[view] or entry['display'],
                StabilityRecord.model_validate_json((entry[view] or entry['display'])['record_json']),
                entry[view] is not None) for entry in snapshot['entries']]
    scopes = {tuple('研究内显式对齐' if field in aligned_fields else value for field,value in
                    zip(('sample_type','presentation','study','study_id','condition','item','method','method_version','unit','time_unit','purpose','reporting_basis'),analysis_scope(rec),strict=True)) for _,rec,_ in records}
    if len(scopes) != 1:
        raise WorkflowError('chart_scope','PNG须选择同一样品规格、条件、研究、检测项、方法/版本、单位和时间单位。')
    scope = next(iter(scopes))
    points,omitted = [],[]
    labels = dict(snapshot.get('series_labels', {}))
    sample_ids = {}
    for _, record, _ in records:
        sample_ids.setdefault(record.key.batch, set()).add(record.key.sample_id)
    for row, record, _ in records:
        key = record.key
        if len(sample_ids[key.batch]) > 1:
            labels.setdefault(row['slot_id'], f"{key.batch} · {key.sample_id or '历史样品'}")
    batches = sorted({labels.get(row['slot_id'],rec.key.batch) for row,rec,_ in records})
    for row,rec,has_value in records:
        point = {'version_id':row['id'] if has_value else None, 'slot_id':row['slot_id'],
                 'source_ids':[s['source_id'] for s in json.loads(row['source_json'])],
                 'batch':labels.get(row['slot_id'],rec.key.batch),'source_batch':rec.key.batch,'time':rec.key.time_label,'raw':rec.result.raw_text,
                 'unit':rec.result.unit,'state':row['state'],'comparator':rec.result.comparator,
                 'x':rec.key.day if rec.key.day is not None else rec.key.month if rec.key.month is not None else batches.index(labels.get(row['slot_id'],rec.key.batch))}
        if has_value and rec.result.status == 'numeric' and rec.result.comparator == 'eq' and row['state'] in {'confirmed','legacy'}:
            points.append({**point,'value':str(rec.result.value)})
        else:
            omitted.append({**point,'reason':'no adopted result' if not has_value else
                            'pending' if row['state']=='pending' else 'not an exact numeric result'})
    points.sort(key=lambda p:(p['batch'],p['x']))
    times = sorted({p['x'] for p in points+omitted})
    exact = {(p['batch'],p['x']):p for p in points}
    if len({(p['batch'],p['x']) for p in points+omitted}) != len(points+omitted):
        raise WorkflowError('chart_collision','同一曲线时间点存在多条身份结果，请分组核对；不能覆盖其中一条。',409)
    existing = {(p['batch'],p['x']):p for p in points+omitted}
    series = [{'batch':batch,'values':[{
        **existing.get((batch,x),{'x':x,'batch':batch,'reason':'missing received time point','slot_id':None}),
        'value':exact.get((batch,x),{}).get('value')} for x in times]} for batch in batches]
    selection_id = digest([snapshot['project'],snapshot['revision'],view,
                           sorted((e['display']['slot_id'],e['display']['id'],
                                   e['adopted']['id'] if e['adopted'] else None) for e in snapshot['entries'])])
    from .plot_axes import axis_range
    label = snapshot.get('item_labels',{}).get(scope[5],scope[5])
    options = axis or snapshot.get('axis', {})
    candidates = [snapshot.get("plot_limits", {}).get(p["slot_id"]) for p in points]
    limits = candidates[0] if candidates and candidates[0] and all(value == candidates[0] for value in candidates) else None
    bounds = [limits[k] for k in ("lower", "upper") if limits[k] is not None] if limits else []
    scale = axis_range([p['value'] for p in points] + bounds, label, scope[8],
                       options.get('axis_mode','overview'), options.get('axis_min'), options.get('axis_max'))
    return {'axis':scale,'aligned_fields':list(aligned_fields),'snapshot_id':snapshot['snapshot_id'],'project':snapshot['project'],'revision':snapshot['revision'],
            'selection_id':selection_id,'series':series,'times':times,
            'item_label':label,
            'unresolved_worksheet_count':len(snapshot.get('pending_source_regions',[])),
            'filters':snapshot['filters'],'view':view,'scope':scope,'points':points,'omitted':omitted,'batches':batches,
            'standard_limits':limits}


_OMITTED_REASON_ZH = {
    'no adopted result': '无正式采用结果',
    'pending': '待复核确认',
    'not an exact numeric result': '非精确数值结果',
    'missing received time point': '缺失该时间点',
}


def write_png(path,snapshot,view='adopted',aligned_fields=()):
    import re
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.font_manager import FontProperties
    data = chart_data(snapshot,view,aligned_fields=aligned_fields)
    if not data['points']:
        raise WorkflowError(
            'chart_no_numeric_points',
            '当前所选范围没有可绘制的精确数值结果（均为定性描述、N/A 或限度符号），无法生成数值趋势图；请改选定量数值检项。',
            422,
        )
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
    title_font = FontProperties(fname=str(font_path), size=10.5) if font_path.exists() else FontProperties(size=10.5)
    legend_font = FontProperties(fname=str(font_path), size=8.5) if font_path.exists() else FontProperties(size=8.5)
    note_font = FontProperties(fname=str(font_path), size=8) if font_path.exists() else FontProperties(size=8)
    sample,presentation,study,study_id,condition,item,method,version,unit,time_unit,purpose,reporting_basis = data['scope']
    fig,ax = plt.subplots(figsize=(11,6))
    try:
        grouped = {series['batch']:series['values'] for series in data['series']}
        legend_labels: list[str] = []
        for batch,points in grouped.items():
            ax.plot(
                [p['x'] for p in points],
                [float(p['value']) if p['value'] is not None else float('nan') for p in points],
                marker='o',
                linewidth=1.9,
                markersize=5.5,
                linestyle='--' if '· 变更前 ·' in batch else '-',
                label=batch,
            )
            legend_labels.append(batch)
            if data['axis']['mode']=='detail' and len(data['points']) <= 50:
                for point in points:
                    if point['value'] is not None:
                        ax.annotate(
                            point['value'],
                            (point['x'],float(point['value'])),
                            xytext=(4,6),
                            textcoords='offset points',
                            fontsize=8,
                            fontproperties=note_font,
                        )
        method_label = method + ('/'+version if version != 'not_reported' else '')
        if data["standard_limits"]:
            for bound, label in (("lower", "标准下限"), ("upper", "标准上限")):
                value = data["standard_limits"][bound]
                if value is not None:
                    lim_label = f"{label} ({value:g})"
                    ax.axhline(value, color="#b4432c", linestyle="--", linewidth=1.4, label=lim_label)
                    legend_labels.append(lim_label)
        status_label = '正式采用值' if view == 'adopted' else '展示值（仅绘制已确认精确数值）'
        study_label = {'long_term':'长期','accelerated':'加速','stress':'应力','release':'放行'}.get(study,study)
        purpose_label = {'routine':'常规考察','reference':'参考品','unknown':'常规/未注明'}.get(purpose,purpose)
        mode_label = {'overview': '总览刻度', 'detail': '细节刻度', 'manual': '手动范围'}.get(data['axis']['mode'], data['axis']['mode'])
        title = f"{data['project']} | {data['item_label']} | {method_label} | {status_label}（{mode_label}）| 数据版本 {data['revision']}"
        def human(value):
            return '原文未注明（独立来源）' if value.startswith('原文未注明@') else value
        subtitle = f'{sample}/{purpose_label}/{reporting_basis or "共用结果"} | {human(presentation)} | {study_label}/{human(study_id)} | {condition}'
        if len(subtitle) > 120:
            subtitle = subtitle[:117] + '…'
        eff_title_font = FontProperties(fname=str(font_path), size=9.5 if len(subtitle) > 88 else 10.5) if font_path.exists() else FontProperties(size=9.5 if len(subtitle) > 88 else 10.5)
        ax.set_title(title + '\n' + subtitle, fontproperties=eff_title_font, pad=10)
        if unit:
            y_label = unit
        elif re.search(r'(?<![A-Za-z])pH(?![A-Za-z])', data['item_label'] or '', re.I):
            y_label = 'pH（无量纲）'
        else:
            y_label = '无量纲'
        ax.set_ylabel(y_label, fontproperties=font)
        ax.set_ylim(data['axis']['min'], data['axis']['max'])
        x_label = '考察时间（天）' if time_unit=='D' else '考察时间（月）' if time_unit=='M' else '批次'
        ax.set_xlabel(x_label, fontproperties=font, labelpad=6)
        if time_unit=='release':
            ax.set_xticks(range(len(data['batches'])))
            ax.set_xticklabels(data['batches'], fontproperties=note_font, rotation=15 if len(data['batches']) > 4 else 0)
        else:
            times = sorted({p['x'] for p in data['points']+data['omitted']})
            ax.set_xticks(times)
            if len(times)==1:
                ax.set_xlim(times[0]-.5,times[0]+.5)
        ax.grid(alpha=.25, linestyle=':')

        # Place legend cleanly outside the right plot border so it never overlaps curves or limit lines
        if legend_labels:
            max_lbl_len = max(len(lbl) for lbl in legend_labels)
            right_margin = 0.72 if max_lbl_len > 22 else 0.77
            ax.legend(
                prop=legend_font,
                loc='upper left',
                bbox_to_anchor=(1.015, 1.0),
                borderaxespad=0.0,
                frameon=True,
                framealpha=0.92,
                edgecolor='#cbd5e1',
            )
        else:
            right_margin = 0.96

        if data['omitted']:
            notes = ['未绘入数值曲线（不按 0 处理）：']
            for p in data['omitted'][:6]:
                reason_zh = _OMITTED_REASON_ZH.get(p['reason'], p['reason'])
                raw_text = str(p['raw']).replace('\n', ' ')
                line = f"  · {p['batch']} {p['time']}：{raw_text}（{reason_zh}）"
                if len(line) > 105:
                    line = line[:102] + '…'
                notes.append(line)
            if len(data['omitted']) > 6:
                notes.append(f"  ……共 {len(data['omitted'])} 条，完整明细见随附清单（manifest JSON）")
            caption = '\n'.join(notes)
        else:
            caption = '说明：仅绘制已确认的精确数值结果；缺失时间点保持断线留白，不按 0 处理。'
        if data['unresolved_worksheet_count']:
            caption += f"\n另有 {data['unresolved_worksheet_count']} 张未完成解析的源工作表（未计入正式事实，详见来源状态）。"

        caption_lines = len(caption.splitlines())
        bottom_margin = min(0.44, max(0.18, 0.115 + 0.029 * caption_lines))
        fig.subplots_adjust(left=0.09, right=right_margin, top=0.86, bottom=bottom_margin)
        fig.text(0.02, 0.018, caption, fontproperties=note_font, va='bottom', ha='left', color='#334155')
        fig.savefig(path, dpi=140, metadata={'Description':encoded(data)})
    finally:
        plt.close(fig)
    from PIL import Image
    with Image.open(path) as img:
        img.verify()
    with Image.open(path) as img:
        if json.loads(img.info['Description']) != json.loads(encoded(data)):
            raise WorkflowError('image_verification_failed','PNG范围/快照元数据反读不一致。',409)
    manifest = {**data,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    Path(path).with_suffix('.json').write_text(encoded(manifest),encoding='utf-8')
    return manifest
