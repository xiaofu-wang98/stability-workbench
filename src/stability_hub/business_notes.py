"""Shared deterministic discovery and scope of business notes; never adoption authority."""
import re

from .source_notes import review_text, informational_reference

BUSINESS = re.compile(
    r'\b(?:AR|AE)[-（(]|\b(?:OOS|OOE|OOT)\b|暂停|终止|停检|偏差|复测|更正|修改|修订|'
    r'不汇报|未汇报|异常|未通过|报错|调查|改为|桥接|恢复汇报|根据|检测组提供|发起|'
    r'\b(?:retest(?:ed|ing)?|correct(?:ed|ion)?|replac(?:e[ds]?|ement)|bridg(?:e[ds]?|ing)|investigation|deviation)\b', re.I)
REFERENCES = re.compile(r'(?<![A-Za-z0-9])(?:AR|AE|OOS|OOE|OOT|CC)[-－][A-Za-z0-9]+(?:[-－][A-Za-z0-9]+)*', re.I)
ASSAYS = re.compile(r'渗透压|等电点|肽图|NRCE|RCE|CE-SDS|SEC|icIEF|CZE|DAR|ELISA|游离|N糖|pH|蛋白|活性|内毒素|微粒|颜色|澄清度|外观', re.I)
TIME = re.compile(
    r'(?<![\d.])(?:(?P<pre>[MD])\s*(?P<pn>\d+)(?![\d.])|(?P<n>\d+)\s*'
    r'(?P<unit>months?\b|个?月|天|days?\b|[MD](?![A-Za-z0-9]))|第(?P<cn>[一二三四五六七八九十百零两\d]+)个?月)', re.I)


def is_business_note(text):
    return bool(BUSINESS.search(review_text(text)))


def identifiers(text):
    return list(dict.fromkeys(m.group().replace('－', '-') for m in REFERENCES.finditer(text)))


def _integer(token):
    if token.isdigit():
        return int(token)
    numbers = dict(zip('零一二三四五六七八九', range(10), strict=True)) | {'两': 2}
    if '十' in token:
        a, b = token.split('十', 1)
        return (numbers.get(a, 1) * 10 + numbers.get(b, 0)) if len(a) <= 1 and len(b) <= 1 else None
    return numbers.get(token)


def time_references(text):
    """Explicit sets and bounded ranges, preserving days vs months."""
    text = REFERENCES.sub('', text)
    matches = list(TIME.finditer(text))
    values = []
    for m in matches:
        number = _integer(m['cn']) if m['cn'] else int(m['pn'] or m['n'])
        unit = (m['pre'] or m['unit'] or 'M').casefold()
        values.append((number, 'D' if unit in {'d', 'day', 'days', '天'} else 'M'))
    result = set(values)
    invalid_range = False
    for left, right, a, b in zip(matches, matches[1:], values, values[1:], strict=False):
        if re.fullmatch(r'\s*(?:至|到|[-~～–—])\s*', text[left.end():right.start()]):
            if a[0] is not None and b[0] is not None and a[1] == b[1] and a[0] <= b[0] <= a[0] + 240:
                result.update((n, a[1]) for n in range(a[0], b[0] + 1))
            else:
                invalid_range = True
    for m, (end, unit) in zip(matches, values, strict=True):
        prefix = re.search(r'(?<![\d.])(\d+)\s*([-~～至到、,，])\s*$', text[:m.start()])
        if prefix and end is not None:
            start = int(prefix[1])
            if prefix[2] in '、,，':
                result.add((start, unit))
            elif start <= end <= start + 240:
                result.update((n, unit) for n in range(start, end + 1))
            else:
                invalid_range = True
    # A recognised limit with no usable number is unresolved, never universal.
    unresolved = invalid_range or any(n is None for n, _ in values)
    unresolved |= bool(re.search(r'后续|以后|之后|(?:\d+\s*(?:M|个月|月|天|D))\s*起|'
                                r'following|subsequent|onwards?|\d\s*(?:周|weeks?)|\d+\.\d+\s*(?:M|个月|月|D|天)', text, re.I))
    return result, unresolved


def note_scope(line, observations):
    """Return source indexes only where the written restrictions can be established."""
    from .matrix_fields import method_text, month_token, purity_subresults, item_text
    body = review_text(line)
    if not is_business_note(body) or informational_reference(body):
        return set()
    body = REFERENCES.sub('', body)
    times, uncertain = time_references(body)
    if uncertain:
        return set()
    labels = []
    for raw in observations:
        parts = [raw.fields.get('test_item', ''), raw.fields.get('method', ''),
                 item_text(raw.fields.get('test_item', ''), raw.fields.get('criterion', ''),
                           raw.fields.get('method', ''), raw.fields.get('subresult', ''))]
        names = {p.strip() for part in parts for p in re.split(r'\n| · ', part) if p.strip()}
        names.update(purity_subresults(raw.fields.get('criterion', '')))
        names.update(m.group().casefold() for part in parts for m in ASSAYS.finditer(part))
        canonical = method_text(raw.fields.get('method', '') or raw.fields.get('test_item', ''), embedded=True)
        if canonical:
            names.add(canonical)
        labels.append(names)
    matched = set()
    for names in labels:
        for name in names:
            pattern = re.escape(name)
            if name.isascii():
                pattern = r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])'
            if re.search(pattern, body, re.I):
                matched.add(name)
    method = method_text(body, embedded=True)
    if method:
        matched.add(method)
    mentioned_assays = {m.group().casefold() for m in ASSAYS.finditer(body)}
    matched.update(mentioned_assays)
    bilingual = re.sub(r'(?<=[A-Za-z])(?=[\u3400-\u9fff])|(?<=[\u3400-\u9fff])(?=[A-Za-z])', ' ', body)
    mentioned_purity = set(purity_subresults(bilingual))
    matched.update(mentioned_purity)
    # Unknown explicit assay/batch qualifiers cannot turn into a table-wide action.
    cleaned = REFERENCES.sub('', TIME.sub('', body))
    unknown_ascii = re.findall(r'^\s*([A-Z][A-Z0-9-]{1,})(?![A-Za-z0-9])', cleaned)
    recognised = {v.casefold() for names in labels for v in names} | {'oos', 'ooe', 'oot', 'ar', 'ae', 'cc'}
    if any(token.casefold() not in recognised and not any(token.casefold() in v for v in recognised)
           for token in unknown_ascii):
        return set()
    qualifier = re.search(r'([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_-]{1,25})(?:结果|检项)', cleaned)
    if qualifier and not matched and qualifier[1] not in {'检测', '检验', '本次', '本表', '所有', '全部', '更正', '复测'}:
        return set()
    if re.search(r'批号|批次|batch|lot\b', cleaned, re.I):
        # Batch-scoped notes require explicit human range selection for now.
        return set()
    result = set()
    for index, raw in enumerate(observations):
        if mentioned_purity and not (set(purity_subresults(raw.fields.get('criterion', ''))) & mentioned_purity):
            continue
        if matched and not ({v.casefold() for v in labels[index]} & {v.casefold() for v in matched}):
            continue
        if '该检项' in body and '*' not in raw.fields.get('test_item', ''):
            continue
        token = raw.fields.get('time_point', '')
        day = re.fullmatch(r'\s*(\d+)\s*(?:D|days?|天)\s*', token, re.I)
        point = (int(day[1]), 'D') if day else (month_token(token), 'M')
        if times and point not in times:
            continue
        result.add(index)
    return result
