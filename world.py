# ------------------------------------------------------------
#  3D guides: world-space geometry
# ------------------------------------------------------------
#
#  A 3D guide is an infinite LINE in world space: a point it passes through
#  and a (unit) direction. It lives on the scene, so it is the same in every
#  view and does not move when you orbit. In a view it is drawn as the
#  screen-space line that results from projecting it; the same hover / drag /
#  snap machinery as the 2D guides works on that projection, and the result
#  is mapped back into world space.
#
#  In the sidebar the direction is also shown as two angles:
#    heading  rotation about world Z, measured from +X towards +Y
#    pitch    elevation above the XY plane (90 = straight up)

import math

import numpy as np
from mathutils import Quaternion, Vector
from mathutils import geometry as mu_geometry

from .common import (
    HANDLE_DIST, clip_segment_to_rect, get_region_3d, get_view_region, view_forward,
)


LINE3_T = 1.0e5     # how far (world units) either way a 3D guide is followed
LINE3_EPS = 1.0e-4  # clip-space w below this counts as behind the camera

AXIS_COLORS = {
    'X': (0.90, 0.25, 0.25),
    'Y': (0.45, 0.80, 0.25),
    'Z': (0.30, 0.50, 1.00),
}
AXIS_VECTORS = {'X': (1.0, 0.0, 0.0), 'Y': (0.0, 1.0, 0.0), 'Z': (0.0, 0.0, 1.0)}


def direction_to_angles(d):
    """Unit direction -> (heading, pitch) in radians. Heading is None when
    the line is vertical (any heading describes it)."""
    x, y, z = d
    pitch = math.asin(max(-1.0, min(1.0, z)))
    if math.hypot(x, y) < 1e-5:          # float32 storage leaves ~1e-8 behind
        return None, pitch
    return math.atan2(y, x), pitch


def angles_to_direction(heading, pitch):
    c = math.cos(pitch)
    return (c * math.cos(heading), c * math.sin(heading), math.sin(pitch))


def view_projection(context):
    """(perspective matrix as float64 4x4, width, height) of the 3D view."""
    rv3d, region = get_region_3d(context), get_view_region(context)
    if rv3d is None or region is None:
        return None
    return (np.array(rv3d.perspective_matrix, dtype=np.float64),
            float(region.width), float(region.height))


def project_point(proj, p):
    """World point -> region pixel (x, y), or None if behind the camera."""
    M, W, H = proj
    c = M @ np.array([p[0], p[1], p[2], 1.0])
    if c[3] <= LINE3_EPS:
        return None
    return (float((c[0] / c[3] * 0.5 + 0.5) * W), float((c[1] / c[3] * 0.5 + 0.5) * H))


def project_points(proj, pts):
    """(N,3) world points -> ((N,2) region px, (N,) in-front mask)."""
    M, W, H = proj
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    w = pts @ M[3, :3] + M[3, 3]
    x = pts @ M[0, :3] + M[0, 3]
    y = pts @ M[1, :3] + M[1, 3]
    valid = w > LINE3_EPS
    with np.errstate(all='ignore'):
        sx = (x / w * 0.5 + 0.5) * W
        sy = (y / w * 0.5 + 0.5) * H
    return np.stack([sx, sy], axis=1), valid


def line3_screen(proj, point, direction):
    """Screen-space picture of an infinite 3D line.

    -> dict(A, B, d, n, anchor) or None when it cannot be seen. A and B are the
    ends of the visible part (A..B follows the +direction); they can lie
    far outside the region, clip them with clip_segment_to_rect. d / n are the
    unit screen direction and normal. anchor is where `point` shows on screen
    (None if it is behind the camera).
    """
    M, W, H = proj
    p = np.array([point[0], point[1], point[2], 1.0])
    dv = np.array([direction[0], direction[1], direction[2]], dtype=np.float64)
    c0 = M @ p
    c1 = M[:, :3] @ dv
    w0, w1 = c0[3], c1[3]
    lo, hi = -LINE3_T, LINE3_T
    if abs(w1) < 1e-12:
        if w0 <= LINE3_EPS:
            return None
    elif w1 > 0:
        lo = max(lo, (LINE3_EPS - w0) / w1)
    else:
        hi = min(hi, (LINE3_EPS - w0) / w1)
    if lo >= hi:
        return None

    def scr(t):
        c = c0 + c1 * t
        return (float((c[0] / c[3] * 0.5 + 0.5) * W), float((c[1] / c[3] * 0.5 + 0.5) * H))

    A, B = scr(lo), scr(hi)
    dx, dy = B[0] - A[0], B[1] - A[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    d = (dx / length, dy / length)
    return {"A": A, "B": B, "d": d, "n": (-d[1], d[0]),
            "anchor": scr(0.0) if (lo <= 0.0 <= hi) else None}


def guide3_info(proj, g3, bounds):
    """line3_screen() for a 3D guide plus its clipped segment: adds
    "seg" = (A, B) clipped to bounds (or None when it misses the frame)."""
    info = line3_screen(proj, tuple(g3.point), tuple(g3.direction))
    if info is None:
        return None
    info["seg"] = clip_segment_to_rect(info["A"], info["B"], bounds)
    return info


def guide3_handles(proj, g3, bounds):
    """Screen positions of a 3D guide's two points (A, B); either is None
    when that point is behind the viewer or outside the frame."""
    x0, y0, x1, y1 = bounds

    def place(p):
        s = project_point(proj, p)
        if s is None or not (x0 <= s[0] <= x1 and y0 <= s[1] <= y1):
            return None
        return s

    return place(tuple(g3.point)), place(tuple(g3.point_b))


def set_guide_points(g3, a, b):
    """Make a 3D guide run from point a to point b."""
    v = Vector(b) - Vector(a)
    g3.point = tuple(a)
    if v.length > 1e-6:
        g3.direction = tuple(v.normalized())
        g3.length = v.length


def handle_length(context, a, d):
    """A Point B distance (world units) that puts B a comfortable handle's
    distance from A on screen in the current view. 1.0 if unknown."""
    proj = view_projection(context)
    if proj is None:
        return 1.0
    pa = project_point(proj, tuple(a))
    pb = project_point(proj, tuple(Vector(a) + Vector(d)))
    if pa is None or pb is None:
        return 1.0
    px = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    if px < 1e-6:
        return 1.0
    return max(1e-3, min(1e4, HANDLE_DIST * 1.5 / px))


def line_through_target(a, d, target):
    """Translate a line (point a, unit direction d) so it passes through
    `target`, by the smallest possible move. -> new point."""
    return target - d * (target - a).dot(d)


def point_on_line_under_ray(ro, rd, a, d):
    """The point of the line (a, unit d) that lies on the view ray (ro, rd),
    i.e. where the ray and the line come closest. None if (nearly) parallel."""
    res = mu_geometry.intersect_line_line(ro, ro + rd, a, a + d)
    if res is None:
        return None
    return res[1]


def view_axis(context):
    """Unit vector from the scene toward the viewer."""
    return -view_forward(get_region_3d(context))


def rotate_about(v, axis, angle):
    """v rotated by angle (radians, counter-clockwise looking down `axis`
    toward the origin) about the unit vector axis."""
    return Quaternion(axis, angle) @ v


def snap_to_world_grid(p, step):
    if step <= 0:
        return Vector(p)
    return Vector((round(p.x / step) * step, round(p.y / step) * step, round(p.z / step) * step))


def bbox_key_points(lo, hi):
    """Corners, face centres and centre of an axis-aligned box (Vectors)."""
    lo, hi = Vector(lo), Vector(hi)
    mid = (lo + hi) / 2.0
    pts = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    pts += [Vector((lo.x, mid.y, mid.z)), Vector((hi.x, mid.y, mid.z)),
            Vector((mid.x, lo.y, mid.z)), Vector((mid.x, hi.y, mid.z)),
            Vector((mid.x, mid.y, lo.z)), Vector((mid.x, mid.y, hi.z)), mid]
    return pts
