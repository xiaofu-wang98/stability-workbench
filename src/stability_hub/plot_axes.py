"""Shared display ranges, never acceptance criteria or result transformations."""
import math
import re

from .workflow_common import WorkflowError


def axis_range(values, label, unit, mode='overview', lower=None, upper=None):
    numbers = [float(v) for v in values if v is not None]
    if any(not math.isfinite(v) for v in numbers):
        raise WorkflowError('axis_values', '坐标数据必须是有限数值。')
    lo, hi = (min(numbers), max(numbers)) if numbers else (0., 1.)
    name = label.lower()
    family = 'general'
    if mode == 'manual':
        if lower is None or upper is None or not all(math.isfinite(x) for x in (lower, upper)) or lower >= upper:
            raise WorkflowError('axis_range', '手动范围须填写有限数值，且下限小于上限。')
        if numbers and (lower > lo or upper < hi):
            raise WorkflowError('axis_clipped', '手动范围不能裁掉已选正式数值；请扩大范围。')
        a, b = lower, upper
    elif mode in {'overview', 'detail'}:
        span = hi-lo
        if mode == 'detail':
            pad = max(span*.15, max(abs(lo), abs(hi), 1)*.005)
            a, b = lo-pad, hi+pad
        elif unit.strip() in {'%', '％'} and re.search(r'\bhmw\b|\blmw\b|聚合|高分子|低分子|杂质|impurit|aggregate', name):
            family = 'impurity_percent'
            a, b = min(0., lo), max(5., hi*1.1)
        elif unit.strip() in {'%', '％'} and re.search(r'monomer|单体|纯度|purity|主峰', name):
            family = 'purity_percent'
            a, b = min(90., lo-max(span*.1, 1)), max(100., hi)
        elif re.search(r'(^|[^a-z])ph([^a-z]|$)|酸碱', name):
            family = 'ph'
            a, b = min(0., lo), max(14., hi)
        elif unit.strip() in {'%', '％'}:
            family = 'relative_percent'
            a, b = min(0., lo), max(100., hi*1.1)
        else:
            # A minimum 50% magnitude span prevents narrow autoscaling for unknown assays.
            width = max(span*1.3, max(abs(lo), abs(hi), 1e-12)*.5)
            mid = (lo+hi)/2
            a, b = mid-width/2, mid+width/2
            if lo >= 0 and a < 0:
                a = 0.
        step = 10**math.floor(math.log10(max(b-a, 1e-300)))/5
        a, b = math.floor(a/step)*step, math.ceil(b/step)*step
    else:
        raise WorkflowError('axis_mode', '未知坐标显示模式。')
    return {'mode':mode, 'min':a, 'max':b, 'family':family,
            'note':'显示尺度，不代表质量标准或可比性接受限度。'}
