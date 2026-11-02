"""Conservative single-item PNG from a frozen result snapshot; no guessed points."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .models import StabilityRecord
from .workflow_common import WorkflowError,encoded


def chart_data(snapshot,view='adopted'):
    rows = [entry[view] for entry in snapshot['entries'] if entry[view] is not None]
    records = [(r,StabilityRecord.model_validate_json(r['record_json'])) for r in rows]
    scopes = {(rec.key.sample_type,rec.key.presentation,rec.key.study,rec.key.study_id,rec.key.condition,
               rec.key.item,rec.key.method,rec.key.method_version,rec.result.unit,
               'D' if rec.key.day is not None else 'M' if rec.key.month is not None else 'release', rec.key.purpose, rec.key.reporting_basis) for _,rec in records}
    if len(scopes) != 1:
        raise WorkflowError('chart_scope','PNG须选择同一样品规格、条件、研究、检测项、方法/版本、单位和时间单位。')
    scope = next(iter(scopes))
    points,omitted = [],[]
    batches = sorted({rec.key.batch for _,rec in records})
    for row,rec in records:
        point = {'version_id':row['id'],'batch':rec.key.batch,'time':rec.key.time_label,'raw':rec.result.raw_text,
                 'unit':rec.result.unit,'state':row['state'],'comparator':rec.result.comparator,
                 'x':rec.key.day if rec.key.day is not None else rec.key.month if rec.key.month is not None else batches.index(rec.key.batch)}
        if rec.result.status == 'numeric' and rec.result.comparator == 'eq' and row['state'] in {'confirmed','legacy'}:
            points.append({**point,'value':str(rec.result.value)})
        else:
            omitted.append({**point,'reason':'pending' if row['state']=='pending' else 'not an exact numeric result'})
    points.sort(key=lambda p:(p['batch'],p['x']))
    return {'snapshot_id':snapshot['snapshot_id'],'project':snapshot['project'],'revision':snapshot['revision'],
            'item_label':snapshot.get('item_labels',{}).get(scope[5],scope[5]),
            'unresolved_worksheet_count':len(snapshot.get('pending_source_regions',[])),
            'filters':snapshot['filters'],'view':view,'scope':scope,'points':points,'omitted':omitted,'batches':batches}


def write_png(path,snapshot,view='adopted'):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.font_manager import FontProperties
    data = chart_data(snapshot,view)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
    sample,presentation,study,study_id,condition,item,method,version,unit,time_unit,purpose,reporting_basis = data['scope']
    fig,ax = plt.subplots(figsize=(11,6))
    try:
        grouped = defaultdict(list)
        for point in data['points']:
            grouped[point['batch']].append(point)
        for batch,points in grouped.items():
            points.sort(key=lambda p:p['x'])
            ax.plot([p['x'] for p in points],[float(p['value']) for p in points],marker='o',label=batch)
            if len(data['points']) <= 50:
                for point in points:
                    ax.annotate(point['value'],(point['x'],float(point['value'])),xytext=(5,7),textcoords='offset points',fontsize=8)
        method_label = method + ('/'+version if version != 'not_reported' else '')
        status_label = '正式采用值' if view == 'adopted' else '展示值（仅绘制已确认精确数值）'
        study_label = {'long_term':'长期','accelerated':'加速','stress':'应力','release':'放行'}.get(study,study)
        title = f"{data['project']} | {data['item_label']} | {method_label} | {status_label} | 数据版本 {data['revision']}"
        ax.set_title(title + '\n' + f'{sample}/{purpose}/{reporting_basis or "共用结果"} | {presentation} | {study_label}/{study_id} | {condition}',fontproperties=font,fontsize=11)
        ax.set_ylabel(unit or 'source unit not reported',fontproperties=font)
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
