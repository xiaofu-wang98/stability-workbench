"""Physical worksheet inventory independent of successful result projection."""
import json
import posixpath
import hashlib
import re
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from .models import digest
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, replay, remember
from .workflow_contracts import MutationResult
from .xlsx_matrix import source_cells, header_label


def structural_roles(sheet, cells, summary):
    """Positive structural evidence only; unknown formulae and objects stay visible."""
    from datetime import date, datetime
    from .matrix_fields import month_token
    from .source_notes import review_text,informational_reference
    roles = {}
    blocks = summary.get('blocks', [])
    first_header = min((b['header_row'] for b in blocks if 'header_row' in b), default=0)
    captions = {'项目编号','产品名称','产品批号','样品批号','批号','开始日期','规格',
                '稳定性试验方案编号','稳定性类型','考察条件','实验条件','稳定性开展地点','存放位置','放入时间'}
    for (row,col), cell in cells.items():
        label = str(cell.value).splitlines()[0].strip().rstrip(':：')
        if row < first_header and label in captions and cell.data_type != 'f':
            merged = next((m for m in sheet.merged_cells.ranges if cell.coordinate in m), None)
            following = cells.get((row, merged.max_col+1 if merged else col+1))
            roles[(row,col)] = ('identity','显式矩阵前置信息标签')
            if (following is not None and following.data_type != 'f'
                    and not re.search(r'更正|修改|异常|复测|调查|OOS|OOE|AE-|correct|retest|deviat',str(following.value),re.I)):
                # Do not consume another labelled field as this caption's value.
                if str(following.value).splitlines()[0].strip().rstrip(':：') not in captions:
                    roles[(row,following.column)] = ('identity','显式标签相邻值，位于首个结果表头之前')
        value = str(cell.value)
        in_results=any(b.get('header_row',0)<row<=b.get('end_row',0) and b.get('time_columns') and col>=min(b['time_columns']) for b in blocks)
        if informational_reference(value) and not in_results and cell.data_type != 'f':
            roles[(row,col)] = ('note','独立AR编号作为当前表提醒；不要求强制关联批次/时间点，不改变采用')
        elif value.strip() and not review_text(value):
            roles[(row,col)] = ('note','完整模板写作指引；没有剩余实际说明')
        elif (re.match(r'^\s*(?:判定标准|判断标准|Evaluation criteria)[:：]', value, re.I)
              and not re.search(r'更正|修改|异常|复测|调查|OOS|OOE|AE-|correct|retest|deviat', value, re.I)
              and col < min((min(b['time_columns']) for b in blocks if b.get('time_columns')),default=0)):
            roles[(row,col)] = ('standard','时间点区域之前的显式判断标准说明')
        elif re.fullmatch(r'\s*(?:备注\s*(?:Remark|Notes?)?|Remark|Notes?)\s*', value, re.I):
            roles[(row,col)] = ('note','独立备注列标题')
    planning_rows = {b['planning_date_row'] for b in blocks if 'planning_date_row' in b}
    for (row,col), cell in cells.items():
        if row not in planning_rows:
            continue
        if isinstance(cell.value,(date,datetime)):
            roles[(row,col)] = ('auxiliary','表头下已识别计划日期行；不作为检测结果')
            continue
        match = re.fullmatch(r'=?EDATE\(\s*\$?([A-Z]+)\$?(\d+)\s*,\s*(\$?[A-Z]+\$?\d+|\d+)\s*\)',str(cell.value),re.I)
        if not match:
            continue
        start = sheet[f'{match[1]}{match[2]}'].value
        offset = sheet[match[3].replace('$','')].value if re.search('[A-Z]',match[3],re.I) else int(match[3])
        month = month_token(sheet.cell(row-1,col).value)
        if isinstance(start,(date,datetime)) and isinstance(offset,int) and offset == month:
            roles[(row,col)] = ('auxiliary','表头月龄与EDATE偏移一致，起始格为日期；仅计划日期')
    return roles


def unsupported_drawings(path):
    """Inspect raw drawing parts; openpyxl does not preserve native shapes."""
    ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    result = {}
    def target(base,relative):
        return relative.lstrip('/') if relative.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(base),relative))
    with ZipFile(path) as archive:
        def read(part):
            return etree.fromstring(archive.read(part),etree.XMLParser(resolve_entities=False,no_network=True))
        workbook = read('xl/workbook.xml')
        rels = {r.get('Id'):r.get('Target') for r in read('xl/_rels/workbook.xml.rels')}
        for sheet in workbook.findall('.//{*}sheet'):
            part = target('xl/workbook.xml',rels[sheet.get(ns+'id')])
            relpart = posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
            if relpart not in archive.namelist():
                continue
            links = {r.get('Id'):r.get('Target') for r in read(relpart) if r.get('TargetMode') != 'External'}
            for node in read(part).findall('.//{*}drawing'):
                drawing = target(part,links.get(node.get(ns+'id'),''))
                if drawing not in archive.namelist():
                    continue
                root = read(drawing)
                if any(isinstance(n.tag,str) and etree.QName(n).localname in {'sp','cxnSp','grpSp','contentPart'} for n in root.iter()):
                    coords = ['drawing:'+drawing]
                    title = sheet.get('name')
                    result.setdefault(title,[]).append({'id':digest([title,coords]),'section':title,'coordinates':coords,
                        'hidden':sheet.get('state','visible')!='visible','role':'unclassified','revision':1,
                        'basis':'Native drawing/line is not font strikethrough; inspect original manually, no OCR'})
    return result


def referenced_source_chart(chart, sheet, observations):
    """A cell-empty chart sheet may display other sheets' preserved source data."""
    from openpyxl.utils.cell import range_boundaries
    result_cells = {(o.location.section,o.location.row,o.location.column) for o in observations}
    references,results = 0,0
    for node in chart.to_tree().iter():
        kind = etree.QName(node).localname
        if kind in {'numLit','strLit'}:
            return False
        if kind != 'f' or not node.text:
            continue
        references += 1
        try:
            title,area = node.text.rsplit('!',1)
            title = title.strip("'").replace("''", "'")
            if title not in sheet.parent.sheetnames or '[' in title:
                return False
            x1,y1,x2,y2 = range_boundaries(area.replace('$',''))
            if None in (x1,y1,x2,y2) or (x2-x1+1)*(y2-y1+1)>100000:
                return False
        except (ValueError,TypeError):
            return False
        parent = node.getparent()
        role = etree.QName(parent.getparent()).localname if parent is not None and parent.getparent() is not None else ''
        if role not in {'val','yVal'}:
            continue
        source = sheet.parent[title]
        actual = [(r,c) for r in range(y1,y2+1) for c in range(x1,x2+1)
                  if source.cell(r,c).value is not None and str(source.cell(r,c).value).strip()]
        known = [(r,c) for r,c in actual if (title,r,c) in result_cells]
        limit = any(re.search(r'limit\s*[:：]|[上下]限|[≤≥<>]',str(source.cell(r,c).value),re.I)
                    for r in range(y1,y2+1) for c in range(1,x1))
        if actual and len(known)!=len(actual) and not limit:
            return False
        results += len(known)
    return bool(references and results)


def inventory(sheet,observations,summary):
    roles = {}
    for raw in observations:
        if raw.location.section != sheet.title:
            continue
        roles[(raw.location.row,raw.location.column)] = 'result'
        for name,loc in raw.evidence.items():
            if loc.section == sheet.title:
                roles.setdefault((loc.row,loc.column),'result' if name == 'result' else
                    'standard' if name in {'criterion','subresult'} else 'note' if name.startswith('block_note') else 'identity')
    cells = source_cells(sheet)
    inferred = structural_roles(sheet,cells,summary)
    blocks = summary.get('blocks',[])
    headers = {r for b in blocks if 'header_row' in b for r in (b['header_row'],b['header_row']+1)}
    standard_cols = {loc.column for raw in observations if raw.location.section == sheet.title
                     for key,loc in raw.evidence.items() if key == 'criterion'}
    # A merged standard heading can describe several dated standard columns;
    # the currently projected criterion need not reference every old column.
    for block in blocks:
        for (r,c),cell in cells.items():
            if r == block.get('header_row') and re.search(r'质量标准|接受限度|acceptance|specification',str(cell.value),re.I):
                merged = next((m for m in sheet.merged_cells.ranges if cell.coordinate in m),None)
                first_result = min(block.get('time_columns') or [sheet.max_column+1])
                standard_cols.update(col for col in (range(merged.min_col,merged.max_col+1) if merged else [c]) if col < first_result)
    # Chart references are structural evidence, not permission to ignore arbitrary
    # cells. Only categories and explicitly labelled limit series are auxiliary.
    from openpyxl.utils.cell import range_boundaries
    chart_aux,known_charts = set(),set()
    last_result = max((raw.location.row for raw in observations if raw.location.section == sheet.title),default=0)
    for index,chart in enumerate(sheet._charts):
        valid = True
        references = 0
        for node in chart.to_tree().iter():
            if etree.QName(node).localname in {'numLit','strLit'}:
                valid = False
            if etree.QName(node).localname != 'f' or not node.text:
                continue
            references += 1
            try:
                title,area = node.text.rsplit('!',1)
                if title.strip("'").replace("''", "'") != sheet.title:
                    valid = False
                    continue
                x1,y1,x2,y2 = range_boundaries(area.replace('$',''))
            except (ValueError,TypeError):
                valid = False
                continue
            referenced = {(r,c) for r in range(y1,y2+1) for c in range(x1,x2+1)}
            parent = node.getparent()
            is_category = parent is not None and parent.getparent() is not None and etree.QName(parent.getparent()).localname in {'cat','xVal'}
            limit = any(re.search(r'limit\s*[:：]|[上下]限|[≤≥<>]',str(cell.value),re.I)
                        for (r,c),cell in cells.items() if y1 <= r <= y2 and c <= x1)
            if is_category or (y1 > last_result and limit):
                chart_aux.update(referenced)
            elif not all(pos in roles for pos in referenced if pos in cells):
                valid = False
        anchor = getattr(chart.anchor,'_from',None)
        if ((valid and references and anchor and anchor.row+1 > last_result) or
                (not cells and referenced_source_chart(chart,sheet,observations))) and chart.ser:
            known_charts.add(index)
    if observations and summary.get('adapter') == 'long_table':
        headers.add(min(c.row for c in cells.values()))
    from .source_notes import explicit_annotation
    groups = {}
    tail_cells = {}
    row_labels = {}
    for (row,_),cell in cells.items():
        row_labels.setdefault(row,set()).add(str(cell.value).strip().casefold())
    for (row,col),cell in sorted(cells.items()):
        role = roles.get((row,col))
        labels = row_labels[row]
        if role is None:
            label = header_label(cell)
            if (row,col) in inferred:
                role = inferred[(row,col)][0]
            elif (row in headers or summary.get('adapter') == 'metadata' or label in {
                '项目编号','产品名称','产品批号','样品批号','批号','开始日期','规格',
                '稳定性试验方案编号','稳定性类型','考察条件'}) and cell.data_type != 'f':
                role = 'identity'
            elif col in standard_cols and any(b.get('header_row',row) < row <= b.get('end_row',0) for b in blocks):
                role = 'standard'
            elif (row,col) in chart_aux or (row > last_result and any((row,c) in chart_aux for c in range(col+1,sheet.max_column+1))
                    and cell.data_type not in {'n','f'}):
                role = 'auxiliary'
            elif labels & {'lower limit','upper limit','下限','上限'}:
                role = 'standard'
            elif labels & {'备注','note','notes'} and cell.data_type not in {'n','f'}:
                role = 'note'
            elif row > last_result and cell.data_type not in {'n','f'} and explicit_annotation(cell.value):
                role = 'note'
            else:
                role = 'unclassified'
        hidden = sheet.sheet_state != 'visible' or bool(sheet.row_dimensions[row].hidden) or bool(sheet.column_dimensions[cell.column_letter].hidden)
        if last_result and row > last_result:
            tail_cells[(row, col)] = (role, hidden, cell.coordinate)
        else:
            groups.setdefault((row, role, hidden), []).append(cell.coordinate)

    from openpyxl.utils.cell import coordinate_from_string
    if tail_cells:
        unvisited = set(tail_cells.keys())
        while unvisited:
            seed = min(unvisited)
            target_role, target_hidden, _ = tail_cells[seed]
            component = []
            queue = [seed]
            unvisited.remove(seed)
            while queue:
                curr_r, curr_c = queue.pop(0)
                component.append(tail_cells[(curr_r, curr_c)][2])
                neighbors = [
                    pos for pos in unvisited
                    if tail_cells[pos][0] == target_role and tail_cells[pos][1] == target_hidden
                    and (pos[0] == curr_r or (abs(pos[0] - curr_r) <= 2 and abs(pos[1] - curr_c) <= 3))
                ]
                for nbr in neighbors:
                    unvisited.remove(nbr)
                    queue.append(nbr)
            component = sorted(component, key=lambda c: (coordinate_from_string(c)[1], coordinate_from_string(c)[0]))
            groups[('tail_block', tuple(component), target_role, target_hidden)] = component

    regions = []
    for key, coords in groups.items():
        role = key[-2]
        hidden = key[-1]
        basis = 'physical cells and explicit parser evidence'
        summary_text = ''
        if key[0] == 'tail_block':
            min_r = min(coordinate_from_string(c)[1] for c in coords)
            max_r = max(coordinate_from_string(c)[1] for c in coords)
            span_text = f"原行 {min_r}—{max_r}" if min_r != max_r else f"原行 {min_r}"
            if role == 'auxiliary':
                summary_text = f"绘图辅助区域 · {span_text} · 不入库"
                basis = '表尾连贯绘图/计算辅助区域 · 紧邻图表与轴'
            elif role == 'unclassified':
                summary_text = f"表尾待核对区域 · {span_text} · 待明确用途"
                basis = '表尾连贯非结果区域 · 待人工确认用途'
            else:
                summary_text = f"表尾来源区域 · {span_text}"
        regions.append({
            'id': digest([sheet.title, coords]),
            'section': sheet.title,
            'coordinates': coords,
            'hidden': hidden,
            'role': role,
            'basis': basis,
            'summary': summary_text,
            'revision': 1,
        })
    for kind,objects in [('image',sheet._images),('chart',sheet._charts)]:
        for index,obj in enumerate(objects):
            anchor = getattr(obj.anchor,'_from',None)
            coord = f'{kind}:{index}' + (f'@row{anchor.row+1},col{anchor.col+1}' if anchor else '')
            regions.append({'id':digest([sheet.title,coord]),'section':sheet.title,'coordinates':[coord],
                'hidden':sheet.sheet_state != 'visible','role':'auxiliary' if kind == 'chart' and index in known_charts else 'unclassified',
                'basis':'chart outside results or cell-empty source chart with explicit result/identity/category/limit references' if kind == 'chart' and index in known_charts else 'embedded object; no OCR performed','revision':1})
    return {**summary,'section':sheet.title,'hidden':sheet.sheet_state != 'visible','regions':regions,
            'physical_nonempty':len(cells)}


def coverage(conn,upload):
    row = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='upload' ORDER BY sequence LIMIT 1",(upload['id'],)).fetchone()
    sheets = json.loads(row[0]).get('parsing_summary',[]) if row else []
    if upload['filename'].lower().endswith('.xlsx') and (not sheets or any('regions' not in s for s in sheets)):
        from .parsing import parse_source
        path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?',(upload['source_hash'],)).fetchone()[0])
        if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
            raise WorkflowError('source_changed','旧来源原件校验失败，不能重新核对区域。',409)
        sheets = list(parse_source(path,allow_partial=True).coverage)
    decisions = {}
    structural = {}
    for row in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='source_structure' ORDER BY sequence",(upload['id'],)):
        for change in json.loads(row[0])['changes']:
            structural.setdefault(change['region_id'],[]).append(change)
    undone = {json.loads(r[0])['batch_id'] for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",(upload['id'],))}
    for row in conn.execute("SELECT action,detail_json FROM workflow_events WHERE subject_id=? AND action IN ('classify_region','classify_source_sheet') ORDER BY sequence",(upload['id'],)):
        data = json.loads(row['detail_json'])
        if data.get('batch_id') and data['batch_id'] in undone:
            continue
        for region_id in data['region_ids'] if row['action']=='classify_source_sheet' else [data['region_id']]:
            decisions[region_id] = data
    projected = {}
    from openpyxl.utils import get_column_letter
    for row in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='parse_region' ORDER BY sequence",(upload['id'],)):
        data = json.loads(row[0])
        projected.setdefault(data['scope']['section'],{}).update({h['coordinate']:'identity' for h in data['headers'].values()})
    for row in conn.execute("""SELECT DISTINCT c.raw_json FROM workflow_candidates c JOIN workflow_events e ON e.subject_id=c.id
        WHERE c.upload_id=? AND c.lifecycle IN ('draft','submitted') AND e.action='manual_projection'""",(upload['id'],)):
        raw = json.loads(row[0])
        for name,loc in raw['evidence'].items():
            target = projected.setdefault(loc['section'], {})
            coord = get_column_letter(loc['column'])+str(loc['row'])
            role = 'result' if name == 'result' else 'standard' if name == 'criterion' else 'identity'
            if target.get(coord) != 'result':
                target[coord] = role
    for sheet in sheets:
        expanded = []
        for region in sheet.get('regions',[]):
            region['source_hash'] = upload['source_hash']
            for d in structural.get(region['id'],[]):
                selected=[c for c in region['coordinates'] if c in d['coordinates']]
                if not selected:
                    continue
                if len(selected)==len(region['coordinates']):
                    region.update(role=d['role'],basis=d['reason'],revision=d['region_revision']+1)
                else:
                    expanded.append({**region,'id':digest([region['id'],'structure',d['role'],selected]),
                        'coordinates':selected,'role':d['role'],'basis':d['reason'],'revision':d['region_revision']+1})
                    region={**region,'coordinates':[c for c in region['coordinates'] if c not in selected]}
            if region['id'] in decisions:
                d = decisions[region['id']]
                region.update(role=d['role'],basis=d['reason'],revision=d['base_revision']+1)
            for role in ('result','identity','standard'):
                consumed = sorted(c for c in region['coordinates'] if projected.get(sheet['section'],{}).get(c) == role)
                if consumed and region['role'] != role:
                    expanded.append({**region,'id':digest([region['id'],'projected',role]), 'coordinates':consumed,
                                     'role':role,'basis':'Explicit manual source projection'})
                    region = {**region,'coordinates':[c for c in region['coordinates'] if c not in consumed]}
            if region['coordinates']:
                expanded.append(region)
        for child in expanded:
            if child['id'] in decisions:
                d=decisions[child['id']]
                child.update(role=d['role'],basis=d['reason'],revision=d['base_revision']+1)
        sheet['regions'] = expanded
    return sheets


def classify(flow,upload_id,request,*,actor,_conn=None,_batch_id=None):
    require(actor,Capability.CONFIRM_FACT)
    conn_ctx = flow.store.connect() if _conn is None else None
    conn = _conn if _conn is not None else conn_ctx.__enter__()
    try:
        if _conn is None:
            conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'classify_region':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        sheets = coverage(conn,upload)
        selected = next(((s,r) for s in sheets for r in s.get('regions',[]) if r['id'] == request.region_id),None)
        if not selected:
            raise WorkflowError('unknown_region','来源区域不存在。')
        sheet,region = selected
        committed = conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=?",(upload_id,sheet['section'])).fetchone()
        if committed or region['role'] == 'result':
            raise WorkflowError('region_final','正式工作表或已投影结果请通过对应结果流程处理。',409)
        ev_payload = request.model_dump(mode='json')
        if _batch_id:
            ev_payload['batch_id'] = _batch_id
        event(conn,project,upload_id,'classify_region',actor,request.reason,ev_payload)
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
    finally:
        if conn_ctx is not None:
            conn_ctx.__exit__(None, None, None)
