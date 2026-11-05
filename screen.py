# ------------------------------------------------------------
#  2D (screen) guides: geometry and the guide-group library
# ------------------------------------------------------------
#
#  A 2D guide is an infinite line clipped to the frame (the camera frame in
#  Camera View, the whole viewport otherwise). It is stored as:
#
#    axis      'H' / 'V'   base direction (0 deg / 90 deg)
#    position  0..1        pivot coordinate ACROSS the base direction
#                          (Y for 'H', X for 'V')
#    offset    0..1        pivot coordinate ALONG the base direction
#    angle     radians     rotation about the pivot, added to the base
#
#  2D guides live in named GROUPS, kept in one library per scene
#  (scene.guidebro.groups). Each camera object, and the plain viewport,
#  POINTS at one group by its uid (Object.guidebro_group /
#  settings.viewport_group). So a group can be reused by several cameras,
#  and a duplicated camera keeps showing the same guides.

import math

from .common import (
    HALF_PI, HANDLE_DIST, TILT_EPS,
    clip_line_to_rect, is_camera_view, tag_redraw_3d_views,
)


# ------------------------------------------------------------
#  Geometry
# ------------------------------------------------------------

def guide_base_angle(guide):
    return 0.0 if guide.axis == 'H' else HALF_PI


def guide_theta(guide):
    """Absolute direction of the line, radians, counter-clockwise from +X."""
    return guide_base_angle(guide) + guide.angle


def guide_angle_abs(guide):
    """Absolute direction wrapped to (-90, 90] degrees (as radians)."""
    a = guide_theta(guide) % math.pi
    if a > HALF_PI + 1e-9:
        a -= math.pi
    return a


def guide_dir(guide):
    th = guide_theta(guide)
    return math.cos(th), math.sin(th)


def guide_normal(guide):
    d = guide_dir(guide)
    return -d[1], d[0]


def guide_is_tilted(guide):
    r = guide_theta(guide) % HALF_PI
    return min(r, HALF_PI - r) > TILT_EPS


def guide_tag(guide):
    """'H', 'V' or '/' - what the guide looks like right now."""
    if guide_is_tilted(guide):
        return "/"
    a = guide_theta(guide) % math.pi
    return "H" if (a < HALF_PI / 2 or a > 3 * HALF_PI / 2) else "V"


def guide_pivot_uv(guide):
    """Pivot as normalised (u, v) within the frame."""
    if guide.axis == 'H':
        return guide.offset, guide.position
    return guide.position, guide.offset


def set_guide_pivot_uv(guide, u, v):
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    if guide.axis == 'H':
        guide.offset = u
        guide.position = v
    else:
        guide.position = u
        guide.offset = v


def guide_pivot_px(guide, bounds):
    x_min, y_min, x_max, y_max = bounds
    u, v = guide_pivot_uv(guide)
    return x_min + u * (x_max - x_min), y_min + v * (y_max - y_min)


def set_guide_pivot_px(guide, bounds, x, y):
    x_min, y_min, x_max, y_max = bounds
    fw, fh = x_max - x_min, y_max - y_min
    if fw <= 0 or fh <= 0:
        return
    set_guide_pivot_uv(guide, (x - x_min) / fw, (y - y_min) / fh)


def guide_segment(guide, bounds):
    """(P, d, n, A, B): pivot, direction, normal and the clipped end points."""
    P = guide_pivot_px(guide, bounds)
    d = guide_dir(guide)
    n = (-d[1], d[0])
    res = clip_line_to_rect(P, d, bounds)
    if res is None:
        return None
    t0, t1 = res
    return (P, d, n,
            (P[0] + d[0] * t0, P[1] + d[1] * t0),
            (P[0] + d[0] * t1, P[1] + d[1] * t1))


def constrain_line_point(P, d, n, rect):
    """Keep a pivot so that its line still crosses the frame, with the pivot
    itself inside the frame (slid along the line, never across it)."""
    x0, y0, x1, y1 = rect
    cs = [n[0] * x + n[1] * y for x in (x0, x1) for y in (y0, y1)]
    cmin, cmax = min(cs), max(cs)
    cp = n[0] * P[0] + n[1] * P[1]
    s = (cmin - cp) if cp < cmin else ((cmax - cp) if cp > cmax else 0.0)
    P = (P[0] + n[0] * s, P[1] + n[1] * s)
    res = clip_line_to_rect(P, d, rect)
    if res is None:
        return P
    t = max(res[0], min(res[1], 0.0))
    return P[0] + d[0] * t, P[1] + d[1] * t


def guide_end_px(guide, bounds):
    """Point B of a 2D guide: `length` frame heights from the pivot (Point A)
    along the guide. Signed, so B can sit on either side of A."""
    P = guide_pivot_px(guide, bounds)
    d = guide_dir(guide)
    L = guide.length * (bounds[3] - bounds[1])
    return P[0] + d[0] * L, P[1] + d[1] * L


def clamp_to_segment(p, A, B):
    abx, aby = B[0] - A[0], B[1] - A[1]
    l2 = abx * abx + aby * aby
    if l2 < 1e-9:
        return A
    t = max(0.0, min(1.0, ((p[0] - A[0]) * abx + (p[1] - A[1]) * aby) / l2))
    return A[0] + abx * t, A[1] + aby * t


def handle_positions(guide, bounds):
    """Screen positions of the Point A (pivot) and Point B handles. A Point
    B outside the frame is shown where the guide leaves the frame, so it can
    always be grabbed."""
    P = guide_pivot_px(guide, bounds)
    B = guide_end_px(guide, bounds)
    x0, y0, x1, y1 = bounds
    if not (x0 <= B[0] <= x1 and y0 <= B[1] <= y1):
        seg = guide_segment(guide, bounds)
        if seg is not None:
            B = clamp_to_segment(B, seg[3], seg[4])
    return P, B


def set_guide_points_px(guide, bounds, a, b):
    """Make a 2D guide run through screen points a (Point A, the pivot) and
    b (Point B)."""
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6:
        return
    set_guide_from_line(guide, bounds, a, math.atan2(b[1] - a[1], b[0] - a[0]))
    P = guide_pivot_px(guide, bounds)
    d = guide_dir(guide)
    fh = bounds[3] - bounds[1]
    if fh > 0:
        L = ((b[0] - P[0]) * d[0] + (b[1] - P[1]) * d[1]) / fh
        guide.length = L if abs(L) > 1e-4 else 1e-4


def set_guide_from_line(guide, bounds, p, theta):
    """Make `guide` the line through screen point p with absolute direction
    theta, choosing H/V as the nearer base so the stored tilt stays small."""
    a = theta % math.pi
    if HALF_PI / 2 <= a < 3 * HALF_PI / 2:
        guide.axis = 'V'
        guide.angle = a - HALF_PI
    else:
        guide.axis = 'H'
        guide.angle = a if a < HALF_PI else a - math.pi
    d = (math.cos(theta), math.sin(theta))
    n = (-d[1], d[0])
    p = constrain_line_point(p, d, n, bounds)
    set_guide_pivot_px(guide, bounds, p[0], p[1])


# ------------------------------------------------------------
#  Frame grid
# ------------------------------------------------------------
#
#  One grid for everything (snapping guides, snapping objects, drawing):
#  - default: lines every grid_step % of the width and of the height, from
#    the bottom-left corner, so cells take the frame's shape;
#  - Square Cells: square cells of grid_step % of the frame's SHORTER side,
#    centred on the frame (symmetric; the outer cells may be partial).

MAX_GRID_LINES = 400        # per direction


def grid_cell(settings, w, h):
    """(cell width, cell height) for a frame of w x h (any units)."""
    s = settings.grid_step / 100.0
    if settings.grid_square:
        c = s * min(w, h)
        return c, c
    return s * w, s * h


def frame_grid(settings, bounds):
    """([x], [y]) region positions of the frame grid lines inside bounds."""
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0, y1 - y0
    cw, ch = grid_cell(settings, w, h)
    if w <= 1 or h <= 1 or cw <= 0.5 or ch <= 0.5:
        return [], []

    def axis(lo, hi, cell):
        if (hi - lo) / cell > MAX_GRID_LINES:
            return []
        if settings.grid_square:
            mid = (lo + hi) / 2.0
            n = int((hi - lo) / 2.0 / cell + 1e-9)
            return [mid + k * cell for k in range(-n, n + 1)]
        n = int((hi - lo) / cell + 1e-9)
        return [lo + k * cell for k in range(n + 1)]

    return axis(x0, x1, cw), axis(y0, y1, ch)


def nearest(values, v):
    return min(values, key=lambda t: abs(t - v)) if values else None


def copy_guide(dst, src):
    dst.name = src.name
    dst.axis = src.axis
    dst.position = src.position
    dst.offset = src.offset
    dst.angle = src.angle
    dst.length = src.length
    dst.color = src.color[:]
    dst.visible = src.visible


def unique_name(existing, prefix):
    existing = set(existing)
    i = 1
    while f"{prefix} {i}" in existing:
        i += 1
    return f"{prefix} {i}"


def make_unique_guide_name(guides, axis):
    return unique_name((g.name for g in guides), "H Guide" if axis == 'H' else "V Guide")


# ------------------------------------------------------------
#  Group library
# ------------------------------------------------------------

def find_group(settings, uid):
    if uid <= 0:
        return None
    for group in settings.groups:
        if group.uid == uid:
            return group
    return None


def group_index(settings, group):
    for i, g in enumerate(settings.groups):
        if g.uid == group.uid:
            return i
    return -1


def view_owner(settings, context):
    """(owner, property name) that holds the group uid for the current view:
    the active camera in Camera View, the scene settings otherwise. None in
    Camera View when the scene has no camera."""
    if is_camera_view(context):
        camera = context.scene.camera
        return None if camera is None else (camera, "guidebro_group")
    return settings, "viewport_group"


def get_current_group(settings, context):
    """The group shown in the current view, or None. Read-only, so it is safe
    to call from Panel.draw()."""
    owner = view_owner(settings, context)
    if owner is None:
        return None
    return find_group(settings, getattr(owner[0], owner[1]))


def get_current_guides(settings, context):
    group = get_current_group(settings, context)
    return group.guides if group is not None else None


def assign_group(settings, context, group):
    """Make the current view (camera / viewport) show `group` (None = nothing)."""
    owner = view_owner(settings, context)
    if owner is None:
        return False
    setattr(owner[0], owner[1], 0 if group is None else group.uid)
    tag_redraw_3d_views()
    return True


def new_group(settings, name):
    names = {g.name for g in settings.groups}
    base, i = name, 2
    while name in names:
        name = f"{base} {i}"
        i += 1
    group = settings.groups.add()
    group.uid = settings.next_uid
    settings.next_uid += 1
    group.name = name
    return group


def default_group_name(context):
    if is_camera_view(context) and context.scene.camera is not None:
        return f"{context.scene.camera.name} Guides"
    return "Viewport Guides"


def ensure_group(settings, context):
    """The current view's group, creating and assigning one if it has none.
    None in Camera View without a camera. Writes ID data: operators only."""
    group = get_current_group(settings, context)
    if group is not None:
        return group
    if view_owner(settings, context) is None:
        return None
    group = new_group(settings, default_group_name(context))
    assign_group(settings, context, group)
    return group


def group_users(settings, scene, group):
    """Names of the cameras (and "Viewport") that show this group."""
    users = [o.name for o in scene.objects
             if o.type == 'CAMERA' and o.guidebro_group == group.uid]
    if settings.viewport_group == group.uid:
        users.append("Viewport")
    return users


def get_default_color_attr(context):
    return "camera_default_color" if is_camera_view(context) else "viewport_default_color"


def get_default_color(settings, context):
    return getattr(settings, get_default_color_attr(context))
