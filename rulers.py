# ------------------------------------------------------------
#  Rulers (camera view)
# ------------------------------------------------------------
#
#  Photoshop-style rulers along the top and left edges of the visible view,
#  in render pixels of the camera frame (left ruler counts up from the
#  frame's bottom, like the guide labels). Drag out of the top ruler for a
#  horizontal guide, out of the left ruler for a vertical one; release over
#  a ruler to discard it.
#
#  Clicks on the rulers are caught by the window-level "view tracker" modal
#  (objsnap.py), which sees events before the 3D view's own keymaps.

import math

import blf
import bpy

from .common import (
    fx_from_snap, fx_reset, get_camera_frame_bounds, get_pixel_dimensions, get_settings,
    is_camera_view, tag_redraw_3d_views, ui_scale, visible_rect,
)
from .screen import (
    ensure_group, find_group, frame_grid, get_default_color, guide_is_tilted, guide_pivot_uv,
    guide_tag, make_unique_guide_name, nearest,
)
from .snapping import POINT_KINDS, GeoCache, active_snap_kinds, best_line_snap


RULER_PX = 20

BG = (0.11, 0.11, 0.11, 0.92)
FRAME_BG = (0.19, 0.19, 0.19, 0.96)
TICK = (0.62, 0.62, 0.62, 1.0)
TEXT = (0.78, 0.78, 0.78, 1.0)
EDGE = (0.0, 0.0, 0.0, 0.6)


def ruler_layout(context):
    """Where the rulers are in this view, or None when they don't apply:
    {"top", "left", "corner": rects, "frame": camera frame, "res": (rx, ry), "T"}."""
    settings = get_settings(context)
    if not settings.show_rulers or not is_camera_view(context) or context.scene.camera is None:
        return None
    frame = get_camera_frame_bounds(context)
    view = visible_rect(context)
    if frame is None or view is None:
        return None
    T = round(RULER_PX * ui_scale())
    x0, y0, x1, y1 = view
    if x1 - x0 < 4 * T or y1 - y0 < 4 * T:
        return None
    rx, ry = get_pixel_dimensions(context)
    if rx <= 0 or ry <= 0 or frame[2] - frame[0] <= 1 or frame[3] - frame[1] <= 1:
        return None
    return {"top": (x0 + T, y1 - T, x1, y1), "left": (x0, y0, x0 + T, y1 - T),
            "corner": (x0, y1 - T, x0 + T, y1), "frame": frame, "res": (rx, ry), "T": T}


def ruler_hit(context, x, y):
    """'TOP' / 'LEFT' / 'CORNER' when region point (x, y) is on a ruler, else None."""
    lay = ruler_layout(context)
    if lay is None:
        return None
    for key in ("corner", "top", "left"):
        r = lay[key]
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return key.upper()
    return None


def view_rect_without_rulers(context, view):
    """The visible view minus the ruler strips (so labels don't hide under them)."""
    lay = ruler_layout(context)
    if lay is None or view is None:
        return view
    return (view[0] + lay["T"], view[1], view[2], view[3] - lay["T"])


# ------------------------------------------------------------
#  Drawing
# ------------------------------------------------------------

def _rect_tris(r):
    x0, y0, x1, y1 = r
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def _nice_step(min_units):
    """(major step, minor step) in render px: 1/2/5 x 10^n, at least min_units."""
    min_units = max(min_units, 1e-9)
    k = 10 ** math.floor(math.log10(min_units))
    for m in (1, 2, 5, 10):
        if m * k >= min_units:
            step = m * k
            minor = step / (2 if m == 2 else 5)
            return step, minor
    return 10 * k, 2 * k


def _ticks(lo_px, hi_px, f0, scale, step):
    """Values (render px, multiples of step) whose screen position lies in lo..hi."""
    v0 = math.ceil(((lo_px - f0) / scale) / step)
    v1 = math.floor(((hi_px - f0) / scale) / step)
    if v1 - v0 > 2000:
        return []
    return [k * step for k in range(int(v0), int(v1) + 1)]


def _fmt(v):
    return f"{v:g}" if abs(v - round(v)) > 1e-6 else str(int(round(v)))


def draw_rulers(context, settings, shader, draw_prim, guides):
    lay = ruler_layout(context)
    if lay is None:
        return
    top, left, corner, T = lay["top"], lay["left"], lay["corner"], lay["T"]
    fx0, fy0, fx1, fy1 = lay["frame"]
    rx, ry = lay["res"]
    sx, sy = (fx1 - fx0) / rx, (fy1 - fy0) / ry          # screen px per render px

    # Backgrounds; the frame's own extent is a touch lighter.
    draw_prim(shader, 'TRIS', _rect_tris(top) + _rect_tris(left) + _rect_tris(corner), BG)
    span = []
    a, b = max(top[0], fx0), min(top[2], fx1)
    if b > a:
        span += _rect_tris((a, top[1], b, top[3]))
    a, b = max(left[1], fy0), min(left[3], fy1)
    if b > a:
        span += _rect_tris((left[0], a, left[2], b))
    if span:
        draw_prim(shader, 'TRIS', span, FRAME_BG)

    # Ticks: a major tick (with a label) every ~70 px on screen.
    font = 0
    blf.size(font, 9 * ui_scale())
    lines = []
    step, minor = _nice_step(70.0 / sx)
    for v in _ticks(top[0], top[2], fx0, sx, minor):
        x = fx0 + v * sx
        major = abs(v / step - round(v / step)) < 1e-6
        h = T * (0.55 if major else 0.25)
        lines += [(x, top[1]), (x, top[1] + h)]
        if major:
            blf.color(font, *TEXT)
            blf.position(font, x + 3, top[1] + T * 0.5, 0)
            blf.draw(font, _fmt(v))
    step, minor = _nice_step(70.0 / sy)
    blf.enable(font, blf.ROTATION)
    blf.rotation(font, math.pi / 2)
    for v in _ticks(left[1], left[3], fy0, sy, minor):
        y = fy0 + v * sy
        major = abs(v / step - round(v / step)) < 1e-6
        h = T * (0.55 if major else 0.25)
        lines += [(left[2], y), (left[2] - h, y)]
        if major:
            blf.color(font, *TEXT)
            blf.position(font, left[0] + T * 0.5, y + 3, 0)
            blf.draw(font, _fmt(v))
    blf.disable(font, blf.ROTATION)
    if lines:
        draw_prim(shader, 'LINES', lines, TICK)

    # Inner edges.
    draw_prim(shader, 'LINES', [(left[2], left[1]), (left[2], corner[3]),
                                (corner[0], top[1]), (top[2], top[1])], EDGE)

    # Where the 2D guides are: a small mark on each ruler.
    marks = []
    for g in guides or ():
        if not g.visible or guide_is_tilted(g):
            continue
        rgb = g.color[:]
        u, v = guide_pivot_uv(g)
        if guide_tag(g) == 'V':                      # vertical line: mark on the top ruler
            x = fx0 + u * (fx1 - fx0)
            if top[0] <= x <= top[2]:
                marks.append(([(x - 4, top[1]), (x + 4, top[1]), (x, top[1] + 6)], rgb))
        else:                                        # horizontal line: on the left ruler
            y = fy0 + v * (fy1 - fy0)
            if left[1] <= y <= left[3]:
                marks.append(([(left[2], y - 4), (left[2], y + 4), (left[2] - 6, y)], rgb))
    for tri, rgb in marks:
        draw_prim(shader, 'TRIS', tri, (rgb[0], rgb[1], rgb[2], 1.0))


# ------------------------------------------------------------
#  Dragging a guide out of a ruler
# ------------------------------------------------------------

class RulerDrag:
    """A guide being dragged out of a ruler. Lives in the view tracker modal."""

    HINT = ("Drag the guide into place   |   Ctrl: snap (or the Snapping toggles)   |   "
            "release over a ruler to discard   |   Esc / RMB: cancel")

    def __init__(self, context, side):
        settings = get_settings(context)
        self.area_ptr = context.area.as_pointer()
        self.region_ptr = context.region.as_pointer()
        self.axis = 'H' if side == 'TOP' else 'V'
        group = ensure_group(settings, context)
        self.group_uid = group.uid
        g = group.guides.add()
        g.name = make_unique_guide_name(group.guides, self.axis)
        g.axis = self.axis
        g.offset = 0.5
        g.angle = 0.0
        g.color = get_default_color(settings, context)
        self.index = len(group.guides) - 1
        group.active_index = self.index
        self.cache = None
        context.workspace.status_text_set(self.HINT)

    def guide(self, context):
        group = find_group(get_settings(context), self.group_uid)
        if group is None or not (0 <= self.index < len(group.guides)):
            return None, None
        return group, group.guides[self.index]

    def update(self, context, event, mx, my):
        settings = get_settings(context)
        group, g = self.guide(context)
        frame = get_camera_frame_bounds(context)
        if g is None or frame is None:
            return
        fx0, fy0, fx1, fy1 = frame
        n = (0.0, 1.0) if self.axis == 'H' else (1.0, 0.0)
        c = my if self.axis == 'H' else mx
        kinds = active_snap_kinds(settings, event)
        snap = None
        hit = None
        if kinds & set(POINT_KINDS):
            if self.cache is None:
                self.cache = GeoCache(context)
            hit = best_line_snap(self.cache, context, kinds, (mx, my), n, mx, my,
                                 settings.snap_radius)
        if hit is not None:
            c += hit["dist"]
            snap = {"label": hit["label"], "pt": hit["pt"], "rect": hit["rect"]}
        elif 'GRID' in kinds:
            xs, ys = frame_grid(settings, frame)
            t = nearest(ys if self.axis == 'H' else xs, c)
            if t is not None:
                c = t
                snap = {"label": f"Grid {settings.grid_step:g}%"}
        lo, hi = (fy0, fy1) if self.axis == 'H' else (fx0, fx1)
        pos = max(0.0, min(1.0, (c - lo) / (hi - lo)))
        if snap is None:                             # whole render pixels
            dim = get_pixel_dimensions(context)[1 if self.axis == 'H' else 0]
            if dim > 0:
                pos = round(pos * dim) / dim
        g.position = pos
        fx_from_snap(self.area_ptr, snap)
        context.area.tag_redraw()

    def finish(self, context, mx, my):
        """Keep the guide, or drop it when released over a ruler."""
        self.cache = None
        context.workspace.status_text_set(None)
        fx_reset()
        if ruler_hit(context, mx, my) is not None:
            return self.cancel(context)
        try:
            bpy.ops.ed.undo_push(message="Add Guide from Ruler")
        except Exception:
            pass
        tag_redraw_3d_views()

    def cancel(self, context):
        self.cache = None
        context.workspace.status_text_set(None)
        fx_reset()
        group, g = self.guide(context)
        if g is not None:
            group.guides.remove(self.index)
            group.active_index = max(0, min(group.active_index, len(group.guides) - 1))
        tag_redraw_3d_views()
