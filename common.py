# ------------------------------------------------------------
#  Shared helpers: view / region lookup, runtime state, 2D maths
# ------------------------------------------------------------

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector


HALF_PI = math.pi / 2.0
GRAB_PX = 8         # how close (screen px) the mouse must be to grab a line
GRAB_MARGIN = 12    # how far past the frame ends a line can still be grabbed
HANDLE_PX = 10      # grab radius of the pivot / rotate handles
HANDLE_DIST = 64    # distance of the rotate handle from the pivot (px)
TILT_EPS = 1e-3     # radians

SNAP_COLOR = (1.0, 0.85, 0.1, 1.0)
MODIFIER_KEYS = {
    'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_ALT', 'RIGHT_ALT',
    'LEFT_SHIFT', 'RIGHT_SHIFT', 'OSKEY',
}


def get_settings(context):
    return context.scene.guidebro


# ------------------------------------------------------------
#  View / region lookup
# ------------------------------------------------------------

def get_view_region(context):
    """The WINDOW region of the current 3D View.

    The context's own region wins when it is a WINDOW (draw callbacks, and
    each pane of a quad view); from the sidebar it is the area's first one.
    """
    region = getattr(context, "region", None)
    if region is not None and region.type == 'WINDOW':
        return region

    area = getattr(context, "area", None)
    if area and area.type == 'VIEW_3D':
        for region in area.regions:
            if region.type == 'WINDOW':
                return region
    return None


def get_region_3d(context):
    """Return RegionView3D from either draw or sidebar context."""
    rv3d = getattr(context, "region_data", None)
    if rv3d is not None:
        return rv3d

    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == 'VIEW_3D':
        return getattr(space, "region_3d", None)

    return None


def is_camera_view(context):
    rv3d = get_region_3d(context)
    return rv3d is not None and rv3d.view_perspective == 'CAMERA'


def get_camera_frame_bounds(context):
    """(x_min, y_min, x_max, y_max) of the camera frame in region pixels,
    or None if not in camera view."""
    region = get_view_region(context)
    rv3d = get_region_3d(context)

    if region is None or rv3d is None or rv3d.view_perspective != 'CAMERA':
        return None

    scene = context.scene
    camera = scene.camera
    if camera is None:
        return None

    # view_frame() is camera-local; location_3d_to_region_2d() wants world space.
    pts_2d = [
        view3d_utils.location_3d_to_region_2d(region, rv3d, camera.matrix_world @ corner)
        for corner in camera.data.view_frame(scene=scene)
    ]
    if any(p is None for p in pts_2d):
        return None

    xs = [p.x for p in pts_2d]
    ys = [p.y for p in pts_2d]
    return min(xs), min(ys), max(xs), max(ys)


def get_current_bounds(context):
    """The frame guides live in: the camera frame in Camera View, the
    whole viewport otherwise."""
    region = get_view_region(context)
    if region is None:
        return None
    if is_camera_view(context):
        return get_camera_frame_bounds(context)
    return 0, 0, region.width, region.height


def get_pixel_dimensions(context):
    """(width, height) used for guide pixel values: the render resolution in
    Camera View, the viewport's own pixels otherwise."""
    if is_camera_view(context):
        render = context.scene.render
        return int(render.resolution_x), int(render.resolution_y)

    region = get_view_region(context)
    if region is None:
        return 0, 0
    return int(region.width), int(region.height)


def visible_rect(context):
    """The part of the view's WINDOW region that is really visible: not
    covered by a toolbar, sidebar or header drawn over it (region overlap)."""
    region = get_view_region(context)
    if region is None:
        return None
    x0, y0, x1, y1 = 0.0, 0.0, float(region.width), float(region.height)
    area = getattr(context, "area", None)
    if area is None:
        return x0, y0, x1, y1

    for other in area.regions:
        if other.width <= 1 or other.height <= 1:
            continue
        if other.type not in {'TOOLS', 'UI', 'HEADER', 'TOOL_HEADER'}:
            continue
        ox0, oy0 = other.x - region.x, other.y - region.y
        ox1, oy1 = ox0 + other.width, oy0 + other.height
        if ox1 <= x0 or ox0 >= x1 or oy1 <= y0 or oy0 >= y1:
            continue                                  # beside the view, not over it
        if other.type in {'TOOLS', 'UI'}:
            if (ox0 + ox1) / 2.0 >= (x0 + x1) / 2.0:
                x1 = min(x1, ox0)
            else:
                x0 = max(x0, ox1)
        else:
            if (oy0 + oy1) / 2.0 >= (y0 + y1) / 2.0:
                y1 = min(y1, oy0)
            else:
                y0 = max(y0, oy1)
    return x0, y0, x1, y1


def intersect_rects(a, b):
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if r[2] <= r[0] or r[3] <= r[1]:
        return None
    return r


def norm_to_pixel(norm, dim):
    """0..1 -> pixel value, from left/bottom."""
    return round(norm * dim)


def pixel_to_norm(px, dim):
    """Pixel value -> 0..1."""
    if dim <= 0:
        return 0.0
    return max(0.0, min(1.0, px / dim))


def tag_redraw_3d_views():
    """Force all open 3D Viewports to redraw."""
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def guide_update_callback(self, context):
    tag_redraw_3d_views()


def ui_scale():
    try:
        return bpy.context.preferences.system.ui_scale
    except AttributeError:
        return 1.0


# ------------------------------------------------------------
#  2D maths
# ------------------------------------------------------------

def wrap_pi(a):
    """Wrap an angle to (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


def norm_rel(a):
    """Wrap a relative guide angle to [-pi/2, pi/2) (lines repeat every pi)."""
    return ((a + HALF_PI) % math.pi) - HALF_PI


def clip_line_to_rect(P, d, rect):
    """Clip the infinite line P + t*d to rect. Returns (t0, t1) or None."""
    x0, y0, x1, y1 = rect
    t0, t1 = -1e9, 1e9
    for p, dd, lo, hi in ((P[0], d[0], x0, x1), (P[1], d[1], y0, y1)):
        if abs(dd) < 1e-9:
            if p < lo - 1e-6 or p > hi + 1e-6:
                return None
        else:
            ta, tb = (lo - p) / dd, (hi - p) / dd
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
            if t0 > t1:
                return None
    return t0, t1


def clip_segment_to_rect(A, B, rect):
    """Part of the segment A-B inside rect, or None."""
    dx, dy = B[0] - A[0], B[1] - A[1]
    res = clip_line_to_rect(A, (dx, dy), rect)
    if res is None:
        return None
    t0, t1 = max(res[0], 0.0), min(res[1], 1.0)
    if t0 >= t1:
        return None
    return (A[0] + dx * t0, A[1] + dy * t0), (A[0] + dx * t1, A[1] + dy * t1)


def dist_point_segment(m, A, B):
    abx, aby = B[0] - A[0], B[1] - A[1]
    l2 = abx * abx + aby * aby
    if l2 < 1e-9:
        return math.hypot(m[0] - A[0], m[1] - A[1])
    t = max(0.0, min(1.0, ((m[0] - A[0]) * abx + (m[1] - A[1]) * aby) / l2))
    return math.hypot(m[0] - (A[0] + t * abx), m[1] - (A[1] + t * aby))


# ------------------------------------------------------------
#  Edit-mode state + transient viewport feedback (runtime only)
# ------------------------------------------------------------

_edit = {
    "active": False,
    "token": 0,         # bumped on every start so a stale modal can retire itself
    "area": None,       # as_pointer() of the 3D area being edited
    "hover": -1,        # guide index under the mouse
    "hover_kind": None, # LINE / PIVOT (point A) / END (point B)
    "hover_space": '2D',# '2D' = screen guide, '3D' = world-space guide
    "drag": -1,         # guide index being dragged
    "drag_kind": None,
    "drag_space": '2D',
}

# Overlay primitives drawn by the draw callback for whichever tool is running.
# "owner" says who filled it ('OBJSNAP' = object snapping during a move).
_fx = {"area": None, "owner": None, "guides": set(), "guides3": set(), "pts": [],
       "rects": [], "thick": [], "thin": [], "label": None}


def fx_reset(area=None, owner=None):
    _fx.update(area=area, owner=owner, guides=set(), guides3=set(), pts=[], rects=[],
               thick=[], thin=[], label=None)


def fx_from_snap(area_ptr, snap):
    """Show the marker / bounds rectangle / label of a snap result."""
    fx_reset(area_ptr)
    if not snap:
        return
    if snap.get("pt") is not None:
        _fx["pts"].append(tuple(snap["pt"]))
    if snap.get("rect") is not None:
        _fx["rects"].append(tuple(snap["rect"]))
    if snap.get("label") and snap.get("pt") is not None:
        pt = snap["pt"]
        _fx["label"] = (pt[0] + 10, pt[1] + 10, snap["label"])


def edit_state_reset():
    _edit.update(active=False, area=None, hover=-1, hover_kind=None, hover_space='2D',
                 drag=-1, drag_kind=None, drag_space='2D')
    fx_reset()
    _edit["token"] += 1


def snap_kind_from_event(event):
    """Ctrl = grid, Ctrl+Shift = origin, Ctrl+Alt = bounds, Ctrl+Alt+Shift = vertex."""
    if not event.ctrl:
        return None
    if event.alt and event.shift:
        return 'VERTEX'
    if event.alt:
        return 'BOUNDS'
    if event.shift:
        return 'ORIGIN'
    return 'GRID'


# ------------------------------------------------------------
#  Mouse / view helpers
# ------------------------------------------------------------

def region_mouse_coords(context, event):
    """Mouse position relative to the 3D view's WINDOW region.

    Uses window coordinates, so it is correct even when a modal operator was
    started from the sidebar (where event.mouse_region_x is sidebar-relative).
    """
    region = get_view_region(context)
    if region is None:
        return None
    return event.mouse_x - region.x, event.mouse_y - region.y


def mouse_over_viewport(context, event):
    """True if the mouse is over the 3D view proper (not the sidebar/toolbar/header)."""
    region = get_view_region(context)
    if region is None or context.area is None:
        return False
    x, y = event.mouse_x, event.mouse_y
    if not (region.x <= x < region.x + region.width and
            region.y <= y < region.y + region.height):
        return False
    for other in context.area.regions:
        if other.type == 'WINDOW' or other.width <= 1 or other.height <= 1:
            continue
        if other.x <= x < other.x + other.width and other.y <= y < other.y + other.height:
            return False
    return True


def view_forward(rv3d):
    """Direction the view looks along, in world space.

    Taken from the view matrix, which is always correct. rv3d.view_rotation is
    NOT kept in sync while looking through a camera.
    """
    f = rv3d.view_matrix.inverted_safe().to_3x3() @ Vector((0.0, 0.0, -1.0))
    return f.normalized()


def screen_to_plane(region, rv3d, xy, plane_point):
    """World point under screen position xy, on the plane through plane_point
    that faces the viewer (so moving objects keeps their depth)."""
    ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
    rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy)
    forward = view_forward(rv3d)
    denom = rd.dot(forward)
    if abs(denom) < 1e-9:
        return None
    return ro + rd * ((plane_point - ro).dot(forward) / denom)
