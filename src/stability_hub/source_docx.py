"""Deterministic physical OOXML table archive, without business interpretation."""
import hashlib
import io
import posixpath
from zipfile import ZipFile

from lxml import etree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'


def xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def text_of(cell):
    """Keep paragraph boundaries, tabs, math text and explicit line breaks."""
    values = []
    for node in cell.iter():
        if node.tag in (W + 't', M + 't'):
            values.append(node.text or '')
        elif node.tag == W + 'tab':
            values.append('\t')
        elif node.tag in (W + 'br', W + 'cr'):
            values.append('\n')
        elif node.tag == W + 'p' and values:
            values.append('\n')
    return ''.join(values)


def archive_tables(data):
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('原件不得超过20MB')
    tables = []
    with ZipFile(io.BytesIO(data)) as book:
        if len(book.namelist()) != len(set(book.namelist())):
            raise ValueError('DOCX包含重复部件名')
        if sum(i.file_size for i in book.infolist()) > 150 * 1024 * 1024:
            raise ValueError('解压后文档过大')
        parts = sorted(n for n in book.namelist() if n.startswith('word/') and n.endswith('.xml'))
        if 'word/document.xml' not in parts:
            raise ValueError('缺少Word正文')
        parts.remove('word/document.xml')
        parts.insert(0, 'word/document.xml')
        for part in parts:
            root = xml(book.read(part))
            elements = list(root.iter(W + 'tbl'))
            relpath = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
            rels = {n.get('Id'): n for n in xml(book.read(relpath))} if relpath in book.namelist() else {}
            ids = {element: f'table-{len(tables) + i + 1}' for i, element in enumerate(elements)}
            for ti, table in enumerate(elements, 1):
                prior = table.getprevious()
                caption = text_of(prior) if prior is not None and prior.tag == W + 'p' else ''
                notes = []
                following = table.getnext()
                while following is not None and following.tag != W+'tbl':
                    if following.tag == W+'p' and text_of(following):
                        notes.append(text_of(following))
                    following = following.getnext()
                parent = next((p for p in table.iterancestors(W + 'tbl')), None)
                rows, active = [], {}
                for ri, row in enumerate(table.findall(W + 'tr'), 1):
                    before = row.find(W + 'trPr/' + W + 'gridBefore')
                    col = int(before.get(W + 'val')) + 1 if before is not None else 1
                    cells = []
                    for ci, cell in enumerate(row.findall(W + 'tc'), 1):
                        span = cell.find(W + 'tcPr/' + W + 'gridSpan')
                        colspan = int(span.get(W + 'val')) if span is not None else 1
                        merge = cell.find(W + 'tcPr/' + W + 'vMerge')
                        continuation = merge is not None and merge.get(W + 'val') != 'restart'
                        refs = []
                        for n in cell.iter():
                            for attr, rid in n.attrib.items():
                                if attr.startswith(R) and rid in rels:
                                    rel = rels[rid]
                                    target = rel.get('Target', '')
                                    external = rel.get('TargetMode') == 'External'
                                    path = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                                    if target.startswith('/'):
                                        path = target.lstrip('/')
                                    content = book.read(path) if not external and path in book.namelist() else None
                                    refs.append({'id': rid, 'target': target, 'external': external,
                                                 'part': path, 'sha256': hashlib.sha256(content).hexdigest() if content else None})
                        value = {'coord': f'R{ri}C{ci}', 'row': ri, 'column': ci, 'grid_column': col,
                                 'text': text_of(cell), 'colspan': colspan, 'rowspan': 1,
                                 'continuation': continuation, 'anchor': None,
                                 'xml_sha256': hashlib.sha256(etree.tostring(cell)).hexdigest(), 'references': refs,
                                 'formula': '\n'.join(text_of(m) for m in cell.iter(M + 'oMath')),
                                 'has_image': any(ref['part'].lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.emf', '.wmf')) for ref in refs), 'is_blank': not text_of(cell)}
                        if continuation:
                            anchor = active.get(col)
                            if anchor:
                                anchor['rowspan'] += 1
                                value['anchor'] = anchor['coord']
                        elif merge is not None:
                            active[col] = value
                        else:
                            active.pop(col, None)
                        cells.append(value)
                        col += colspan
                    rows.append(cells)
                tables.append({'table_id': ids[table], 'table_index': len(tables) + 1,
                               'part': part, 'section': f'ooxml:{part}#{ti}',
                               'parent_table_id': ids.get(parent), 'caption': caption or f'物理表 {len(tables)+1}',
                               'rows': rows, 'headers': [], 'footnotes': notes,
                               'xml_sha256': hashlib.sha256(etree.tostring(table)).hexdigest()})
    return tables


def make_package(data):
    return {'format_version': 1, 'source_hash': hashlib.sha256(data).hexdigest(),
            'extractor_version': 'hub-ooxml-1', 'tables': archive_tables(data), 'categories': {}, 'projections': []}
