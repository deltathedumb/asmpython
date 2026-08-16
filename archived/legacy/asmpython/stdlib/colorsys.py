"""colorsys module: color system conversions."""
from __future__ import annotations
import math


def rgb_to_yiq(r: float, g: float, b: float) -> list[float]:
    """Convert RGB to YIQ."""
    y: float = 0.30 * r + 0.59 * g + 0.11 * b
    i: float = 0.74 * (r - b) - 0.27 * (g - b)
    q: float = 0.48 * (r - b) + 0.41 * (g - b)
    return [y, i, q]


def yiq_to_rgb(y: float, i: float, q: float) -> list[float]:
    """Convert YIQ to RGB, clamped to [0, 1]."""
    r: float = y + 0.9468822170900693 * i + 0.6235565819861433 * q
    g: float = y - 0.27478764629897834 * i - 0.6356910791873801 * q
    b: float = y - 1.1085450346420322 * i + 1.7090069284064666 * q

    def clamp(x: float) -> float:
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return x

    return [clamp(r), clamp(g), clamp(b)]


def rgb_to_hls(r: float, g: float, b: float) -> list[float]:
    """Convert RGB to HLS."""
    maxc: float = r
    if g > maxc:
        maxc = g
    if b > maxc:
        maxc = b
    minc: float = r
    if g < minc:
        minc = g
    if b < minc:
        minc = b
    l: float = (minc + maxc) / 2.0
    if minc == maxc:
        return [0.0, l, 0.0]
    s: float = 0.0
    if l <= 0.5:
        s = (maxc - minc) / (maxc + minc)
    else:
        s = (maxc - minc) / (2.0 - maxc - minc)
    rc: float = (maxc - r) / (maxc - minc)
    gc: float = (maxc - g) / (maxc - minc)
    bc: float = (maxc - b) / (maxc - minc)
    h: float = 0.0
    if r == maxc:
        h = bc - gc
    elif g == maxc:
        h = 2.0 + rc - bc
    else:
        h = 4.0 + gc - rc
    h = (h / 6.0) % 1.0
    return [h, l, s]


def _v(m1: float, m2: float, hue: float) -> float:
    hue = hue % 1.0
    if hue < 1.0 / 6.0:
        return m1 + (m2 - m1) * hue * 6.0
    if hue < 0.5:
        return m2
    if hue < 2.0 / 3.0:
        return m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0
    return m1


def hls_to_rgb(h: float, l: float, s: float) -> list[float]:
    """Convert HLS to RGB."""
    if s == 0.0:
        return [l, l, l]
    m2: float = 0.0
    if l <= 0.5:
        m2 = l * (1.0 + s)
    else:
        m2 = l + s - l * s
    m1: float = 2.0 * l - m2
    return [_v(m1, m2, h + 1.0 / 3.0), _v(m1, m2, h), _v(m1, m2, h - 1.0 / 3.0)]


def rgb_to_hsv(r: float, g: float, b: float) -> list[float]:
    """Convert RGB to HSV."""
    maxc: float = r
    if g > maxc:
        maxc = g
    if b > maxc:
        maxc = b
    minc: float = r
    if g < minc:
        minc = g
    if b < minc:
        minc = b
    v: float = maxc
    if maxc == minc:
        return [0.0, 0.0, v]
    s: float = (maxc - minc) / maxc
    rc: float = (maxc - r) / (maxc - minc)
    gc: float = (maxc - g) / (maxc - minc)
    bc: float = (maxc - b) / (maxc - minc)
    h: float = 0.0
    if r == maxc:
        h = bc - gc
    elif g == maxc:
        h = 2.0 + rc - bc
    else:
        h = 4.0 + gc - rc
    h = (h / 6.0) % 1.0
    return [h, s, v]


def hsv_to_rgb(h: float, s: float, v: float) -> list[float]:
    """Convert HSV to RGB."""
    if s == 0.0:
        return [v, v, v]
    i: int = int(h * 6.0)
    f: float = (h * 6.0) - float(i)
    p: float = v * (1.0 - s)
    q: float = v * (1.0 - s * f)
    t: float = v * (1.0 - s * (1.0 - f))
    i = i % 6
    if i == 0:
        return [v, t, p]
    if i == 1:
        return [q, v, p]
    if i == 2:
        return [p, v, t]
    if i == 3:
        return [p, q, v]
    if i == 4:
        return [t, p, v]
    return [v, p, q]
