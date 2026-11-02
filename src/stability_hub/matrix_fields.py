"""Stable, reviewable interpretation of matrix source text; never invent a project item."""
import re

IDENTITY_VERSION = 'v54-identity-1'


def interpret_raw(raw, source=None):
    from .standards import select
    fields = select(raw, raw.fields if source is None else source)
    return interpret_matrix_fields(fields, raw.location.section,
        reference_rules=raw.source_value.get('identity_rule_version') == IDENTITY_VERSION
    ) if raw.adapter == 'stability_matrix' else dict(fields)


def compact(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def method_lines(text):
    """Bilingual method descriptions (Chinese line + English method translation) are equivalent."""
    merged = re.sub(r'(?<=型)\s*[\r\n]+\s*(?=(?:CE[\s-]*SDS|毛细管))', '', str(text or ''), flags=re.I)
    lines = [v.strip() for v in merged.splitlines() if v.strip()]
    if len(lines) != 2:
        return lines
    has_cn = [any('\u4e00' <= ch <= '\u9fff' for ch in line) for line in lines]
    if (has_cn[0] and not has_cn[1]) or (has_cn[1] and not has_cn[0]):
        return lines[:1]
    return lines


def presentation_lines(text):
    """Prove unit/container translation equivalence without merging strengths."""
    lines = [v.strip() for v in str(text).splitlines() if v.strip()]
    def token(value):
        value = re.sub(r'\s+', '', value).casefold()
        value = re.sub(r'(?<=/)(?:bottles?|vials?)\b', '瓶', value)
        value = re.sub(r'(?<=/)bags?\b', '袋', value)
        value = value.replace('毫升','ml').replace('毫克','mg')
        return value
    if len(lines)>1 and len({token(v) for v in lines})==1:
        return lines[:1]
    return lines


def month_token(text):
    match = re.match(r'^\s*(\d{1,3})(?:\s*(?:[Mm]|个?月)|\s*[（(\n\r]|\s*$)', str(text))
    return int(match[1]) if match and int(match[1]) <= 240 else None


def purity_subresults(criterion):
    return [name for pattern, name in [(r'单体|\bMonomer\b', 'Monomer'),
            (r'聚合体|高分子量|\bHMW\b', 'HMW'), (r'低分子量|\bLMW\b', 'LMW')]
            if re.search(pattern, criterion, re.I)]


def purity_conflict(fields):
    """Opposing bilingual labels require an explicit identity, never a numeric guess."""
    criterion = fields.get('criterion', '')
    cn = [name for pattern, name in [(r'单体', 'Monomer'), (r'聚合体|高分子量', 'HMW'), (r'低分子量', 'LMW')]
          if re.search(pattern, criterion)]
    en = re.findall(r'\b(Monomer|HMW|LMW)\b', criterion, re.I)
    en = list(dict.fromkeys({'monomer': 'Monomer', 'hmw': 'HMW', 'lmw': 'LMW'}[s.lower()] for s in en))
    if len(cn) == len(en) == 1 and cn != en:
        parent = fields.get('test_item', '').splitlines()[0].strip()
        method = explicit_method(fields.get('method', ''))
        prefix = ' · '.join(p for p in [parent, compact(method)] if p)
        return {'chinese': cn[0], 'english': en[0],
                'options': {key: prefix + ' · ' + value for key, value in [('chinese', cn[0]), ('english', en[0])]}}
    return None


def method_conflict(text):
    """Only explicit opposing CE-SDS bilingual identities, never inferred from results."""
    text = str(text or '')
    if not re.search(r'CE[\s-]*SDS', text, re.I):
        return None
    chinese = set(re.findall(r'非还原|(?<!非)还原', text))
    english_raw = re.findall(r'(?<![A-Za-z])(NR|R|Non[\s-]*reduced|Reduced)[\s-]*CE[\s-]*SDS', text, re.I)
    english = {'NR' if m.upper().startswith(('NR', 'NON')) else 'R' for m in english_raw}
    mapping = {'非还原': 'NR', '还原': 'R'}
    if len(chinese) == len(english) == 1 and mapping[next(iter(chinese))] != next(iter(english)):
        cn = next(iter(chinese))
        en = next(iter(english))
        return {'chinese_label': cn, 'english_label': en,
                'options': {'chinese': mapping[cn] + ' CE-SDS', 'english': en + ' CE-SDS'}}
    return None


def reporting_basis(text):
    """Explicit pharmacopoeial reporting labels; mentions inside prose are not labels."""
    return set(re.findall(r'^\s*(CHP|EP|USP)\s*[:：]', str(text), re.I | re.M))


def item_text(parent, criterion, method='', subresult='', result=''):
    """Labels distinguish explicit subresults without importing their acceptance limits."""
    parent = re.sub(r'^\s*\d+[.、．]\s*', '', parent).strip()
    first = parent.splitlines()[0].strip() if parent else ''
    sub = ''
    labels = [
        (r'\bHMW\b|高分子量', 'HMW'),
        (r'\bLMW\b|低分子量', 'LMW'),
        (r'\bMonomer\b|单体', 'Monomer'),
        (r'重链\s*[%％]?\s*[+＋]\s*轻链|\bHC\s*[%％]?\s*\+\s*LC\b', 'HC+LC'),
        (r'总体小峰|\bTotal\s+minor\s+species\b', '总体小峰'),
        (r'酸性(?:峰|组分)|\bAcidic\s+Peaks?\b', '酸性峰'),
        (r'碱性(?:峰|组分)|\bBasic\s+Peaks?\b', '碱性峰'),
        (r'主峰\d*的?p[I1l]值.*差值|difference\s+of\s+main\s+peak\s+p[I1l]', '主峰pI差值'),
        (r'主峰等电点|main peak pI', '主峰等电点'),
        (r'主峰|main peak', '主峰'),
        (r'其他峰|\bOthers\b', '其他峰'),
        (r'量效关系曲线|dose-dependent\s+curves?', '量效曲线一致性'),
        (r'标准品或参考品的\s*\d+\s*%|\brelative\s+to\s+(?:interim\s+)?reference', '相对效价'),
    ]
    for pattern, name in labels:
        if re.search(pattern, criterion, re.I):
            sub = name
            break
    purity_labels = purity_subresults(criterion)
    if purity_labels:
        sub = purity_labels[0] if len(purity_labels) == 1 else ''
    # A compound A/B result must stay compound until explicitly resolved by a human.
    if re.search(r'Group\s*A', criterion, re.I) and re.search(r'Group\s*B', criterion, re.I):
        sub = 'Group A / Group B（组合结果）'
    elif re.search(r'(?:≥|>=)\s*10\s*[μµu]m', criterion):
        sub = '≥10μm'
    elif re.search(r'(?:≥|>=)\s*25\s*[μµu]m', criterion):
        sub = '≥25μm'
    elif re.search(r'HGEG|H1-W25|H1-K28|H1-R46|(?<![A-Z])-HG', criterion):
        sub = re.split(r'[:：]', criterion.splitlines()[0])[0].strip()
    if not sub and '图谱一致' in criterion:
        sub = '图谱一致性'
    if not sub and re.search(r'^\s*纯度\s*[%％]?\s*[:：]', criterion):
        sub = '主峰纯度'
    if not sub:
        if '中国申报' in criterion or 'for China' in criterion:
            sub = '中国申报'
        elif '美国申报' in criterion or '欧洲申报' in criterion:
            sub = '美国/欧洲申报'
    if not sub and subresult and len(purity_labels) <= 1:
        first_subresult = next((line.strip() for line in subresult.splitlines() if line.strip()), '')
        cleaned_sub = re.sub(r'^[（(]\s*([^）)]+)\s*[）)]', r'\1', first_subresult)
        label = re.split(r'[:：%％（(≤≥<>]', cleaned_sub, maxsplit=1)[0].strip()
        if (re.search(r'[A-Za-z\u4e00-\u9fff]', label)
                and not re.search(r'报告|符合|不得|应为|^与|^应|report|conform|should', label, re.I)):
            sub = label
    if not sub and re.fullmatch(r'[\s报告数据结果ReportResult(s).:：]+', criterion, re.I) and result:
        if re.search(r'主峰\d*的?p[I1l]值.*差值|difference\s*of\s*main\s*peak\s*p[I1l]', result, re.I):
            sub = '主峰pI差值'
        elif '图谱一致' in result:
            sub = '图谱一致性'
    criterion_basis = {b.upper() for b in reporting_basis(criterion)}
    result_basis = {b.upper() for b in reporting_basis(result)}
    basis = criterion_basis | result_basis
    # Only an unambiguous explicit label can separate rows. A combined result stays combined.
    basis_label = {'CHP': 'ChP', 'EP': 'EP', 'USP': 'USP'}.get(next(iter(basis))) if len(basis) == 1 else ''
    if basis_label and sub.upper() in {'CHP', 'EP', 'USP'}:
        sub = ''
    parts = [first, compact(method), sub, basis_label]
    return ' · '.join(dict.fromkeys(p for p in parts if p))


def method_text(text, embedded=False):
    text = compact(text)
    patterns = [(r'NR\s*CE|nrCE|非还原', 'NR CE-SDS'), (r'(?<!N)R\s*CE|(?<!n)rCE|还原', 'R CE-SDS'),
                (r'SEC(?:-HPLC)?', 'SEC-HPLC'), (r'RP-HPLC', 'RP-HPLC'),
                (r'RP-UPLC', 'RP-UPLC'), (r'icIEF', 'icIEF'), (r'ELISA', 'ELISA'),
                (r'Cell based|cAMP', 'Cell based cAMP'), (r'\bUV\b', 'UV')]
    for pattern, value in patterns:
        if re.search(pattern, text, re.I):
            return value
    if embedded:
        explicit = re.findall(r'[（(]([^()（）]{1,35}法)\s*[）)]', text)
        if len(set(explicit)) == 1:
            return explicit[0].strip().removesuffix('法')
    if not embedded and len(text) <= 60:
        return text.removesuffix('法')
    return ''


def explicit_method(text):
    merged = re.sub(r'(?<=型)\s*[\r\n]+\s*(?=(?:CE[\s-]*SDS|毛细管))', '', str(text or ''), flags=re.I)
    lines = [v.strip() for v in merged.splitlines() if v.strip()]
    interpreted = [re.sub(r'(?:法|\s+Method)$','',v,flags=re.I).strip() for v in lines]
    if len(set(interpreted)) == 1 and interpreted[0:1]:
        return interpreted[0]
    return '\n'.join(lines)


def selected_condition(text):
    """Only checked form options, or one unambiguous legacy temperature, are usable."""
    text = re.sub(r'°\s*C', '℃', str(text), flags=re.I).replace('−','-')
    markers = list(re.finditer(r'[□£√✓☑]|(?<![A-Za-z])R(?=[\d\-其他])', text))
    if markers:
        chosen, humidity, placement = [], [], []
        for index, marker in enumerate(markers):
            if marker.group() in {'□', '£'}:
                continue
            fragment = text[marker.end():markers[index+1].start() if index+1 < len(markers) else len(text)]
            # A selected temperature and optional selected RH/light describe the condition.
            humidity.extend(re.findall(r'\d+(?:\.\d+)?\s*%RH(?:\s*±\s*\d+(?:\.\d+)?\s*%RH)?', fragment, re.I))
            if re.search(r'避光|protect from light', fragment, re.I):
                placement.append('避光')
            for pattern, label in [(r'正置|Upright', '正置'), (r'倒置|Inverted', '倒置'), (r'平放|Horizontal', '平放')]:
                if re.search(pattern, fragment, re.I):
                    placement.append(label)
            fragment = re.split(r'相对湿度|Relative humidity|放置方式|Placement', fragment, flags=re.I)[0]
            temperatures = re.findall(r'-?\d+(?:\.\d+)?\s*(?:[~～—-]\s*\d+(?:\.\d+)?)?\s*℃(?:\s*±\s*\d+(?:\.\d+)?\s*℃?)?', fragment)
            chosen.extend(compact(v).replace(' ', '') for v in temperatures)
        unique = list(dict.fromkeys(chosen))
        return ' · '.join([unique[0], *dict.fromkeys(compact(v).replace(' ', '') for v in humidity), *dict.fromkeys(placement)]) if len(unique) == 1 else ''
    # Restrict legacy text to the title parentheses, not start dates or other narrative.
    temperatures = re.findall(r'-?\d+(?:\.\d+)?\s*(?:[~～—-]\s*\d+(?:\.\d+)?)?\s*℃(?:\s*±\s*\d+(?:\.\d+)?\s*℃?)?', text)
    if len(set(temperatures)) != 1:
        return ''
    parts = [compact(temperatures[0]).replace(' ', '')]
    parts.extend(dict.fromkeys(compact(v).replace(' ', '') for v in re.findall(
        r'\d+(?:\.\d+)?\s*%RH(?:\s*±\s*\d+(?:\.\d+)?\s*%RH)?', text, re.I)))
    if re.search(r'(?<!不)避光|protect from light', text, re.I):
        parts.append('避光')
    for pattern,label in [(r'正置|\bupright\b','正置'),(r'倒置|\binverted\b','倒置'),(r'平放|\bhorizontal\b','平放')]:
        if re.search(pattern,text,re.I):
            parts.append(label)
    return ' · '.join(parts)


def interpret_matrix_fields(source, section, *, reference_rules=True):
    """Raw fields remain exact source strings. This interpretation precedes human corrections."""
    fields = dict(source)
    title = source.get('study_title', '')
    product = source.get('sample_type', '')
    kind_text = product + ' ' + title + ' ' + section
    if reference_rules:
        fields['purpose'] = source.get('purpose') or ('reference' if re.search(
            r'参考品|对照品|\breference\s+(?:standard|material)\b', kind_text, re.I) else 'unknown')
    ds = bool(re.search(r'原液|(?<![A-Za-z])DS(?![A-Za-z])', kind_text, re.I))
    dp = bool(re.search(r'制剂|注射液|注射器|注射笔|(?<![A-Za-z])DPX?(?![A-Za-z])', kind_text, re.I))
    fields['sample_type'] = 'placebo' if re.search(r'安慰剂|placebo', product, re.I) else 'DS' if ds and not dp else 'DP' if dp and not ds else ''
    batch = compact(re.sub(r'(?<=-)\s*[\r\n]+\s*|\s*[\r\n]+\s*(?=-)', '', source.get('batch', '')))
    batch = re.sub(r'^批号\s*[:：]\s*', '', batch)
    # Alternative identifiers in parentheses are left for explicit identity correction.
    fields['batch'] = batch if re.fullmatch(r'[A-Za-z0-9_-]+', batch) else ''
    study_text = source.get('study', '') or (title + ' ' + section)
    if re.search(r'[R√✓☑]\s*长期', study_text):
        fields['study'] = 'long_term'
    elif re.search(r'[R√✓☑]\s*加速', study_text):
        fields['study'] = 'accelerated'
    elif '□' not in study_text and '£' not in study_text:
        fields['study'] = 'accelerated' if '加速' in study_text else 'long_term' if '长期' in study_text or (not reference_rules and '稳定性研究' in study_text) else ''
    else:
        fields['study'] = ''
    fields['condition'] = selected_condition(source.get('condition', '') or title)
    protocol = re.sub(r'\s+', '', source.get('study_id', '') or title)
    ids = re.findall(r'STB\([A-Za-z]+\)-[A-Za-z0-9.-]+', protocol)
    fields['study_id'] = ids[0].rstrip('）).') if len(set(ids)) == 1 else ''
    time = month_token(source.get('time_point', ''))
    if time is None and source.get('sample_info'):
        times = re.findall(r'(?<![\d.])(\d{1,3})\s*M\b', source['sample_info'], re.I)
        time = int(times[0]) if len(times) == 1 else None
    day = re.fullmatch(r'\s*(\d+)\s*(?:天|[Dd]|days?)\s*', source.get('time_point', ''), re.I)
    fields['time_point'] = f'{int(day[1])}D' if day else f'{time}M' if time is not None else ''
    method_source = source.get('method', '') or source.get('test_item', '')
    method = explicit_method(source['method']) if source.get('method') else method_text(method_source, embedded=True)
    fields['method'] = method
    fields['test_item'] = item_text(source.get('test_item', ''), source.get('criterion', ''), method, source.get('subresult', ''), source.get('result', ''))
    version = compact(source.get('method_version', ''))
    if version:
        fields['method_version'] = version
    # Acceptance units can describe a result, but numeric acceptance limits never become results.
    criterion = source.get('criterion', '')
    unit = re.search(r'mg/ml|mg/mL|EU/mg|cfu/10\s*ml|mOsmol/kg', criterion, re.I)
    fields['unit'] = unit.group() if unit else '%' if '%' in criterion or '％' in criterion else ''
    fields['presentation'] = str(source.get('presentation','')).strip()
    return fields
