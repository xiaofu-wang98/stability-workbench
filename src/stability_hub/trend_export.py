"""Conservative single-item PNG from a frozen result snapshot; no guessed points."""
import hashlib
import json
from pathlib import Path

from .models import StabilityRecord, digest
from .workflow_common import WorkflowError,encoded


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
    labels = snapshot.get('series_labels',{})
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
    scale = axis_range([p['value'] for p in points], label, scope[8],
                       options.get('axis_mode','overview'), options.get('axis_min'), options.get('axis_max'))
    return {'axis':scale,'aligned_fields':list(aligned_fields),'snapshot_id':snapshot['snapshot_id'],'project':snapshot['project'],'revision':snapshot['revision'],
            'selection_id':selection_id,'series':series,'times':times,
            'item_label':label,
            'unresolved_worksheet_count':len(snapshot.get('pending_source_regions',[])),
            'filters':snapshot['filters'],'view':view,'scope':scope,'points':points,'omitted':omitted,'batches':batches}


def write_png(path,snapshot,view='adopted',aligned_fields=()):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.font_manager import FontProperties
    data = chart_data(snapshot,view,aligned_fields=aligned_fields)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
    sample,presentation,study,study_id,condition,item,method,version,unit,time_unit,purpose,reporting_basis = data['scope']
    fig,ax = plt.subplots(figsize=(11,6))
    try:
        grouped = {series['batch']:series['values'] for series in data['series']}
        for batch,points in grouped.items():
            ax.plot([p['x'] for p in points],
                    [float(p['value']) if p['value'] is not None else float('nan') for p in points],marker='o',
                    linestyle='--' if '· 变更前 ·' in batch else '-',label=batch)
            if data['axis']['mode']=='detail' and len(data['points']) <= 50:
                for point in points:
                    if point['value'] is not None:
                        ax.annotate(point['value'],(point['x'],float(point['value'])),xytext=(5,7),textcoords='offset points',fontsize=8)
        method_label = method + ('/'+version if version != 'not_reported' else '')
        status_label = '正式采用值' if view == 'adopted' else '展示值（仅绘制已确认精确数值）'
        study_label = {'long_term':'长期','accelerated':'加速','stress':'应力','release':'放行'}.get(study,study)
        title = f"{data['project']} | {data['item_label']} | {method_label} | {status_label} | 数据版本 {data['revision']}"
        def human(value):
            return '原文未注明（独立来源）' if value.startswith('原文未注明@') else value
        ax.set_title(title + '\n' + f'{sample}/{purpose}/{reporting_basis or "共用结果"} | {human(presentation)} | {study_label}/{human(study_id)} | {condition}',fontproperties=font,fontsize=11)
        ax.set_ylabel(unit or 'source unit not reported',fontproperties=font)
        ax.set_ylim(data['axis']['min'],data['axis']['max'])
        ax.set_xlabel('Days' if time_unit=='D' else 'Months' if time_unit=='M' else 'Batch')
        if time_unit=='release':
            ax.set_xticks(range(len(data['batches'])),data['batches'])
        else:
            times = sorted({p['x'] for p in data['points']+data['omitted']})
            ax.set_xticks(times)
            if len(times)==1:
                ax.set_xlim(times[0]-.5,times[0]+.5)
        ax.grid(alpha=.2)
        if grouped:
            ax.legend(prop=font)
        notes = ['Excluded from numeric plot (never treated as zero):'] + [
            f"{p['batch']} {p['time']}: {p['raw']} [{p['reason']}]" for p in data['omitted'][:8]]
        if len(data['omitted'])>8:
            notes.append(f"... {len(data['omitted'])} total; full details in manifest")
        caption = '\n'.join(notes) if data['omitted'] else 'Only confirmed exact numeric results; missing time points remain missing.'
        if data['unresolved_worksheet_count']:
            caption += f"\nUnresolved source worksheets: {data['unresolved_worksheet_count']} (excluded from facts; see SourceStatus)."
        fig.text(.01,.01,caption,
                 fontsize=8,fontproperties=font)
        if data['omitted']:
            fig.subplots_adjust(bottom=min(.4,.12+.025*len(notes)))
        else:
            fig.subplots_adjust(bottom=.15)
        fig.savefig(path,dpi=140,metadata={'Description':encoded(data)})
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
