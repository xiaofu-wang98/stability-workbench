"""Structural Excel matrix adapter. Source coordinates/text are preserved, never adopted here."""
import re
import calendar
from datetime import date, datetime

from .matrix_fields import compact, item_text, month_token, purity_subresults
from .models import Location, RawObservation
from .normalize import is_not_reported
from .merge_layout import MERGE_HAZARD, vertical_padding


def chart_category_cells(sheet):
    """Explicit local chart axes, not all numeric rows near charts."""
    from openpyxl.utils.cell import range_boundaries
    result = set()
    for chart in sheet._charts:
        for node in chart.to_tree().iter():
            if node.tag.rsplit('}',1)[-1] not in {'cat','xVal'}:
                continue
            for formula in node.iter():
                if formula.tag.rsplit('}',1)[-1]!='f' or not formula.text or '!' not in formula.text:
                    continue
                title,area=formula.text.rsplit('!',1)
                if title.strip("'").replace("''", "'")!=sheet.title:
                    continue
                try:
                    x1,y1,x2,y2=range_boundaries(area.replace('$',''))
                except ValueError:
                    continue
                result.update((r,c) for r in range(y1,y2+1) for c in range(x1,x2+1))
    return result


ITEM_HEADERS = {'检测项目', '检验项目', 'testitem'}
FOOTER = re.compile(r'^(备注|说明|注[:：]|取样量|取样数量|取样日期|复测备用量|考察项目|备用量|总数量|总量|合计|签字|审核|复核|检验人|检测人|总计)')


def source_cells(sheet):
    # Normal-mode openpyxl is sparse until iter_rows() expands the styled rectangle.
    # Some real files have formatting through column 15,255: never iterate that rectangle.
    return {(c.row, c.column): c for c in sheet._cells.values() if c.value is not None}


def text(cell):
    return '' if cell is None or cell.value is None else str(cell.value)


def header_label(cell):
    """Read the source's first header line; a translated second line is not a new column."""
    first = text(cell).splitlines()[0].strip() if text(cell) else ''
    label = re.match(r'^(检测项目|检验项目|方法编号|方法|项目编号|产品名称|产品批号|样品批号|批号|开始日期|规格)(?=\s|[:：A-Za-z（(]|$)', first)
    return label[1] if label else re.sub(r'\s+', '', first).strip(':：').casefold()


def parse_matrix_sheet(sheet):
    cells = source_cells(sheet)
    if not cells:
        return None
    by_row = {}
    for (row, col), cell in cells.items():
        by_row.setdefault(row, {})[col] = cell
    headers = []
    for row, values in sorted(by_row.items()):
        item = [col for col, c in values.items() if header_label(c) in ITEM_HEADERS or
                ('Test Item' in text(c) and ('检验项目' in text(c) or '检测项目' in text(c)))]
        times = [col for col, c in values.items() if ('月' in text(c) or 'month' in text(c).casefold())
                 and ('时间' in text(c) or 'timepoint' in text(c).casefold())
                 or '考察时段' in text(c) or 'survey period' in text(c).casefold()]
        if item and times:
            headers.append((row, min(item), min(times)))
    if not headers:
        return parse_result_panels(sheet, cells)
    merges = tuple(sheet.merged_cells.ranges)

    def anchor(row, col):
        for area in merges:
            if area.min_row <= row <= area.max_row and area.min_col <= col <= area.max_col:
                return cells.get((area.min_row, area.min_col)), area
        return cells.get((row, col)), None

    def loc(cell):
        return Location(format='xlsx', section=sheet.title, row=cell.row, column=cell.column)

    observations, blocks, blank = [], [], 0
    occupied_rows = sorted(by_row)
    seen_results = set()
    physical_results = set()
    chart_axes = chart_category_cells(sheet)
    for block_index, (header, item_col, time_col) in enumerate(headers):
        next_header = headers[block_index+1][0] if block_index+1 < len(headers) else max(by_row)+1
        time_anchor, time_merge = anchor(header, time_col)
        # The merged time caption bounds the real result region; chart helper tables to its right are excluded.
        last_col = time_merge.max_col if time_merge else max(by_row.get(header+1, {time_col: None}))
        months = {}
        for col in range(time_col, last_col+1):
            c = cells.get((header+1, col))
            if c is not None and month_token(text(c)) is not None:
                months[col] = c
        if not months:
            raise ValueError(f'{sheet.title} 第{header+1}行未识别出月龄列，请核对表头。')
        first_result = min(months)
        if last_col-first_result > 512:
            raise ValueError(f'{sheet.title} 时间区域超过512列，请核对合并表头。')
        # Preserve results under missing/unknown headers as unresolved candidates,
        # rather than silently dropping an entire time column.
        for col in range(first_result, last_col+1):
            months.setdefault(col, cells.get((header+1, col)) or sheet.cell(header+1, col))
        # Legacy headers can have two versions of quality criteria before the first month.
        method_cols = [col for col, c in by_row[header].items() if header_label(c) in {'方法', 'method'}]
        version_cols = [col for col, c in by_row[header].items() if header_label(c) == '方法编号']
        method_col = method_cols[0] if method_cols else None
        version_col = version_cols[0] if version_cols else None
        criterion_col = first_result-1
        standard_cols = [col for col, c in by_row[header].items()
                         if '质量标准' in text(c) or '可接受标准' in text(c) or 'Acceptable Criteria' in text(c)]
        identity_limit = method_col if method_col is not None else min(standard_cols) if standard_cols else time_col-1
        metadata = {}
        search_start = max(1, header-6, headers[block_index-1][0]+2 if block_index else 1)
        for row in range(search_start, header):
            for col, c in sorted(by_row.get(row, {}).items()):
                label = text(c)
                _, label_area = anchor(row, col)
                following = cells.get((row, label_area.max_col+1 if label_area else col+1))
                field = None
                if '项目编号' in label or 'Project No.' in label:
                    field = 'project'
                elif ('产品批号' in label or 'Product Batch' in label or '样品批号' in label
                      or header_label(c) in {'批号', 'batchno.', 'samplelotnumber'}):
                    field = 'batch'
                elif '产品名称' in label or 'Product Name' in label:
                    field = 'sample_type'
                elif compact(label) in {'规格', '规格 Specification'} or 'Specification' in label and '规格' in label:
                    field = 'presentation'
                elif '试验方案编号' in label or 'Study Protocol No.' in label:
                    field = 'study_id'
                elif '考察条件' in label or 'Study Condition' in label or '实验条件' in label:
                    field = 'condition'
                elif '稳定性类型' in label or 'Stability Type' in label:
                    field = 'study'
                if field == 'batch' and re.fullmatch(r'[ \t]*(?:样品|产品)?批号[ \t]*[:：][ \t]*[A-Za-z0-9_-]+[ \t]*', label):
                    metadata[field] = c
                elif field and following is not None:
                    metadata[field] = following
                if field == 'condition' and re.search(r'[-−]?\d+\s*[℃°]', label):
                    metadata[field] = c  # Some legacy captions include their value in the same cell.
                if col == item_col and '稳定性' in label and '项目编号' not in label and not field:
                    metadata['study_title'] = c
        if 'batch' not in metadata:
            c = cells.get((header-1, item_col))
            if c is not None:
                metadata['batch'] = c
        if 'study_title' not in metadata:
            c = cells.get((header-2, item_col))
            if c is not None and '稳定性' in text(c):
                metadata['study_title'] = c
        block_start = len(observations)
        block_count, end_row = 0, header+1
        planned = {}
        planning_notes = []
        metadata_start = next((r for r in range(header+2, next_header)
                               if header_label(cells.get((r, item_col))) in
                               {'项目编号', '产品名称', '产品批号', '样品批号', '批号', '开始日期'}), next_header)
        for row in range(header+2, metadata_start):
            leading = text(cells.get((row, item_col))).strip()
            from .source_notes import explicit_annotation
            if FOOTER.search(leading) or explicit_annotation(leading) or ('稳定性' in leading and any(word in leading for word in ('实验结果', '实验数据', '考察方案', '取样方案'))):
                break
            parent_cells = []
            for col in range(item_col, identity_limit):
                c, _ = anchor(row, col)
                if c is not None and text(c).strip():
                    parent_cells.append(c)
            criterion, _ = anchor(row, criterion_col)
            method, _ = anchor(row, method_col) if method_col else (None, None)
            version, _ = anchor(row, version_col) if version_col else (None, None)
            # A second header line often contains scheduled sampling dates under the months.
            # Merged Test Item / Criteria captions are still header cells, not assay names.
            if row <= header+3 and all(c.row <= header for c in parent_cells) and (
                    criterion is None or criterion.row <= header):
                date_cells = [cells.get((row, col)) for col in months]
                filled = [c for c in date_cells if c is not None and text(c).strip()]
                if filled and all(c.data_type == 'f' and re.match(r'^=?EDATE\(', text(c), re.I)
                                  or isinstance(c.value, (date, datetime))
                                  or re.match(r'^\d{4}[/.-]\d{1,2}[/.-]\d{1,2}', text(c)) for c in filled):
                    for col in months:
                        c = cells.get((row, col))
                        if c is None or not text(c).strip():
                            continue
                        planned[col] = c
                        formula = re.fullmatch(r'=?EDATE\(\s*\$?([A-Z]+)\$?(\d+)\s*,\s*(\$?[A-Z]+\$?\d+|\d+)\s*\)', text(c), re.I)
                        if formula:
                            start = sheet[f'{formula[1]}{formula[2]}'].value
                            offset = sheet[formula[3].replace('$', '')].value if re.search('[A-Z]', formula[3], re.I) else int(formula[3])
                            month = month_token(text(months[col]))
                            if isinstance(offset, (int, float)) and month is not None and offset != month:
                                planning_notes.append(f'{c.coordinate}：表头为{month}个月，计划日期公式为加{offset:g}个月；请核对计划日期，不改变结果时间点。')
                            if isinstance(start, (datetime, date)) and isinstance(offset, int):
                                total = start.year * 12 + start.month - 1 + offset
                                year, mon = total // 12, total % 12 + 1
                                planning_notes.append(f'{c.coordinate}：计划日期{year:04d}-{mon:02d}-{min(start.day, calendar.monthrange(year, mon)[1]):02d}（按EDATE公式计算；不是实际检测日期）。')
                    continue
            if not parent_cells and method is None:
                # Auxiliary chart tables repeat numeric limits after the real table.
                # Their standalone time row and Lower/Upper Limit rows are not results.
                nearby = ' '.join(text(c) for (r, ccol), c in cells.items() if row <= r <= row+6)
                if any((row,c) in chart_axes for c in months) and re.search(r'Lower\s*Limit|Upper\s*Limit',nearby,re.I):
                    break
                if re.search(r'Lower\s*Limit|Upper\s*Limit', text(criterion), re.I):
                    break
                numeric_headers = sum(month_token(text(cells.get((row,c)))) is not None for c in months)
                repeated_axis = numeric_headers >= 3 and all(
                    month_token(text(cells.get((row, c)))) == month_token(text(time))
                    for c, time in months.items() if text(cells.get((row, c))).strip())
                separated = all(not any((r, c) in cells for c in range(item_col, last_col+1))
                                for r in (row-2, row-1))
                if criterion is None and repeated_axis and separated:
                    break  # A separate chart time axis is not a set of assay results.
                if criterion is None and numeric_headers >= 3 and re.search(r'Lower\s*Limit|Upper\s*Limit', nearby, re.I):
                    break
                values = [text(cells.get((row,c))) for c in months]
                if all(not v.strip() or explicit_annotation(v) for v in values):
                    continue
                if not any(v.strip() for v in values) or any(re.search(r'发样|修改|复测|RSD|调查|提供|取样|请验|已签|报错|AR-|AE-|系统适用性未通过', v) for v in values):
                    continue
            # Do not carry an item forward past its explicit merge range.
            parent = parent_cells[-1] if parent_cells else None
            parent_area = anchor(row, parent.column)[1] if parent is not None else None
            # Blank subrows inside an established assay merge remain table evidence.
            # Standalone footer labels cannot move the result boundary.
            if parent_area and parent_area.min_row <= end_row:
                end_row = max(end_row, row)
            for col, time in months.items():
                result, area = anchor(row, col)
                if result is None or not text(result).strip():
                    blank += 1
                    continue
                if result.row <= time.row:
                    continue  # A merge crossing the header is never an assay result.
                end_row = max(end_row, row)
                coord = (result.row, result.column)
                shared_statement = bool(area and area.min_col == area.max_col and area.max_row > area.min_row
                    and area.min_row > header+1 and area.max_row < metadata_start and is_not_reported(text(result))
                    and all(any(anchor(rr, cc)[0] is not None and text(anchor(rr, cc)[0]).strip()
                        for cc in range(item_col, identity_limit)) for rr in range(area.min_row, area.max_row+1)))
                result_key = (*coord, row if shared_statement else 0)
                if result_key in seen_results:
                    continue
                seen_results.add(result_key)
                physical_results.add(coord)
                refs = {**metadata, 'result': result, 'time_point': time}
                if col in planned:
                    refs['planned_date'] = planned[col]
                if parent is not None:
                    refs['test_item'] = parent
                if criterion is not None:
                    refs['criterion'] = criterion
                    if parent_area and parent_area.max_row > parent_area.min_row:
                        refs['subresult'] = criterion
                if method is not None:
                    refs['method'] = method
                if version is not None:
                    refs['method_version'] = version
                if parent_cells and parent_cells[0] != parent:
                    refs['category'] = parent_cells[0]
                hazards = []
                if (area and (area.max_col != area.min_col or area.max_row != area.min_row)
                        and not shared_statement
                        and not vertical_padding([area.min_row, area.min_col, area.max_row, area.max_col], occupied_rows)):
                    hazards.append(MERGE_HAZARD)
                if result.data_type == 'e' or isinstance(result.value, bool):
                    hazards.append('结果格为Excel错误或布尔值，须核对原件。')
                if any(c.data_type == 'f' for name, c in refs.items() if name not in {'result', 'planned_date'}):
                    hazards.append('检测身份或表头含公式，须核对原件。')
                comments = [f'{c.coordinate}: {c.comment.text}' for c in dict.fromkeys(refs.values()) if c.comment]
                hints = []
                if criterion is not None:
                    labels = purity_subresults(text(criterion))
                    if len(labels) > 1:
                        hints.append({'kind':'multiple_subresults', 'text':f'{criterion.coordinate}: 子结果标注包含多个含义（{" / ".join(labels)}），请核对原文并明确检测项目及结果。', 'source':loc(criterion).model_dump()})
                    if (len(labels) <= 1 and 'subresult' in refs and item_text(text(parent),text(criterion),text(method),text(criterion),text(result))
                            == item_text(text(parent),'',text(method))):
                        from .standards import labels as standard_labels
                        if not standard_labels(text(criterion)):
                            hints.append({'kind':'missing_subresult', 'text':f'{criterion.coordinate}: 多行子结果未提供可区分的名称，请核对并明确检测项目及结果。', 'source':loc(criterion).model_dump()})
                hint = ''
                if shared_statement and parent_area and parent_area.max_row > parent_area.min_row:
                    names = [item_text(text(parent), text(anchor(rr, criterion_col)[0]), text(method), text(anchor(rr, criterion_col)[0]))
                             for rr in range(parent_area.min_row, parent_area.max_row+1)]
                    if names.count(names[row-parent_area.min_row]) > 1:
                        hint = f'原表分项{row-parent_area.min_row+1}（未命名）'
                observations.append(RawObservation(
                    system_hints=tuple(hints), result_anchor={'range':[area.min_row,area.min_col,area.max_row,area.max_col]} if area else {},
                    logical_row=row, source_span=(area.min_row,area.min_col,area.max_row,area.max_col) if shared_statement else None,
                    subresult_hint=hint,
                    adapter='stability_matrix', fields={name: text(c) for name, c in refs.items()},
                    evidence={name: loc(c) for name, c in refs.items()}, location=loc(result),
                    comment='\n'.join(comments), formula=text(result) if result.data_type == 'f' else '',
                    number_format=result.number_format, hazards=tuple(hazards)))
                block_count += 1
        if planned:
            blocks.append({'planning_date_row': next(iter(planned.values())).row,
                           'note': '计划日期辅助行已排除，不计入检测结果。', 'planning_notes': planning_notes})
        from .business_notes import is_business_note, note_scope
        notes = [c for (row, col), c in cells.items() if end_row < row < metadata_start and
                 is_business_note(text(c))]
        if notes:
            scope = observations[block_start:]
            lines_by_result = {i: [] for i in range(len(scope))}
            for note in notes:
                for line in text(note).splitlines():
                    for i in note_scope(line, scope):
                        lines_by_result[i].append((note, line.strip()))
            for index in range(block_start, len(observations)):
                raw = observations[index]
                matched = lines_by_result[index - block_start]
                relevant = list(dict.fromkeys(c for c, line in matched))
                matched_lines = [f'{c.coordinate}: {line}' for c, line in matched]
                if not relevant:
                    continue
                extra = {f'block_note_{n}': c for n, c in enumerate(relevant, 1)}
                observations[index] = raw.model_copy(update={
                    'fields': {**raw.fields, **{k: text(c) for k, c in extra.items()}},
                    'evidence': {**raw.evidence, **{k: loc(c) for k, c in extra.items()}},
                    'comment': (raw.comment + ('\n表后说明（未自动修改结果）：\n' + '\n'.join(matched_lines) if matched_lines else '')).strip()})
        blocks.append({'header_row': header, 'end_row': end_row, 'time_columns': list(months), 'result_cells': block_count})
    return observations, {'section': sheet.title, 'adapter': 'stability_matrix', 'blocks': blocks,
                          'result_cells': len(physical_results), 'logical_results': len(observations), 'blank_cells': blank,
                          'outside_result_cells': len(cells)-len(physical_results),
                          'note': '只将矩阵结果区域生成候选；标题、标准、备注及图表辅助数据保存在冻结原件，不作为检测结果。空白不更新历史。'}


def parse_result_panels(sheet, cells):
    """Adjacent batch/info/result panels are a distinct tabular layout, not time matrices."""
    batch_headers = [c for (row, col), c in cells.items() if row == 1 and compact(text(c)) == '样品批号']
    if not batch_headers:
        return None
    observations = []
    max_row = max(row for row, col in cells)
    for batch_header in batch_headers:
        col = batch_header.column
        info_header, result_header = cells.get((1, col+1)), cells.get((1, col+2))
        if compact(text(info_header)) != '样品信息' or '比例' not in text(result_header):
            raise ValueError(f'{sheet.title} 的并列结果表头不完整，请核对。')
        for row in range(2, max_row+1):
            result = cells.get((row, col+2))
            if result is None or not text(result).strip():
                continue
            refs = {'test_item': result_header, 'criterion': result_header, 'result': result}
            batch, info = cells.get((row, col)), cells.get((row, col+1))
            if batch is not None:
                refs['batch'] = batch
            if info is not None:
                refs.update({'sample_info': info, 'study_title': info, 'condition': info, 'time_point': info})
            locations = {name: Location(format='xlsx', section=sheet.title, row=c.row, column=c.column) for name, c in refs.items()}
            comments = [f'{c.coordinate}: {c.comment.text}' for c in dict.fromkeys(refs.values()) if c.comment]
            observations.append(RawObservation(adapter='stability_matrix', fields={k: text(c) for k, c in refs.items()},
                evidence=locations, location=locations['result'], comment='\n'.join(comments),
                formula=text(result) if result.data_type == 'f' else ''))
    return observations, {'section': sheet.title, 'adapter': 'result_panels', 'blocks': [],
        'result_cells': len(observations), 'blank_cells': 0,
        'outside_result_cells': len(cells)-len(observations),
        'note': '并列样品结果表；缺失的样品类型、规格、研究编号及方法仍需人工核对，不从批号猜测。'}
