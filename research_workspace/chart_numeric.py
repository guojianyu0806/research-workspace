"""Finite interpolation for imported finite IEEE-754 values, including extremes."""
import math


def unit_fraction(value, low, high):
    if not all(math.isfinite(v) for v in (value, low, high)) or not low <= value <= high:
        raise ValueError('绘图数值或范围无效，无法生成有限坐标。')
    if low == high or value == low:
        return 0.0
    if value == high:
        return 1.0
    delta = high-low
    if math.isfinite(delta):
        result = (value-low)/delta
    else:
        scale = max(abs(low), abs(high))
        result = (value/scale-low/scale)/(high/scale-low/scale)
    if not math.isfinite(result):
        raise ValueError('数值归一化超出可表示范围。')
    return min(1.0, max(0.0, result))


def finite_lerp(low, high, fraction):
    if not all(math.isfinite(v) for v in (low, high, fraction)) or not 0 <= fraction <= 1:
        raise ValueError('刻度插值参数无效。')
    if fraction == 0:
        return low
    if fraction == 1:
        return high
    scale = max(abs(low), abs(high))
    if not scale:
        return 0.0
    relative = (low/scale)*(1-fraction)+(high/scale)*fraction
    return min(high/scale, max(low/scale, relative))*scale
