"""Physical proof for a vertical result whose remaining rows contain no source anchors."""
from bisect import bisect_right
import re


MERGE_HAZARD = '结果格跨行或跨时间点合并，须先明确每个检测结果，不能复制计入。'


def vertical_padding(bounds, occupied_rows):
    if (not isinstance(bounds, (tuple, list)) or len(bounds) != 4
            or any(not isinstance(n, int) for n in bounds)):
        return False
    top, left, bottom, right = bounds
    return (top > 0 and left > 0 and left == right and bottom > top
            and bisect_right(occupied_rows, top) == bisect_right(occupied_rows, bottom))


def inventory(sheets):
    result = {}
    for sheet in sheets:
        cells = {(int(m[2]), m[1]) for region in sheet.get('regions', [])
                 for coord in region.get('coordinates', [])
                 if (m := re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', coord))}
        if len(cells) == sheet.get('physical_nonempty'):
            result[sheet['section']] = (cells, sorted({r for r, _ in cells}))
    return result


def can_keep_single(candidate, proof):
    from openpyxl.utils import get_column_letter
    loc = candidate.location
    cells, rows = proof.get(loc.section, (set(), []))
    bounds = candidate.result_anchor.get('range')
    return ((loc.row, get_column_letter(loc.column)) in cells
            and not candidate.source_span
            and candidate.logical_row in (None, loc.row)
            and vertical_padding(bounds, rows)
            and bounds[:2] == [loc.row, loc.column])
