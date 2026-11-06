# ------------------------------------------------------------
#  Object snapping during Blender's own Move (G / Move tool / gizmo)
# ------------------------------------------------------------
#
#  Python cannot add snap targets to Blender's transform operator, so this
#  works alongside it instead:
#
#  * While transform.translate is running (Window.modal_operators), every
#    depsgraph update the move causes is followed by a correction: if the
#    selection's origin / bounds / a vertex comes within the snap distance of
#    a visible guide (in the view the move happens in), the selection is
#    shifted onto it. The move operator recomputes positions from where it
#    started on every mouse move, so the correction never accumulates; it is
#    just re-applied while the guide stays in reach.
#  * A tiny pass-through modal ("view tracker") remembers which 3D view the
#    pointer is in, because a depsgraph handler cannot know which view the
#    move is being made in. Without it the camera view (or the largest view)
#    is used. Being a window-level handler, it also sees clicks before the 3D
#    view does, which is how drags out of the rulers are caught (rulers.py).

import bmesh
import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras import view3d_utils
from mathutils import Vector

from .common import (
    MODIFIER_KEYS, _fx, fx_reset, get_current_bounds, get_region_3d, get_view_region,
    screen_to_plane, tag_redraw_3d_views,
)
from . import rulers
from .screen import frame_grid, get_current_group, guide_segment
from .snapping import bounds_key_points, evaluated_local_coords, snap_move_to_guides
from .world import bbox_key_points, line3_screen, point_on_line_under_ray, project_points


MOVE_OPS = {'TRANSFORM_OT_translate'}
MAX_SNAP_VERTS = 200_000     # per object; denser meshes snap by origin / bounds only
SETTLED_PX = 0.05            # closer than this to the guide counts as already snapped

_state = {
    "busy": False,       # re-entry guard: our own writes cause another update
    "active": False,     # a move is being snapped right now
    "local": {},         # as_pointer() -> local vertex coords (or None), per move
}

_trackers = set()        # as_pointer() of the windows that run a view tracker
_pointer = {"window": None, "area": None, "region": None}


def supported():
    """Window.modal_operators is what tells us a move is running."""
    wm = bpy.context.window_manager
    return bool(wm and wm.windows) and hasattr(wm.windows[0], "modal_operators")


def _moving_window(wm):
    for win in wm.windows:
        for op in getattr(win, "modal_operators", ()):
            if op.bl_idname in MOVE_OPS:
                return win
    return None


def move_in_progress():
    wm = bpy.context.window_manager
    return wm is not None and _moving_window(wm) is not None


# ------------------------------------------------------------
#  Which view is the move happening in?
# ------------------------------------------------------------

def _find_region(win, area_ptr, region_ptr):
    for area in win.screen.areas:
        if area.as_pointer() == area_ptr and area.type == 'VIEW_3D':
            for region in area.regions:
                if region.as_pointer() == region_ptr:
                    return area, region
    return None, None


def _pick_view(win):
    """(area, region) of the 3D view the move is happening in."""
    if _pointer["window"] == win.as_pointer():
        area, region = _find_region(win, _pointer["area"], _pointer["region"])
        if area is not None:
            return area, region
    # Fallback: a view looking through the camera, else the largest one.
    best, best_key = (None, None), None
    for area in win.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        for region in area.regions:
            if region.type != 'WINDOW':
                continue
            rv3d = region.data if region.data is not None else space.region_3d
            cam = rv3d is not None and rv3d.view_perspective == 'CAMERA'
            key = (cam, region.width * region.height)
            if best_key is None or key > best_key:
                best, best_key = (area, region), key
    return best


def _tracker_wanted(settings):
    return settings is not None and (settings.obj_snap or settings.show_rulers)


class GUIDEBRO_OT_view_tracker(bpy.types.Operator):
    """Remembers which 3D view the pointer is over (for object snapping) and
    catches drags out of the rulers"""
    bl_idname = "guidebro.view_tracker"
    bl_label = "GuideBro View Tracker"
    bl_options = {'INTERNAL'}

    _win = 0
    _drag = None             # rulers.RulerDrag while a guide is dragged out of a ruler
    _cursor = None           # cursor we set while hovering a ruler

    def invoke(self, context, event):
        win = context.window
        if win is None or win.as_pointer() in _trackers:
            return {'CANCELLED'}
        self._win = win.as_pointer()
        self._drag = None
        self._cursor = None
        _trackers.add(self._win)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        settings = getattr(context.scene, "guidebro", None)
        if self._drag is not None:
            return self._drag_event(context, event)
        if not _tracker_wanted(settings):
            self._set_cursor(context, None)
            _trackers.discard(self._win)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or event.value == 'PRESS':
            view = self._record(context, event.mouse_x, event.mouse_y)
            side = None
            if settings.show_rulers and view is not None:
                area, region = view
                with context.temp_override(area=area, region=region):
                    side = rulers.ruler_hit(context, event.mouse_x - region.x,
                                            event.mouse_y - region.y)
                    if (side in {'TOP', 'LEFT'} and event.type == 'LEFTMOUSE'
                            and event.value == 'PRESS'):
                        return self._start_drag(context, event, side, area, region)
            self._set_cursor(context, {'TOP': 'MOVE_Y', 'LEFT': 'MOVE_X'}.get(side))
        return {'PASS_THROUGH'}

    def cancel(self, context):
        if self._drag is not None:
            try:
                self._with_drag_view(context, lambda r: self._drag.cancel(context))
            except Exception:
                pass
            self._drag = None
        _trackers.discard(self._win)

    # -- ruler drags ---------------------------------------------

    def _set_cursor(self, context, cursor):
        if cursor == self._cursor:
            return
        if cursor is None:
            context.window.cursor_set('DEFAULT')
        else:
            context.window.cursor_set(cursor)
        self._cursor = cursor

    def _start_drag(self, context, event, side, area, region):
        try:
            self._drag = rulers.RulerDrag(context, side)
            self._drag.update(context, event, event.mouse_x - region.x, event.mouse_y - region.y)
        except Exception as exc:
            print(f"GuideBro: ruler drag failed: {exc}")
            self._drag = None
            return {'PASS_THROUGH'}
        context.window.cursor_modal_set('MOVE_Y' if side == 'TOP' else 'MOVE_X')
        return {'RUNNING_MODAL'}

    def _with_drag_view(self, context, fn):
        """Run fn with the 3D view the drag started in as the context."""
        area, region = _find_region(context.window, self._drag.area_ptr, self._drag.region_ptr)
        if area is None:
            return None
        with context.temp_override(area=area, region=region):
            return fn(region)

    def _drag_event(self, context, event):
        drag = self._drag

        def mouse(region):
            return event.mouse_x - region.x, event.mouse_y - region.y

        try:
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or event.type in MODIFIER_KEYS:
                self._with_drag_view(context, lambda r: drag.update(context, event, *mouse(r)))
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if self._with_drag_view(context, lambda r: drag.finish(context, *mouse(r)) or True) is None:
                    drag.cancel(context)
                return self._end_drag(context)
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self._with_drag_view(context, lambda r: drag.cancel(context))
                return self._end_drag(context)
        except Exception as exc:
            print(f"GuideBro: ruler drag failed: {exc}")
            return self._end_drag(context)
        return {'RUNNING_MODAL'}

    def _end_drag(self, context):
        self._drag = None
        context.window.cursor_modal_restore()
        self._cursor = None
        return {'RUNNING_MODAL'}

    def _record(self, context, x, y):
        """Remember (and return) the 3D view WINDOW region under (x, y)."""
        win = context.window
        if win is None or win.screen is None:
            return None
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            if not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
                continue
            for region in area.regions:
                if (region.type == 'WINDOW'
                        and region.x <= x < region.x + region.width
                        and region.y <= y < region.y + region.height):
                    _pointer.update(window=win.as_pointer(), area=area.as_pointer(),
                                    region=region.as_pointer())
                    return area, region
            return None
        return None


def _start_trackers():
    try:
        wm = bpy.context.window_manager
        for win in wm.windows:
            if win.as_pointer() in _trackers or win.scene is None:
                continue
            if not _tracker_wanted(getattr(win.scene, "guidebro", None)):
                continue
            with bpy.context.temp_override(window=win):
                bpy.ops.guidebro.view_tracker('INVOKE_DEFAULT')
    except Exception as exc:     # never break the UI over a convenience
        print(f"GuideBro: could not start the view tracker: {exc}")
    return None


def ensure_trackers_later():
    if not bpy.app.timers.is_registered(_start_trackers):
        bpy.app.timers.register(_start_trackers, first_interval=0.05)


# ------------------------------------------------------------
#  Snapping
# ------------------------------------------------------------

def _end_move():
    if _state["active"]:
        _state["active"] = False
        _state["local"] = {}
        if _fx.get("owner") == 'OBJSNAP':
            fx_reset()
            tag_redraw_3d_views()


def _local_coords(obj, depsgraph):
    key = obj.as_pointer()
    if key not in _state["local"]:
        _state["local"][key] = evaluated_local_coords(obj, depsgraph, MAX_SNAP_VERTS)
    return _state["local"][key]


def _moving_objects(context):
    """The selected objects the move acts on: the top-most selected ones."""
    sel = [o for o in context.view_layer.objects.selected if o.visible_get()]
    picked = set(sel)

    def has_selected_parent(o):
        p = o.parent
        while p is not None:
            if p in picked:
                return True
            p = p.parent
        return False

    return [o for o in sel if not has_selected_parent(o)]


def _world_points(objs, depsgraph, want_verts):
    """origins (list of Vector), vertices (N,3 float64 or None), box lo/hi."""
    origins, verts = [], []
    for o in objs:
        ev = o.evaluated_get(depsgraph)
        mw = ev.matrix_world
        origins.append(mw.translation.copy())
        co = _local_coords(o, depsgraph) if want_verts else None
        if co is not None:
            m = np.array(mw, dtype=np.float64)
            verts.append(co.astype(np.float64) @ m[:3, :3].T + m[:3, 3])
    allv = np.concatenate(verts) if verts else None
    pts = [np.array([tuple(p) for p in origins], dtype=np.float64)]
    if allv is not None:
        pts.append(allv)
    stack = np.concatenate(pts)
    return origins, allv, stack.min(axis=0), stack.max(axis=0)


def _screen_lines(context, settings, bounds):
    """2D snap lines in this view: [(key, P, n, (A, B))]."""
    lines = []
    group = get_current_group(settings, context)
    if group is not None:
        for i, g in enumerate(group.guides):
            if not g.visible:
                continue
            seg = guide_segment(g, bounds)
            if seg is not None:
                P, _d, n, A, B = seg
                lines.append((('G', i), P, n, (A, B)))
    if settings.obj_snap_grid:
        x0, y0, x1, y1 = bounds
        xs, ys = frame_grid(settings, bounds)
        for k, x in enumerate(xs):
            lines.append((('GV', k), (x, y0), (1.0, 0.0), ((x, y0), (x, y1))))
        for k, y in enumerate(ys):
            lines.append((('GH', k), (x0, y), (0.0, 1.0), ((x0, y), (x1, y))))
    return lines


def _apply_offset(objs, D, depsgraph):
    """Move each object by the world-space offset D (via its location, so
    rotation and scale are never touched)."""
    for o in objs:
        if o.parent is not None:
            ev = o.evaluated_get(depsgraph)
            pw = ev.matrix_world @ ev.matrix_basis.inverted_safe()  # parent space
            local = pw.to_3x3().inverted_safe() @ D
        else:
            local = D
        o.location = o.location + local


def _snap_lines(context, settings, region, rv3d, bounds):
    """Everything to snap to in this view: (proj, 2D lines, 3D lines)."""
    proj = (np.array(rv3d.perspective_matrix, dtype=np.float64),
            float(region.width), float(region.height))
    lines = _screen_lines(context, settings, bounds)
    lines3 = [(i, Vector(g.point), Vector(g.direction))
              for i, g in enumerate(settings.guides_3d) if g.visible]
    return proj, lines, lines3


def _show_hits(context, lines, hits, hits3, pts):
    """Snap feedback: highlight the guides / grid lines that caught something."""
    fx_reset(context.area.as_pointer(), 'OBJSNAP')
    seg_by_key = {k: seg for k, _P, _n, seg in lines}
    for key, _k, _res in hits:
        if key[0] == 'G':
            _fx["guides"].add(key[1])
        else:
            _fx["thin"].append(seg_by_key[key])
    _fx["guides3"] = set(hits3)
    _fx["pts"].extend(pts)
    context.area.tag_redraw()


# -- Edit Mode: the selected vertices snap -----------------------------------

def _edit_meshes(context):
    """[(object, bmesh, [selected vertex indices])] of the meshes in Edit Mode.
    The selection can't change during a move, so it is read once per move."""
    out = []
    for o in context.view_layer.objects:
        if o.type != 'MESH' or o.mode != 'EDIT':
            continue
        bm = bmesh.from_edit_mesh(o.data)
        key = ("edit", o.as_pointer())
        if key not in _state["local"]:
            _state["local"][key] = [v.index for v in bm.verts if v.select and not v.hide]
        idx = _state["local"][key]
        if idx:
            bm.verts.ensure_lookup_table()
            out.append((o, bm, idx))
    return out


def _snap_edit(context, settings):
    """While vertices are moved in Edit Mode, the selected vertices snap to
    guides (Objects snap to guides + Vertex)."""
    if not settings.obj_snap_vertex:
        return
    region, rv3d = get_view_region(context), get_region_3d(context)
    bounds = get_current_bounds(context)
    if region is None or rv3d is None or bounds is None:
        return
    if bounds[2] - bounds[0] <= 1 or bounds[3] - bounds[1] <= 1:
        return
    meshes = _edit_meshes(context)
    if not meshes or sum(len(i) for _o, _b, i in meshes) > MAX_SNAP_VERTS:
        return
    proj, lines, lines3 = _snap_lines(context, settings, region, rv3d, bounds)
    if not lines and not lines3:
        return
    radius = settings.snap_radius
    lines2 = [(k, P, n) for k, P, n, _seg in lines]

    def world_now(D):
        parts = []
        for o, bm, idx in meshes:
            m = np.array(o.matrix_world, dtype=np.float64)
            co = np.array([tuple(bm.verts[i].co) for i in idx], dtype=np.float64)
            parts.append(co @ m[:3, :3].T + m[:3, 3])
        return np.concatenate(parts) + np.array(tuple(D))

    # 2D guides: exact for real points; a second pass refines a two-line
    # crossing caught by vertices at different depths.
    D = Vector((0.0, 0.0, 0.0))
    hits, score2 = [], None
    for _it in range(3):
        world = world_now(D)
        xy, valid = project_points(proj, world)
        xy, world = xy[valid], world[valid]
        if not len(xy):
            break
        delta, hits = snap_move_to_guides(xy, (0.0, 0.0), lines2, radius)
        if not hits:
            break
        if score2 is None:
            score2 = min(abs(h[2]) for h in hits)
        if max(abs(h[2]) for h in hits) < SETTLED_PX:
            break
        k = hits[0][1]
        W = Vector(world[k].tolist())
        target = (float(xy[k, 0] + delta[0]), float(xy[k, 1] + delta[1]))
        W2 = screen_to_plane(region, rv3d, target, W)
        if W2 is None:
            hits = []
            break
        D = D + (W2 - W)

    hits3, pts3 = [], []
    if lines3:
        D3, h3, p3, score3 = _snap_points_3d(region, rv3d, proj, world_now(Vector()), lines3, radius)
        if h3 and (not hits or score2 is None or score3 < score2):
            D, hits, hits3, pts3 = D3, [], h3, p3

    if D.length > 1e-9:
        for o, bm, idx in meshes:
            local = o.matrix_world.to_3x3().inverted_safe() @ D
            for i in idx:
                bm.verts[i].co += local
            bmesh.update_edit_mesh(o.data, loop_triangles=False, destructive=False)

    pts = list(pts3)
    if hits:
        xy, valid = project_points(proj, world_now(Vector()))
        xy = xy[valid]
        pts += [(float(xy[k, 0]), float(xy[k, 1])) for _key, k, _res in hits if k < len(xy)]
    _show_hits(context, lines, hits, hits3, pts)


def _snap(context, settings, depsgraph):
    if context.mode == 'EDIT_MESH':
        return _snap_edit(context, settings)
    if context.mode != 'OBJECT':
        return
    region, rv3d = get_view_region(context), get_region_3d(context)
    bounds = get_current_bounds(context)
    if region is None or rv3d is None or bounds is None:
        return
    if bounds[2] - bounds[0] <= 1 or bounds[3] - bounds[1] <= 1:
        return
    objs = _moving_objects(context)
    if not objs:
        return

    use_o, use_b, use_v = settings.obj_snap_origin, settings.obj_snap_bounds, settings.obj_snap_vertex
    if not (use_o or use_b or use_v):
        return

    proj = (np.array(rv3d.perspective_matrix, dtype=np.float64),
            float(region.width), float(region.height))
    radius = settings.snap_radius
    lines = _screen_lines(context, settings, bounds)
    lines3 = [(i, Vector(g.point), Vector(g.direction))
              for i, g in enumerate(settings.guides_3d) if g.visible]
    if not lines and not lines3:
        return

    def refs_now(D):
        """Reference points after moving by D:
        (world (N,3), screen (N,2), kinds, on-screen box or None)."""
        origins, verts, lo, hi = _world_points(objs, depsgraph, use_v or use_b)
        off = np.array(tuple(D), dtype=np.float64)
        org = np.array([tuple(p) for p in origins], dtype=np.float64).reshape(-1, 3)
        parts, kinds = [], []
        if use_o:
            parts.append(org + off)
            kinds += ['origin'] * len(org)
        if use_v and verts is not None:
            parts.append(verts + off)
            kinds += ['vertex'] * len(verts)
        world = np.concatenate(parts) if parts else np.zeros((0, 3))
        xy, valid = project_points(proj, world)
        xy, world = xy[valid], world[valid]
        kinds = [k for k, ok in zip(kinds, valid) if ok]
        box = None
        if use_b:
            pts = (verts if verts is not None else org) + off
            xy_all, ok = project_points(proj, pts)
            if ok.any():
                v = xy_all[ok]
                box = (float(v[:, 0].min()), float(v[:, 1].min()),
                       float(v[:, 0].max()), float(v[:, 1].max()))
                # The box's key points are screen spots; give them the depth
                # of the selection's centre.
                centre = (lo + hi) / 2.0 + off
                xy = np.concatenate([xy, np.array(bounds_key_points(box))])
                world = np.concatenate([world, np.repeat(centre[None, :], 9, axis=0)])
                kinds += ['bounds'] * 9
        return world, xy, kinds, box

    D = Vector((0.0, 0.0, 0.0))
    hits, box = [], None
    lines2 = [(k, P, n) for k, P, n, _seg in lines]

    # -- 2D guides (and grid): solved on screen, then lifted into the view plane.
    score2 = None                 # how far the nearest 2D guide was, before snapping
    for _it in range(4):
        world, xy, kinds, box = refs_now(D)
        if not len(xy):
            break
        delta, hits = snap_move_to_guides(xy, (0.0, 0.0), lines2, radius)
        if not hits:
            break
        if score2 is None:
            score2 = min(abs(h[2]) for h in hits)
        if max(abs(h[2]) for h in hits) < SETTLED_PX:
            break
        # Lift the screen move into the view plane at the reference point's
        # depth. Both ends are lifted, because a bounds key point is a screen
        # spot that is not at `world[k]` itself (only at its depth).
        k = hits[0][1]
        W = Vector(world[k].tolist())
        here = (float(xy[k, 0]), float(xy[k, 1]))
        target = (here[0] + delta[0], here[1] + delta[1])
        W1 = screen_to_plane(region, rv3d, here, W)
        W2 = screen_to_plane(region, rv3d, target, W)
        if W1 is None or W2 is None:
            hits = []
            break
        D = D + (W2 - W1)
        if kinds[k] != 'bounds':
            break                 # exact for real 3D points; the box needs a re-measure

    # -- 3D guides: whichever kind of guide is nearer wins.
    hits3, pts3 = [], []
    if lines3:
        D3, h3, p3, score3 = _snap_3d(context, region, rv3d, proj, objs, depsgraph,
                                      use_o, use_b, use_v, lines3, radius)
        if h3 and (not hits or score2 is None or score3 < score2):
            D, hits, hits3, pts3 = D3, [], h3, p3

    # -- write + feedback
    if D.length > 1e-9:
        _apply_offset(objs, D, depsgraph)

    fx_reset(context.area.as_pointer(), 'OBJSNAP')
    seg_by_key = {k: seg for k, _P, _n, seg in lines}
    for key, k, _res in hits:
        if key[0] == 'G':
            _fx["guides"].add(key[1])
        else:
            _fx["thin"].append(seg_by_key[key])
    _fx["guides3"] = set(hits3)
    if hits or hits3:
        world, xy, kinds, box = refs_now(D)
        for _key, k, _res in hits:
            if k < len(xy):
                _fx["pts"].append((float(xy[k, 0]), float(xy[k, 1])))
        _fx["pts"].extend(pts3)
        if use_b and box is not None:
            _fx["rects"].append(box)
    context.area.tag_redraw()


def _snap_3d(context, region, rv3d, proj, objs, depsgraph, use_o, use_b, use_v, lines3, radius):
    """Land a reference point exactly on a 3D guide (sliding along it).
    -> (world offset, [guide index], [screen points], screen distance before)"""
    origins, verts, lo, hi = _world_points(objs, depsgraph, use_v or use_b)
    refs = []
    if use_o:
        refs += [tuple(p) for p in origins]
    if use_b:
        refs += [tuple(p) for p in bbox_key_points(tuple(lo.tolist()), tuple(hi.tolist()))]
    R = np.array(refs, dtype=np.float64).reshape(-1, 3)
    if use_v and verts is not None:
        R = np.concatenate([R, verts])
    return _snap_points_3d(region, rv3d, proj, R, lines3, radius)


def _snap_points_3d(region, rv3d, proj, R, lines3, radius):
    """Land the point of R (N,3 world) nearest a 3D guide on screen exactly on
    that guide. -> (world offset, [guide index], [screen points], screen
    distance before)"""
    zero = Vector((0.0, 0.0, 0.0))
    if not len(R):
        return zero, [], [], None
    sp, valid = project_points(proj, R)

    best = None
    for i, a, d in lines3:
        info = line3_screen(proj, tuple(a), tuple(d))
        if info is None:
            continue
        n = info["n"]
        dist = (sp[:, 0] - info["A"][0]) * n[0] + (sp[:, 1] - info["A"][1]) * n[1]
        dist = np.where(valid, dist, np.inf)
        k = int(np.argmin(np.abs(dist)))
        dk = float(dist[k])
        if abs(dk) <= radius and (best is None or abs(dk) < best[0]):
            best = (abs(dk), i, k, dk, n, a, d)
    if best is None:
        return zero, [], [], None
    score, i, k, dk, n, a, d = best
    q = (float(sp[k, 0] - n[0] * dk), float(sp[k, 1] - n[1] * dk))
    if score < SETTLED_PX:
        return zero, [i], [q], score
    ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, q)
    rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, q)
    X = point_on_line_under_ray(ro, rd, a, d)
    if X is None:
        return zero, [], [], None
    return X - Vector(R[k].tolist()), [i], [q], score


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if _state["busy"]:
        return
    try:
        settings = scene.guidebro
    except AttributeError:
        return
    if not settings.obj_snap or settings.guides_hidden:
        _end_move()
        return
    wm = bpy.context.window_manager
    win = _moving_window(wm) if wm is not None else None
    if win is None or win.scene != scene:
        _end_move()
        return
    if win.as_pointer() not in _trackers:
        ensure_trackers_later()
    area, region = _pick_view(win)
    if area is None:
        return

    _state["busy"] = True
    _state["active"] = True
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            _snap(bpy.context, settings, depsgraph)
    except Exception as exc:
        print(f"GuideBro: object snapping failed: {exc}")
    finally:
        _state["busy"] = False


@persistent
def _on_load_post(_dummy):
    _trackers.clear()           # file loading ends every modal operator
    _state.update(active=False, local={})
    ensure_trackers_later()


def clear_feedback_if_idle():
    """Called from the draw callback: drop the snap markers once the move ends."""
    if _fx.get("owner") == 'OBJSNAP' and not move_in_progress():
        _end_move()
        _state["active"] = False
        fx_reset()


classes = (GUIDEBRO_OT_view_tracker,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    ensure_trackers_later()


def unregister():
    if bpy.app.timers.is_registered(_start_trackers):
        bpy.app.timers.unregister(_start_trackers)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _trackers.clear()           # the modals see obj_snap gone / get cancelled
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
