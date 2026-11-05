# ------------------------------------------------------------
#  Edit Guides mode (drag / pivot / rotate) and the edge picker
# ------------------------------------------------------------

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from .common import (
    GRAB_MARGIN, GRAB_PX, HANDLE_PX, MODIFIER_KEYS,
    _edit, _fx, clip_line_to_rect, dist_point_segment, fx_from_snap, fx_reset,
    get_current_bounds, get_pixel_dimensions, get_region_3d, get_settings,
    get_view_region, is_camera_view, mouse_over_viewport, norm_rel, norm_to_pixel,
    region_mouse_coords, screen_to_plane, tag_redraw_3d_views, wrap_pi,
)
from .ops import add_3d_guide
from .screen import (
    constrain_line_point, ensure_group, frame_grid, get_current_group, get_current_guides,
    get_default_color, guide_angle_abs, guide_base_angle, guide_dir, guide_is_tilted,
    guide_normal, guide_pivot_px, guide_segment, handle_positions, nearest,
    set_guide_points_px, set_guide_pivot_px, unique_name,
)
from .snapping import (
    POINT_KINDS, GeoCache, active_snap_kinds, best_line_snap, best_point_snap,
    snap_world_point,
)
from .world import (
    guide3_handles, guide3_info, line3_screen, line_through_target,
    point_on_line_under_ray, project_point, set_guide_points, snap_to_world_grid,
    view_projection,
)


SNAP_HINT = "Ctrl: grid  |  +Shift: origin  |  +Alt: bounds  |  +Alt+Shift: vertex"


def _grid_label(settings):
    return f"Grid {settings.grid_step:g}%" + (" square" if settings.grid_square else "")


# ------------------------------------------------------------
#  Picking
# ------------------------------------------------------------

def line_hit_dist(guide, bounds, mx, my):
    """Distance from (mx, my) to a 2D guide's line (a little beyond its
    ends included), or None when the guide misses the frame."""
    seg = guide_segment(guide, bounds)
    if seg is None:
        return None
    _P, d, _n, A, B = seg
    A2 = (A[0] - d[0] * GRAB_MARGIN, A[1] - d[1] * GRAB_MARGIN)
    B2 = (B[0] + d[0] * GRAB_MARGIN, B[1] + d[1] * GRAB_MARGIN)
    return dist_point_segment((mx, my), A2, B2)


def pick_at(context, mx, my):
    """What 2D-guide thing is under (mx, my)? -> (kind, index), kind = LINE /
    PIVOT (Point A) / END (Point B), or None. The active guide's handles win over lines."""
    settings = get_settings(context)
    if settings.guides_hidden:
        return None
    group = get_current_group(settings, context)
    if group is None or not group.guides:
        return None
    bounds = get_current_bounds(context)
    if bounds is None:
        return None
    if bounds[2] - bounds[0] <= 0 or bounds[3] - bounds[1] <= 0:
        return None

    guides = group.guides
    active = max(0, min(group.active_index, len(guides) - 1))
    g = guides[active]
    if g.visible:
        pa, pb = handle_positions(g, bounds)
        if math.hypot(mx - pb[0], my - pb[1]) <= HANDLE_PX:
            return 'END', active
        if math.hypot(mx - pa[0], my - pa[1]) <= HANDLE_PX:
            return 'PIVOT', active

    best, best_d = None, GRAB_PX + 0.001
    for i, g in enumerate(guides):
        if not g.visible:
            continue
        dist = line_hit_dist(g, bounds, mx, my)
        if dist is not None and dist < best_d:
            best, best_d = i, dist
    return ('LINE', best) if best is not None else None


def pick3_at(context, mx, my):
    """What 3D-guide thing is under (mx, my)? -> (kind, index, dist) or None.
    Same rules as pick_at(): the active guide's handles win over lines."""
    settings = get_settings(context)
    guides = settings.guides_3d
    if settings.guides_hidden or len(guides) == 0:
        return None
    bounds = get_current_bounds(context)
    proj = view_projection(context)
    if bounds is None or proj is None:
        return None
    if bounds[2] - bounds[0] <= 0 or bounds[3] - bounds[1] <= 0:
        return None

    active = max(0, min(settings.guides_3d_active_index, len(guides) - 1))
    best, best_d = None, GRAB_PX + 0.001
    for i, g in enumerate(guides):
        if not g.visible:
            continue
        if i == active:
            # Point B first: it is the one people grab to turn the guide.
            pa, pb = guide3_handles(proj, g, bounds)
            if pb is not None and math.hypot(mx - pb[0], my - pb[1]) <= HANDLE_PX:
                return 'END', i, 0.0
            if pa is not None and math.hypot(mx - pa[0], my - pa[1]) <= HANDLE_PX:
                return 'PIVOT', i, 0.0
        info = guide3_info(proj, g, bounds)
        if info is None:
            continue
        seg = info["seg"]
        if seg is None:
            continue
        A, B = seg
        d = info["d"]
        A2 = (A[0] - d[0] * GRAB_MARGIN, A[1] - d[1] * GRAB_MARGIN)
        B2 = (B[0] + d[0] * GRAB_MARGIN, B[1] + d[1] * GRAB_MARGIN)
        dist = dist_point_segment((mx, my), A2, B2)
        if dist < best_d:
            best, best_d = i, dist
    return ('LINE', best, best_d) if best is not None else None


def pick_any(context, mx, my):
    """2D and 3D guides together: -> (space, kind, index) or None.
    Handles beat lines; among lines the nearest wins."""
    cands = []
    hit = pick_at(context, mx, my)
    if hit is not None:
        kind, i = hit
        if kind == 'LINE':
            group = get_current_group(get_settings(context), context)
            bounds = get_current_bounds(context)
            dist = line_hit_dist(group.guides[i], bounds, mx, my)
            cands.append((1, 0.0 if dist is None else dist, '2D', kind, i))
        else:
            cands.append((0, 0.0, '2D', kind, i))
    hit3 = pick3_at(context, mx, my)
    if hit3 is not None:
        kind, i, dist = hit3
        cands.append((0 if kind != 'LINE' else 1, dist, '3D', kind, i))
    if not cands:
        return None
    best = min(cands, key=lambda c: (c[0], c[1]))
    return best[2], best[3], best[4]


# ------------------------------------------------------------
#  Edit Guides
# ------------------------------------------------------------

class GUIDEBRO_OT_edit_mode(bpy.types.Operator):
    """Toggle Guide Edit mode: drag guide lines, pivots and rotate handles with the mouse.

    While active, pressing on a guide (or its handles) is captured by this
    operator, so the scene objects behind it are never selected. Pressing
    anywhere else passes straight through to the normal tools"""
    bl_idname = "guidebro.edit_mode"
    bl_label = "Edit Guides"

    IDLE_HINT = ("Guide Edit:  drag a line to move it  |  diamond: move pivot  |  "
                 "circle: rotate  |  hold Ctrl to snap  |  Esc: exit")

    _timer = None
    _token = 0
    _drag = -1
    _mode = None
    _orig = None
    _anchor_m = (0.0, 0.0)
    _anchor_p = (0.0, 0.0)
    _fixed = None           # the point that stays put while the other is dragged (2D)
    _last_fine = False
    _cursor_hover = False
    _cache = None
    _space = '2D'           # which kind of guide is being dragged: '2D' / '3D'
    # 3D drag state
    _base = None            # (point, direction) when the drag was (re-)anchored
    _grab = None            # world point being held (LINE) / the point being moved (PIVOT, END)

    @classmethod
    def poll(cls, context):
        return (context.area is not None
                and context.area.type == 'VIEW_3D'
                and context.scene is not None)

    # -- lifecycle ---------------------------------------------

    def invoke(self, context, event):
        if _edit["active"]:
            # Toggle off. The running modal sees this on its next event
            # (its 0.1 s timer guarantees that is almost immediate).
            _edit["active"] = False
            tag_redraw_3d_views()
            return {'FINISHED'}

        _edit["token"] += 1
        _edit.update(active=True, area=context.area.as_pointer(), hover=-1,
                     hover_kind=None, hover_space='2D', drag=-1, drag_kind=None, drag_space='2D')
        self._space = '2D'
        self._token = _edit["token"]
        self._drag = -1
        self._mode = None
        self._cursor_hover = False
        self._cache = None

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(self.IDLE_HINT)
        tag_redraw_3d_views()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        # Called by Blender if the modal is killed (e.g. a file is loaded).
        try:
            self._end(context)
        except Exception:
            pass

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None

        window = context.window
        if window is not None:
            if self._drag >= 0:
                window.cursor_modal_restore()
            if self._cursor_hover:
                window.cursor_set('DEFAULT')
        self._drag = -1
        self._cursor_hover = False
        self._cache = None

        # Only the newest session owns the shared state / status bar.
        if _edit["token"] == self._token:
            _edit.update(active=False, area=None, hover=-1, hover_kind=None,
                         hover_space='2D', drag=-1, drag_kind=None, drag_space='2D')
            fx_reset()
            context.workspace.status_text_set(None)
        tag_redraw_3d_views()
        return {'FINISHED'}

    # -- event loop --------------------------------------------

    def modal(self, context, event):
        if (not _edit["active"] or _edit["token"] != self._token
                or context.area is None or context.area.type != 'VIEW_3D'):
            return self._end(context)

        if event.type == 'TIMER':
            return {'PASS_THROUGH'}

        move = event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}

        # While dragging we own the mouse completely.
        if self._drag >= 0:
            if move:
                self._drag_to(context, event)
                return {'RUNNING_MODAL'}
            if event.type in MODIFIER_KEYS:
                self._drag_to(context, event)     # snapping toggles instantly
                return {'PASS_THROUGH'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._finish_drag(context)
                return {'RUNNING_MODAL'}
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self._cancel_drag(context)
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}   # let orbit/pan/zoom keep working

        in_view = mouse_over_viewport(context, event)

        if move:
            hit = None
            if in_view:
                mx, my = region_mouse_coords(context, event)
                hit = pick_any(context, mx, my)
            self._set_hover(context, hit)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and in_view:
            mx, my = region_mouse_coords(context, event)
            hit = pick_any(context, mx, my)
            if hit is not None:
                space, kind, index = hit
                self._begin_drag(context, event, kind, index, space)
                return {'RUNNING_MODAL'}   # captured: nothing gets selected
            return {'PASS_THROUGH'}

        if event.type == 'ESC' and event.value == 'PRESS' and in_view:
            _edit["active"] = False
            return self._end(context)

        return {'PASS_THROUGH'}

    # -- hover -------------------------------------------------

    def _cursor_for(self, kind, normal):
        if kind in {'PIVOT', 'END'}:
            return 'SCROLL_XY'
        return 'MOVE_Y' if abs(normal[1]) >= abs(normal[0]) else 'MOVE_X'

    def _hover_normal(self, context, space, index):
        """Screen normal of the guide under the pointer (None if unavailable)."""
        settings = get_settings(context)
        if space == '3D':
            guides = settings.guides_3d
            proj, bounds = view_projection(context), get_current_bounds(context)
            if index < len(guides) and proj is not None and bounds is not None:
                info = line3_screen(proj, tuple(guides[index].point), tuple(guides[index].direction))
                return None if info is None else info["n"]
            return None
        guides = get_current_guides(settings, context)
        if guides is not None and index < len(guides):
            return guide_normal(guides[index])
        return None

    def _set_hover(self, context, hit):
        space, kind, index = hit if hit else ('2D', None, -1)
        if (index != _edit["hover"] or kind != _edit["hover_kind"]
                or space != _edit["hover_space"]):
            _edit["hover"], _edit["hover_kind"], _edit["hover_space"] = index, kind, space
            context.area.tag_redraw()

        window = context.window
        if hit is not None:
            normal = self._hover_normal(context, space, index)
            if normal is not None:
                window.cursor_set(self._cursor_for(kind, normal))
                self._cursor_hover = True
        elif self._cursor_hover:
            window.cursor_set('DEFAULT')
            self._cursor_hover = False

    # -- drag --------------------------------------------------

    def _get_cache(self, context):
        if self._cache is None:
            self._cache = GeoCache(context)
        return self._cache

    def _begin_drag(self, context, event, kind, index, space='2D'):
        if space == '3D':
            return self._begin_drag3(context, event, kind, index)
        self._space = '2D'
        settings = get_settings(context)
        group = get_current_group(settings, context)
        guide = group.guides[index]
        group.active_index = index          # selects it in the sidebar list too

        mx, my = region_mouse_coords(context, event)
        self._drag = index
        self._mode = kind
        self._orig = (guide.axis, guide.position, guide.offset, guide.angle, guide.length)
        self._last_fine = bool(event.shift and not event.ctrl)
        self._cache = None
        self._anchor2d(guide, get_current_bounds(context), mx, my)

        _edit.update(drag=index, drag_kind=kind, drag_space='2D',
                     hover=index, hover_kind=kind, hover_space='2D')
        self._cursor_hover = False
        context.window.cursor_modal_set(self._cursor_for(kind, guide_normal(guide)))
        self._update_status(context, guide, None)
        tag_redraw_3d_views()

    def _anchor2d(self, guide, bounds, mx, my):
        """(Re-)anchor a 2D drag: where the dragged handle starts, and where
        the other point is held."""
        self._anchor_m = (mx, my)
        if bounds is None:
            self._anchor_p = self._fixed = (mx, my)
            return
        A, B = handle_positions(guide, bounds)
        if self._mode == 'END':
            self._anchor_p, self._fixed = B, A
        elif self._mode == 'PIVOT':
            self._anchor_p, self._fixed = A, B
        else:
            self._anchor_p, self._fixed = A, None

    def _drag_to(self, context, event):
        if self._space == '3D':
            return self._drag_to3(context, event)
        settings = get_settings(context)
        guides = get_current_guides(settings, context)
        if guides is None or not (0 <= self._drag < len(guides)):
            self._stop_drag(context)      # group/camera changed under us
            return
        guide = guides[self._drag]
        bounds = get_current_bounds(context)
        if bounds is None or bounds[2] - bounds[0] <= 0 or bounds[3] - bounds[1] <= 0:
            return
        mx, my = region_mouse_coords(context, event)

        kinds = active_snap_kinds(settings, event)
        fine = bool(event.shift and not event.ctrl)
        if fine != self._last_fine:
            # Re-anchor whenever Shift is pressed or released so nothing jumps.
            self._last_fine = fine
            self._anchor2d(guide, bounds, mx, my)
        factor = 0.1 if fine else 1.0

        snap = None
        try:
            if self._mode == 'LINE':
                snap = self._do_line(context, settings, guide, bounds, mx, my, kinds, factor)
            elif self._mode in {'PIVOT', 'END'}:
                snap = self._do_point(context, settings, guide, bounds, mx, my, kinds, factor)
        except Exception as exc:          # never let a snap error kill the mode
            self.report({'WARNING'}, f"Snap failed: {exc}")
            snap = None

        fx_from_snap(_edit["area"], snap)
        self._update_status(context, guide, snap)
        context.area.tag_redraw()

    def _round_px(self, context, bounds, P):
        """Land on whole (render) pixels, like the number fields."""
        x, y = P
        x0, y0, x1, y1 = bounds
        dim_x, dim_y = get_pixel_dimensions(context)
        if dim_x > 0 and dim_y > 0:
            u = round((x - x0) / (x1 - x0) * dim_x) / dim_x
            v = round((y - y0) / (y1 - y0) * dim_y) / dim_y
            x, y = x0 + u * (x1 - x0), y0 + v * (y1 - y0)
        return x, y

    def _do_line(self, context, settings, guide, bounds, mx, my, kinds, factor):
        """Move the line sideways (along its normal); both points go with it."""
        d, n = guide_dir(guide), guide_normal(guide)
        P0 = self._anchor_p
        t = ((mx - self._anchor_m[0]) * n[0] + (my - self._anchor_m[1]) * n[1]) * factor
        P = (P0[0] + n[0] * t, P0[1] + n[1] * t)
        radius = settings.snap_radius
        snap, rounded = None, True

        hit = None
        if kinds & set(POINT_KINDS):
            hit = best_line_snap(self._get_cache(context), context, kinds, P, n, mx, my, radius)
        if hit is not None:
            P = (P[0] + n[0] * hit["dist"], P[1] + n[1] * hit["dist"])
            snap = {"label": hit["label"], "pt": hit["pt"], "rect": hit["rect"]}
            rounded = False
        elif 'GRID' in kinds:
            # Slide across the line until its pivot sits on the nearest grid
            # line of the axis the line mostly crosses.
            xs, ys = frame_grid(settings, bounds)
            comp = 1 if abs(n[1]) >= abs(n[0]) else 0
            target = nearest(ys if comp else xs, P[comp])
            if target is not None and abs(n[comp]) > 1e-6:
                shift = (target - P[comp]) / n[comp]
                P = (P[0] + n[0] * shift, P[1] + n[1] * shift)
                snap, rounded = {"label": _grid_label(settings)}, False

        P = constrain_line_point(P, d, n, bounds)
        if rounded:
            P = self._round_px(context, bounds, P)
        set_guide_pivot_px(guide, bounds, P[0], P[1])
        return snap

    def _do_point(self, context, settings, guide, bounds, mx, my, kinds, factor):
        """Move Point A (PIVOT) or Point B (END); the other point stays put,
        so the guide turns about it."""
        P0 = self._anchor_p
        Q = (P0[0] + (mx - self._anchor_m[0]) * factor,
             P0[1] + (my - self._anchor_m[1]) * factor)
        F = self._fixed
        radius = settings.snap_radius
        snap = None

        hit = None
        if kinds & set(POINT_KINDS):
            hit = best_point_snap(self._get_cache(context), context, kinds, mx, my, radius)
            if hit is not None and math.hypot(hit["pt"][0] - F[0], hit["pt"][1] - F[1]) <= 3.0:
                hit = None                       # a target on the other point: ignore
        if hit is not None:
            Q = hit["pt"]
            snap = {"label": hit["label"], "pt": Q, "rect": hit["rect"]}
        elif 'GRID' in kinds and self._mode == 'PIVOT':
            xs, ys = frame_grid(settings, bounds)
            if xs and ys:
                Q = (nearest(xs, Q[0]), nearest(ys, Q[1]))
                snap = {"label": _grid_label(settings)}
        elif 'GRID' in kinds:
            # Point B with grid snapping: the angle goes in Angle Step steps.
            step = max(settings.angle_step, 1e-4)
            r = math.hypot(Q[0] - F[0], Q[1] - F[1])
            th = round(math.atan2(Q[1] - F[1], Q[0] - F[0]) / step) * step
            Q = (F[0] + math.cos(th) * r, F[1] + math.sin(th) * r)
            snap = {"label": f"{math.degrees(step):g}° steps"}
        elif self._mode == 'PIVOT':
            Q = self._round_px(context, bounds, Q)

        if self._mode == 'PIVOT':
            x0, y0, x1, y1 = bounds
            Q = (max(x0, min(x1, Q[0])), max(y0, min(y1, Q[1])))
        if math.hypot(Q[0] - F[0], Q[1] - F[1]) < 2.0:
            return snap                          # the two points can't meet
        if self._mode == 'END':
            set_guide_points_px(guide, bounds, F, Q)
        else:
            set_guide_points_px(guide, bounds, Q, F)
        return snap

    # -- 3D guides ---------------------------------------------

    def _begin_drag3(self, context, event, kind, index):
        settings = get_settings(context)
        guides = settings.guides_3d
        g = guides[index]
        settings.guides_3d_active_index = index

        proj, bounds = view_projection(context), get_current_bounds(context)
        info = guide3_info(proj, g, bounds)
        mx, my = region_mouse_coords(context, event)

        self._space = '3D'
        self._drag, self._mode = index, kind
        self._orig = (Vector(g.point), Vector(g.direction), g.length)
        self._anchor_m = (mx, my)
        self._last_fine = bool(event.shift and not event.ctrl)
        self._cache = None
        self._anchor_in_frame(context, g, mx, my)

        _edit.update(drag=index, drag_kind=kind, drag_space='3D',
                     hover=index, hover_kind=kind, hover_space='3D')
        self._cursor_hover = False
        normal = info["n"] if info is not None else (0.0, 1.0)
        context.window.cursor_modal_set(self._cursor_for(kind, normal))
        self._update_status3(context, g, None)
        tag_redraw_3d_views()

    def _anchor_in_frame(self, context, g, mx, my):
        """(Re-)anchor a 3D drag at the guide's current state and the pointer."""
        region, rv3d = get_view_region(context), get_region_3d(context)
        proj = view_projection(context)
        a, d, b = Vector(g.point), Vector(g.direction), Vector(g.point_b)
        self._base = (a.copy(), d.copy(), b.copy())
        self._anchor_m = (mx, my)
        if self._mode == 'LINE':
            ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mx, my))
            rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mx, my))
            grab = point_on_line_under_ray(ro, rd, a, d)
            self._grab = a.copy() if grab is None else grab
        else:
            self._grab = (b if self._mode == 'END' else a).copy()
        self._anchor_p = project_point(proj, self._grab) or (mx, my)

    def _drag_to3(self, context, event):
        settings = get_settings(context)
        guides = settings.guides_3d
        if not (0 <= self._drag < len(guides)):
            self._stop_drag(context)
            return
        g = guides[self._drag]
        bounds = get_current_bounds(context)
        if bounds is None or bounds[2] - bounds[0] <= 0 or bounds[3] - bounds[1] <= 0:
            return
        mx, my = region_mouse_coords(context, event)
        kinds = active_snap_kinds(settings, event)
        fine = bool(event.shift and not event.ctrl)
        if fine != self._last_fine:
            self._last_fine = fine
            self._anchor_in_frame(context, g, mx, my)
        factor = 0.1 if fine else 1.0

        snap = None
        try:
            if self._mode == 'LINE':
                snap = self._do_line3(context, settings, g, mx, my, kinds, factor)
            elif self._mode in {'PIVOT', 'END'}:
                snap = self._do_point3(context, settings, g, mx, my, kinds, factor)
        except Exception as exc:
            self.report({'WARNING'}, f"Snap failed: {exc}")
            snap = None

        fx_from_snap(_edit["area"], snap)
        self._update_status3(context, g, snap)
        context.area.tag_redraw()

    def _do_line3(self, context, settings, g, mx, my, kinds, factor):
        """Slide the line (both points) so it stays exactly under the pointer.
        The line moves in the plane through the held point that faces the
        view (a line's picture tilts with perspective, so "under the pointer"
        is solved in 3D rather than by shifting along a fixed screen direction)."""
        region, rv3d = get_view_region(context), get_region_3d(context)
        proj = view_projection(context)
        a0, d, _b0 = self._base
        cur = (self._anchor_m[0] + (mx - self._anchor_m[0]) * factor,
               self._anchor_m[1] + (my - self._anchor_m[1]) * factor)
        Wc = screen_to_plane(region, rv3d, cur, self._grab)
        if Wc is None:
            return None
        move = Wc - self._grab
        point = a0 + (move - d * move.dot(d))     # sliding along the line changes nothing
        snap = None

        hit = None
        if kinds & set(POINT_KINDS):
            info = line3_screen(proj, tuple(point), tuple(d))
            if info is not None:
                hit = best_line_snap(self._get_cache(context), context, kinds,
                                     cur, info["n"], mx, my, settings.snap_radius)
        if hit is not None:
            point = line_through_target(point, d, snap_world_point(context, hit))
            snap = {"label": hit["label"], "pt": hit["pt"], "rect": hit["rect"]}
        elif 'GRID' in kinds:
            step = settings.grid_step_3d
            point = line_through_target(point, d, snap_to_world_grid(Wc, step))
            snap = {"label": f"Grid {step:g}"}
        g.point = tuple(point)
        return snap

    def _do_point3(self, context, settings, g, mx, my, kinds, factor):
        """Move Point A (PIVOT) or Point B (END) freely in the view plane; the
        other point stays put, so the guide turns about it."""
        region, rv3d = get_view_region(context), get_region_3d(context)
        a0, _d, b0 = self._base
        moving, fixed = (b0, a0) if self._mode == 'END' else (a0, b0)
        P0 = self._anchor_p
        target_px = (P0[0] + (mx - self._anchor_m[0]) * factor,
                     P0[1] + (my - self._anchor_m[1]) * factor)
        point = screen_to_plane(region, rv3d, target_px, moving)
        if point is None:
            return None
        snap = None

        hit = None
        if kinds & set(POINT_KINDS):
            hit = best_point_snap(self._get_cache(context), context, kinds, mx, my, settings.snap_radius)
        if hit is not None:
            point = snap_world_point(context, hit)
            snap = {"label": hit["label"], "pt": hit["pt"], "rect": hit["rect"]}
        elif 'GRID' in kinds:
            step = settings.grid_step_3d
            point = snap_to_world_grid(point, step)
            snap = {"label": f"Grid {format(step, 'g')}"}
        if (point - fixed).length < 1e-6:
            return snap                    # the two points can't meet
        if self._mode == 'END':
            set_guide_points(g, fixed, point)
        else:
            set_guide_points(g, point, fixed)
        return snap

    def _update_status3(self, context, g, snap):
        if self._mode in {'PIVOT', 'END'}:
            p = g.point_b if self._mode == 'END' else g.point
            which = "B" if self._mode == 'END' else "A"
            text = (f"{g.name} (3D):  point {which} ({p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f})"
                    f"   A-B {g.length:.3f}")
        else:
            p = g.point
            text = f"{g.name} (3D):  point A ({p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f})"
        if snap:
            text += f"   [{snap['label']}]"
        context.workspace.status_text_set(
            f"{text}   |   {SNAP_HINT}   |   Shift: fine   |   Esc / RMB: cancel"
        )

    # -- status / finish ---------------------------------------

    def _update_status(self, context, guide, snap):
        dim_x, dim_y = get_pixel_dimensions(context)
        px = norm_to_pixel(guide.position, dim_y if guide.axis == 'H' else dim_x)
        text = f"{guide.name}:  {px} px  ({guide.position * 100.0:.1f}%)"
        if guide_is_tilted(guide):
            text += f"  {math.degrees(guide_angle_abs(guide)):.1f}°"
        if snap:
            text += f"   [{snap['label']}]"
        context.workspace.status_text_set(
            f"{text}   |   {SNAP_HINT}   |   Shift: fine   |   Esc / RMB: cancel"
        )

    def _stop_drag(self, context):
        self._drag = -1
        self._mode = None
        self._cache = None
        self._space = '2D'
        _edit.update(drag=-1, drag_kind=None, drag_space='2D')
        fx_reset()
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(self.IDLE_HINT)
        tag_redraw_3d_views()

    def _finish_drag(self, context):
        self._stop_drag(context)
        try:
            bpy.ops.ed.undo_push(message="Edit Guide")
        except Exception:
            pass

    def _cancel_drag(self, context):
        settings = get_settings(context)
        if self._space == '3D':
            guides = settings.guides_3d
            if self._orig is not None and 0 <= self._drag < len(guides):
                g = guides[self._drag]
                g.point, g.direction = tuple(self._orig[0]), tuple(self._orig[1])
                g.length = self._orig[2]
            self._stop_drag(context)
            return
        guides = get_current_guides(settings, context)
        if guides is not None and self._orig is not None and 0 <= self._drag < len(guides):
            g = guides[self._drag]
            g.axis, g.position, g.offset, g.angle, g.length = self._orig
        self._stop_drag(context)


# ------------------------------------------------------------
#  Guide from edge
# ------------------------------------------------------------

class GUIDEBRO_OT_edge_guide(bpy.types.Operator):
    """Pick a mesh edge in the viewport: a guide is placed along it and rotated to match"""
    bl_idname = "guidebro.edge_guide"
    bl_label = "Guide from Edge"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        items=[('NEW', "New Guide", "Create a new guide along the picked edge"),
               ('ACTIVE', "Active Guide", "Move and rotate the active guide onto the picked edge")],
        default='NEW',
        options={'SKIP_SAVE'},
    )
    space: bpy.props.EnumProperty(
        items=[('SCREEN', "2D Guide", "A guide drawn on the view, matching the edge as seen"),
               ('WORLD', "3D Guide", "A line in world space, along the edge's real 3D direction")],
        default='SCREEN',
        options={'SKIP_SAVE'},
    )

    _cache = None
    _hit = None
    _cursor_set = False

    @classmethod
    def description(cls, context, properties):
        kind = "3D guide" if properties.space == 'WORLD' else "guide"
        if properties.target == 'ACTIVE':
            return f"Click a mesh edge: the active {kind} moves onto it and rotates to match its direction"
        return f"Click a mesh edge: a new {kind} is placed along it and rotated to match its direction"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        settings = get_settings(context)
        if self.space == 'SCREEN' and is_camera_view(context) and context.scene.camera is None:
            self.report({'WARNING'}, "No active camera in this scene")
            return {'CANCELLED'}
        if self.target == 'ACTIVE':
            guides = settings.guides_3d if self.space == 'WORLD' else get_current_guides(settings, context)
            if not guides:
                self.report({'WARNING'}, "There is no active guide to align")
                return {'CANCELLED'}

        self._cache = GeoCache(context, want_edges=True)
        self._hit = None
        context.window.cursor_modal_set('EYEDROPPER')
        self._cursor_set = True
        context.workspace.status_text_set("Click a mesh edge to align a guide to it   |   Esc / RMB: cancel")
        fx_reset(context.area.as_pointer())
        context.window_manager.modal_handler_add(self)
        tag_redraw_3d_views()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self._cleanup(context)
            return {'CANCELLED'}

        in_view = mouse_over_viewport(context, event)

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._hover(context, event, in_view)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and in_view:
            self._hover(context, event, in_view)
            if self._hit is None:
                self.report({'WARNING'}, "No edge under the cursor")
                return {'RUNNING_MODAL'}
            ok = self._apply(context)
            self._cleanup(context)
            return {'FINISHED'} if ok else {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._cleanup(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _hover(self, context, event, in_view):
        self._hit = None
        fx_reset(context.area.as_pointer())
        if in_view:
            mx, my = region_mouse_coords(context, event)
            radius = get_settings(context).snap_radius
            try:
                self._hit = self._cache.nearest_edge(context, mx, my, radius)
            except Exception as exc:
                self.report({'WARNING'}, f"Edge search failed: {exc}")
        hit = self._hit
        if hit is not None:
            _fx["thick"].append((hit["a"], hit["b"]))
            _fx["pts"].append(hit["q"])
            bounds = get_current_bounds(context)
            if bounds is not None:
                theta = math.atan2(hit["b"][1] - hit["a"][1], hit["b"][0] - hit["a"][0])
                d = (math.cos(theta), math.sin(theta))
                res = clip_line_to_rect(hit["q"], d, bounds)
                if res is not None:
                    _fx["thin"].append(((hit["q"][0] + d[0] * res[0], hit["q"][1] + d[1] * res[0]),
                                        (hit["q"][0] + d[0] * res[1], hit["q"][1] + d[1] * res[1])))
            _fx["label"] = (hit["q"][0] + 10, hit["q"][1] + 10, f"{hit['obj'].name} edge")
        context.area.tag_redraw()

    def _apply_world(self, context):
        settings = get_settings(context)
        hit = self._hit
        direction = hit["wb"] - hit["wa"]
        if direction.length < 1e-9:
            self.report({'WARNING'}, "That edge has no length")
            return False
        direction.normalize()
        if self.target == 'ACTIVE':
            guides = settings.guides_3d
            if not guides:
                self.report({'WARNING'}, "There is no active 3D guide to align")
                return False
            g = guides[max(0, min(settings.guides_3d_active_index, len(guides) - 1))]
        else:
            name = unique_name((g.name for g in settings.guides_3d), "Edge Guide")
            g = add_3d_guide(settings, name, hit["wa"], direction, get_default_color(settings, context))
        # The guide's two points are the edge's two ends.
        set_guide_points(g, hit["wa"], hit["wb"])
        self.report({'INFO'}, f"3D guide aligned to an edge of \"{hit['obj'].name}\"")
        return True

    def _apply(self, context):
        if self.space == 'WORLD':
            return self._apply_world(context)
        settings = get_settings(context)
        bounds = get_current_bounds(context)
        if bounds is None:
            self.report({'WARNING'}, "Could not read viewport/camera frame bounds")
            return False
        hit = self._hit
        theta = math.atan2(hit["b"][1] - hit["a"][1], hit["b"][0] - hit["a"][0])

        if self.target == 'ACTIVE':
            group = get_current_group(settings, context)
            if group is None or not group.guides:
                self.report({'WARNING'}, "There is no active guide to align")
                return False
            index = max(0, min(group.active_index, len(group.guides) - 1))
            guide = group.guides[index]
        else:
            group = ensure_group(settings, context)
            if group is None:
                self.report({'WARNING'}, "No active camera in this scene")
                return False
            guides = group.guides
            name = unique_name((g.name for g in guides), "Edge Guide")
            guide = guides.add()
            guide.name = name
            guide.color = get_default_color(settings, context)
            guide.visible = True
            group.active_index = len(guides) - 1

        set_guide_points_px(guide, bounds, hit["a"], hit["b"])
        self.report({'INFO'}, f"Guide aligned to an edge of \"{hit['obj'].name}\"")
        return True

    def _cleanup(self, context):
        if self._cursor_set:
            context.window.cursor_modal_restore()
            self._cursor_set = False
        context.workspace.status_text_set(None)
        fx_reset()
        self._cache = None
        tag_redraw_3d_views()


classes = (
    GUIDEBRO_OT_edit_mode,
    GUIDEBRO_OT_edge_guide,
)
