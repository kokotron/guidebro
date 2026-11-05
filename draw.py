# ------------------------------------------------------------
#  Viewport drawing
# ------------------------------------------------------------

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .common import (
    SNAP_COLOR, _edit, _fx, clip_segment_to_rect, get_current_bounds,
    get_pixel_dimensions, intersect_rects, norm_to_pixel, ui_scale, visible_rect,
)
from .screen import (
    frame_grid, get_current_group, guide_angle_abs, guide_is_tilted, guide_segment,
    handle_positions,
)
from .world import guide3_handles, guide3_info, view_projection
from . import objsnap


FONT_ID = 0
LABEL_PAD = 4


def build_label(settings, guide, px_val):
    parts = []

    if settings.label_show_names and guide.name.strip():
        parts.append(guide.name.strip())

    value_parts = []
    if settings.label_show_pixels:
        value_parts.append(f"{px_val}px")
    if settings.label_show_percentage:
        value_parts.append(f"{guide.position * 100.0:.1f}%")
    if settings.label_show_angle and guide_is_tilted(guide):
        value_parts.append(f"{math.degrees(guide_angle_abs(guide)):.1f}°")

    if value_parts:
        value_label = "  ".join(value_parts)
        if parts:
            parts[-1] = f"{parts[-1]}: {value_label}"
        else:
            parts.append(value_label)

    return "  ".join(parts)


def draw_label(text, A, B, d, label_rect, color, size):
    """Draw a guide's label at the start of the part of the guide that is
    actually visible: the left end of mostly-horizontal lines, the bottom
    end otherwise. When the view is zoomed past the frame, that is the edge
    of the view, so labels never slide out of sight."""
    if label_rect is None:
        return
    seg = clip_segment_to_rect(A, B, label_rect)
    if seg is None:
        return
    A, B = seg
    anchor = (A if A[0] <= B[0] else B) if abs(d[0]) >= abs(d[1]) else (A if A[1] <= B[1] else B)

    blf.size(FONT_ID, size)
    w, h = blf.dimensions(FONT_ID, text)
    x0, y0, x1, y1 = label_rect
    x = max(x0 + 2, min(anchor[0] + LABEL_PAD, x1 - w - 2))
    y = max(y0 + 2, min(anchor[1] + LABEL_PAD, y1 - h - 2))
    blf.color(FONT_ID, color[0], color[1], color[2], color[3])
    blf.position(FONT_ID, x, y, 0)
    blf.draw(FONT_ID, text)


def _draw_prim(shader, prim, pts, color, width=1.0):
    # BLF text drawing can alter GPU blend state in Blender 4.5/5.x, so every
    # primitive resets alpha blending first.
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)
    batch = batch_for_shader(shader, prim, {"pos": pts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _circle_pts(cx, cy, r, steps=24):
    return [(cx + r * math.cos(2 * math.pi * i / steps),
             cy + r * math.sin(2 * math.pi * i / steps)) for i in range(steps + 1)]


def _thick_pts(A, B, n):
    """Three parallel 1 px lines (works even where line_width_set is ignored)."""
    out = []
    for off in (-1.0, 0.0, 1.0):
        out.extend([(A[0] + n[0] * off, A[1] + n[1] * off),
                    (B[0] + n[0] * off, B[1] + n[1] * off)])
    return out


def _dash_pts(A, B, dash=12.0, gap=7.0, max_dashes=600):
    """Line list for a dashed segment A-B."""
    dx, dy = B[0] - A[0], B[1] - A[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    ux, uy = dx / length, dy / length
    period = dash + gap
    if length / period > max_dashes:
        return [A, B]
    out, t = [], 0.0
    while t < length:
        t2 = min(t + dash, length)
        out.append((A[0] + ux * t, A[1] + uy * t))
        out.append((A[0] + ux * t2, A[1] + uy * t2))
        t += period
    return out


def draw_edit_badge(context):
    """Small reminder at the top of the viewport while Edit Guides is on."""
    region = context.region
    if region is None:
        return
    text = "GUIDE EDIT MODE"
    blf.size(FONT_ID, 12 * ui_scale())
    width, _height = blf.dimensions(FONT_ID, text)
    blf.color(FONT_ID, 1.0, 0.8, 0.2, 1.0)
    blf.position(FONT_ID, (region.width - width) / 2.0, region.height - 60, 0)
    blf.draw(FONT_ID, text)


def draw_point_handles(shader, rgb, A, B, hot_a, hot_b):
    """A guide's two points (A and B), drawn alike and tied together."""
    if A is not None and B is not None:
        _draw_prim(shader, 'LINES', [A, B], (rgb[0], rgb[1], rgb[2], 0.5), 1.0)

    def diamond(p, s):
        r = 6.0 * s
        return [(p[0] + r, p[1]), (p[0], p[1] + r), (p[0] - r, p[1]), (p[0], p[1] - r), (p[0] + r, p[1])]

    size = 11 * ui_scale()
    for p, hot, tag in ((A, hot_a, "A"), (B, hot_b, "B")):
        if p is None:
            continue
        scale = 1.35 if hot else 1.0
        _draw_prim(shader, 'LINE_STRIP', diamond(p, scale + 0.35), (0.0, 0.0, 0.0, 0.8), 3.0)
        color = (1.0, 0.9, 0.2, 1.0) if hot else (rgb[0], rgb[1], rgb[2], 1.0)
        _draw_prim(shader, 'LINE_STRIP', diamond(p, scale), color, 2.0)
        blf.size(FONT_ID, size)
        blf.color(FONT_ID, *color)
        blf.position(FONT_ID, p[0] + 9, p[1] + 7, 0)
        blf.draw(FONT_ID, tag)


def draw_grid(context, settings, bounds, shader):
    """The frame grid, inside the frame (camera frame, or the viewport)."""
    xs, ys = frame_grid(settings, bounds)
    if not xs and not ys:
        return
    x0, y0, x1, y1 = bounds
    verts = []
    for x in xs:
        verts += [(x, y0), (x, y1)]
    for y in ys:
        verts += [(x0, y), (x1, y)]
    _draw_prim(shader, 'LINES', verts, tuple(settings.grid_color), 1.0)


def draw_fx(shader):
    """Snap markers, highlighted bounds / edges and the snap label."""
    for A, B in _fx["thin"]:
        _draw_prim(shader, 'LINES', [A, B], (SNAP_COLOR[0], SNAP_COLOR[1], SNAP_COLOR[2], 0.6), 1.0)
    for A, B in _fx["thick"]:
        dx, dy = B[0] - A[0], B[1] - A[1]
        length = math.hypot(dx, dy) or 1.0
        _draw_prim(shader, 'LINES', _thick_pts(A, B, (-dy / length, dx / length)), SNAP_COLOR, 1.0)
    for x0, y0, x1, y1 in _fx["rects"]:
        _draw_prim(shader, 'LINE_STRIP',
                   [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                   (SNAP_COLOR[0], SNAP_COLOR[1], SNAP_COLOR[2], 0.7), 1.0)
    for x, y in _fx["pts"]:
        _draw_prim(shader, 'LINE_STRIP', _circle_pts(x, y, 6.0, 16), SNAP_COLOR, 2.0)
    if _fx["label"] is not None:
        x, y, text = _fx["label"]
        blf.size(FONT_ID, 11 * ui_scale())
        blf.color(FONT_ID, SNAP_COLOR[0], SNAP_COLOR[1], SNAP_COLOR[2], 1.0)
        blf.position(FONT_ID, x, y, 0)
        blf.draw(FONT_ID, text)


def _handle_kind(index, space):
    """Which handle of guide `index` is hot (dragged, else hovered)."""
    if _edit["drag"] >= 0:
        return _edit["drag_kind"] if (_edit["drag_space"] == space and _edit["drag"] == index) else None
    return _edit["hover_kind"] if (_edit["hover_space"] == space and _edit["hover"] == index) else None


def draw_guides2d(context, settings, bounds, label_rect, shader, edit_here, fx_here):
    group = get_current_group(settings, context)
    if group is None or not group.guides:
        return
    guides = group.guides

    x_min, y_min, x_max, y_max = bounds
    if x_max - x_min <= 0 or y_max - y_min <= 0:
        return

    dim_x, dim_y = get_pixel_dimensions(context)
    active_index = max(0, min(group.active_index, len(guides) - 1))
    hover_index = _edit["hover"] if edit_here and _edit["hover_space"] == '2D' else -1
    drag_index = _edit["drag"] if edit_here and _edit["drag_space"] == '2D' else -1
    snapped = _fx["guides"] if fx_here else ()
    size = settings.label_size * ui_scale()

    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(1.0)

    for i, guide in enumerate(guides):
        if not guide.visible:
            continue
        seg = guide_segment(guide, bounds)
        if seg is None:
            continue
        _P, d, n, A, B = seg

        # The guide being hovered / dragged / snapped to is drawn at full
        # opacity and 3 px thick.
        emphasised = (i == drag_index
                      or (i == hover_index and _edit["hover_kind"] == 'LINE')
                      or i in snapped)

        rgb = guide.color[:]
        color = (rgb[0], rgb[1], rgb[2], 1.0 if emphasised else settings.guide_opacity)
        _draw_prim(shader, 'LINES', _thick_pts(A, B, n) if emphasised else [A, B], color, 1.0)

        px_val = norm_to_pixel(guide.position, dim_y if guide.axis == 'H' else dim_x)
        label = build_label(settings, guide, px_val)
        if label:
            draw_label(label, A, B, d, label_rect, color, size)

    if edit_here:
        g = guides[active_index]
        if g.visible:
            kind = _handle_kind(active_index, '2D')
            A, B = handle_positions(g, bounds)
            draw_point_handles(shader, g.color[:], A, B, kind == 'PIVOT', kind == 'END')


def draw_guides3d(context, settings, bounds, label_rect, shader, edit_here, fx_here):
    """3D (world-space) guides, projected into this view. Drawn dashed."""
    guides = settings.guides_3d
    if len(guides) == 0:
        return
    proj = view_projection(context)
    if proj is None:
        return

    active = max(0, min(settings.guides_3d_active_index, len(guides) - 1))
    hover = _edit["hover"] if edit_here and _edit["hover_space"] == '3D' else -1
    drag = _edit["drag"] if edit_here and _edit["drag_space"] == '3D' else -1
    snapped = _fx["guides3"] if fx_here else ()
    size = settings.label_size * ui_scale()

    gpu.state.blend_set('ALPHA')
    infos = {}
    for i, g in enumerate(guides):
        if not g.visible:
            continue
        info = guide3_info(proj, g, bounds)
        if info is None:
            continue
        infos[i] = info
        seg = info["seg"]
        if seg is None:
            continue
        A, B = seg
        emphasised = (i == drag or (i == hover and _edit["hover_kind"] == 'LINE') or i in snapped)

        rgb = g.color[:]
        color = (rgb[0], rgb[1], rgb[2], 1.0 if emphasised else settings.guide_opacity)
        if emphasised:
            n = info["n"]
            verts = []
            for off in (-1.0, 0.0, 1.0):
                verts.extend(_dash_pts((A[0] + n[0] * off, A[1] + n[1] * off),
                                       (B[0] + n[0] * off, B[1] + n[1] * off)))
        else:
            verts = _dash_pts(A, B)
        if verts:
            _draw_prim(shader, 'LINES', verts, color, 1.0)

        if settings.label_show_names:
            text = f"{g.name.strip()}  (3D)" if g.name.strip() else "3D"
            draw_label(text, A, B, info["d"], label_rect, color, size)

    if edit_here and active in infos and guides[active].visible:
        A, B = guide3_handles(proj, guides[active], bounds)
        kind = _handle_kind(active, '3D')
        draw_point_handles(shader, guides[active].color[:], A, B, kind == 'PIVOT', kind == 'END')


def draw_guides():
    context = bpy.context
    if not hasattr(context, 'scene') or context.scene is None:
        return

    settings = context.scene.guidebro

    area = getattr(context, "area", None)
    area_ptr = area.as_pointer() if area is not None else None
    objsnap.clear_feedback_if_idle()
    edit_here = _edit["active"] and area_ptr is not None and area_ptr == _edit["area"]
    fx_here = area_ptr is not None and area_ptr == _fx["area"]
    if edit_here:
        draw_edit_badge(context)
        gpu.state.blend_set('ALPHA')

    bounds = get_current_bounds(context)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    # Universal hide toggle hides the guides (feedback overlays still show).
    if not settings.guides_hidden and bounds is not None:
        view = visible_rect(context)
        label_rect = intersect_rects(bounds, view) if view is not None else bounds
        if settings.grid_show:
            draw_grid(context, settings, bounds, shader)
        draw_guides2d(context, settings, bounds, label_rect, shader, edit_here, fx_here)
        draw_guides3d(context, settings, bounds, label_rect, shader, edit_here, fx_here)

    if fx_here and bounds is not None:
        draw_fx(shader)

    gpu.state.blend_set('NONE')
    gpu.state.line_width_set(1.0)


_draw_handle = None


def register():
    global _draw_handle
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_guides, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
