# Kinetiq -- (c) 2026 sinj0x7 -- MIT License, see LICENSE.txt.
"""
curve_math.py -- pure bezier math for Kinetiq.

No Fusion / Resolve dependencies in this module, so it can be unit-tested
with any plain Python 3 interpreter.

Coordinate model
----------------
The editor works in a normalized "easing" space:

    P0 = (0.0, p0y)   left keyframe   (p0y is usually 0.0)
    C1 = (c1x, c1y)   right handle of the left keyframe
    C2 = (c2x, c2y)   left handle of the right keyframe
    P3 = (1.0, p3y)   right keyframe  (p3y is usually 1.0)

X is always the 0..1 fraction of the segment's time range.
Y is the fraction of the segment's value range (may exceed 0..1 for
overshoot / anticipation curves).
"""

EPS_FLAT = 1e-12


def clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def cubic_point(p0, c1, c2, p3, t):
    """Evaluate a cubic bezier at parameter t. Points are (x, y) tuples."""
    mt = 1.0 - t
    a = mt * mt * mt
    b = 3.0 * mt * mt * t
    c = 3.0 * mt * t * t
    d = t * t * t
    x = a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0]
    y = a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1]
    return x, y


def sample_curve(p0, c1, c2, p3, n=240):
    """Return n+1 points along the cubic bezier, t = 0..1."""
    pts = []
    for i in range(n + 1):
        pts.append(cubic_point(p0, c1, c2, p3, i / float(n)))
    return pts


def segment_to_normalized(t0, v0, t1, v1, rh, lh):
    """Convert one keyframe segment (absolute Fusion coordinates) into
    normalized control points.

    rh -- (x, y) absolute right-handle of the left keyframe, or None
    lh -- (x, y) absolute left-handle of the right keyframe, or None

    Returns (c1, c2, flat) where c1/c2 are (x, y) tuples and flat is True
    when the segment's value range is zero (y-normalization then uses a
    scale of 1.0, i.e. y values are raw value offsets).
    """
    dt = float(t1 - t0)
    if dt <= 0:
        raise ValueError("segment has non-positive duration")
    dv = float(v1 - v0)
    flat = abs(dv) < EPS_FLAT
    scale = 1.0 if flat else dv

    if rh is not None:
        c1 = ((rh[0] - t0) / dt, (rh[1] - v0) / scale)
    else:
        # Fusion's default handle sits 1/3 along a linear segment.
        c1 = (1.0 / 3.0, 1.0 / 3.0)

    if lh is not None:
        c2 = ((lh[0] - t0) / dt, (lh[1] - v0) / scale)
    else:
        c2 = (2.0 / 3.0, 2.0 / 3.0)

    return c1, c2, flat


def normalized_to_segment(t0, v0, t1, v1, p0y, c1, c2, p3y):
    """Convert the edited normalized curve back into absolute Fusion
    coordinates for one segment.

    Returns (new_v0, new_v1, rh_abs, lh_abs):
      new_v0 / new_v1 -- possibly adjusted keyframe values (the endpoint
                         nodes can be dragged vertically in the editor)
      rh_abs          -- (x, y) absolute right handle for the left key
      lh_abs          -- (x, y) absolute left handle for the right key
    """
    dt = float(t1 - t0)
    dv = float(v1 - v0)
    scale = 1.0 if abs(dv) < EPS_FLAT else dv

    new_v0 = v0 + p0y * scale
    new_v1 = v0 + p3y * scale

    # Handle X must stay inside the segment (Fusion requires RH right of its
    # key and LH left of its key).
    c1x = clamp(c1[0], 0.0, 1.0)
    c2x = clamp(c2[0], 0.0, 1.0)

    rh_abs = (t0 + c1x * dt, v0 + c1[1] * scale)
    lh_abs = (t0 + c2x * dt, v0 + c2[1] * scale)
    return new_v0, new_v1, rh_abs, lh_abs
