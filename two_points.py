# ------------------------------------------------------------
#  Guide through two points
# ------------------------------------------------------------
#
#  Click a first point, then a second: the guide runs through both. Each
#  click lands on, in order of preference:
#    1. a snap target (vertex / origin / bounds, per the Snapping toggles or
#       the held Ctrl shortcut) - so a guide can join any two vertices
#    2. the surface under the pointer
#    3. a plane facing the view: through the first point for the second
#       click, through the 3D cursor for the first
#  Grid snapping (toggle or Ctrl) rounds a free point to the 3D grid.

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from .common import (
    MODIFIER_KEYS, _fx, clip_segment_to_rect, fx_reset, get_current_bounds, get_region_3d,
    get_settings, get_view_region, is_camera_view, mouse_over_viewport,
    region_mouse_coords, screen_to_plane, tag_redraw_3d_views,
)
from .ops import add_3d_guide
from .screen import (
    ensure_group, get_current_group, get_current_guides, get_default_color,
    set_guide_points_px, unique_name,
)
from .snapping import POINT_KINDS, GeoCache, active_snap_kinds, best_point_snap, snap_world_point
from .world import (
    line3_screen, project_point, set_guide_points, snap_to_world_grid, view_projection,
)


HINT = ("{step}   |   snaps to: Snapping toggles, or hold Ctrl: grid  +Shift: origin  "
        "+Alt: bounds  +Alt+Shift: vertex   |   Esc / RMB: cancel")


class GUIDEBRO_OT_two_points(bpy.types.Operator):
    """Click two points: a guide is placed through both"""
    bl_idname = "guidebro.two_points"
    bl_label = "Guide Through Two Points"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        items=[('NEW', "New Guide", "Create a new guide through the two points"),
               ('ACTIVE', "Active Guide", "Move the active guide so it runs through the two points")],
        default='NEW',
        options={'SKIP_SAVE'},
    )
    space: bpy.props.EnumProperty(
        items=[('SCREEN', "2D Guide", "A guide drawn on the view, through the two points as seen"),
               ('WORLD', "3D Guide", "A line in world space through the two 3D points")],
        default='WORLD',
        options={'SKIP_SAVE'},
    )

    _cache = None
    _first = None        # (world point, label) of the first click
    _hover = None        # (world point, label, screen point) under the pointer
    _cursor_set = False

    @classmethod
    def description(cls, context, properties):
        kind = "3D guide" if properties.space == 'WORLD' else "guide"
        what = "the active" if properties.target == 'ACTIVE' else "a new"
        return (f"Click two points: {what} {kind} runs through both. Points snap to vertices, "
                f"origins or bounds (Snapping toggles or Ctrl shortcuts), else land on the "
                f"surface under the pointer")

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    # -- lifecycle ---------------------------------------------

    def invoke(self, context, event):
        settings = get_settings(context)
        if self.space == 'SCREEN' and is_camera_view(context) and context.scene.camera is None:
            self.report({'WARNING'}, "No active camera in this scene")
            return {'CANCELLED'}
        if self.target == 'ACTIVE':
            guides = settings.guides_3d if self.space == 'WORLD' else get_current_guides(settings, context)
            if not guides:
                self.report({'WARNING'}, "There is no active guide to place")
                return {'CANCELLED'}

        self._cache = GeoCache(context)
        self._first = None
        self._hover = None
        context.window.cursor_modal_set('CROSSHAIR')
        self._cursor_set = True
        fx_reset(context.area.as_pointer())
        self._status(context)
        context.window_manager.modal_handler_add(self)
        tag_redraw_3d_views()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self._cleanup(context)
            return {'CANCELLED'}

        in_view = mouse_over_viewport(context, event)

        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or event.type in MODIFIER_KEYS:
            self._update(context, event, in_view)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and in_view:
            self._update(context, event, in_view)
            if self._hover is None:
                return {'RUNNING_MODAL'}
            if self._first is None:
                self._first = (self._hover[0].copy(), self._hover[1])
                self._cache = GeoCache(context)          # the view may have changed
                self._status(context)
                self._update(context, event, in_view)
                return {'RUNNING_MODAL'}
            ok = self._apply(context, self._first[0], self._hover[0])
            if not ok:
                return {'RUNNING_MODAL'}                 # same point twice: keep picking
            self._cleanup(context)
            return {'FINISHED'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._cleanup(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}   # orbit / pan / zoom keep working between clicks

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._cursor_set:
            context.window.cursor_modal_restore()
            self._cursor_set = False
        context.workspace.status_text_set(None)
        fx_reset()
        self._cache = None
        tag_redraw_3d_views()

    def _status(self, context):
        step = "Click the FIRST point" if self._first is None else "Click the SECOND point"
        context.workspace.status_text_set(HINT.format(step=step))

    # -- picking -----------------------------------------------

    def _pick(self, context, event, mx, my):
        """World point under the pointer -> (point, label, screen point) or None."""
        settings = get_settings(context)
        region, rv3d = get_view_region(context), get_region_3d(context)
        kinds = active_snap_kinds(settings, event)

        if kinds & set(POINT_KINDS):
            hit = best_point_snap(self._cache, context, kinds, mx, my, settings.snap_radius)
            if hit is not None:
                return snap_world_point(context, hit), hit["label"], hit["pt"]

        ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mx, my))
        rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mx, my))
        hit = context.scene.ray_cast(context.evaluated_depsgraph_get(), ro, rd)
        if hit[0]:
            point, label = hit[1].copy(), f"{hit[4].name} surface"
        else:
            ref = self._first[0] if self._first is not None else context.scene.cursor.location
            point = screen_to_plane(region, rv3d, (mx, my), ref)
            if point is None:
                return None
            label = "in view plane"
        if 'GRID' in kinds:
            point = snap_to_world_grid(point, settings.grid_step_3d)
            label = f"grid {settings.grid_step_3d:g}"
        proj = view_projection(context)
        scr = project_point(proj, point) or (mx, my)
        return point, label, scr

    def _update(self, context, event, in_view):
        fx_reset(context.area.as_pointer())
        self._hover = None
        if in_view:
            mx, my = region_mouse_coords(context, event)
            try:
                self._hover = self._pick(context, event, mx, my)
            except Exception as exc:
                self.report({'WARNING'}, f"Point search failed: {exc}")
        proj = view_projection(context)

        if self._first is not None:
            a = project_point(proj, self._first[0])
            if a is not None:
                _fx["pts"].append(a)
        if self._hover is not None:
            _fx["pts"].append(self._hover[2])
            q = self._hover[2]
            _fx["label"] = (q[0] + 10, q[1] + 10,
                            ("2nd: " if self._first is not None else "1st: ") + self._hover[1])

        # Preview of the guide: the full line through both points.
        if self._first is not None and self._hover is not None:
            A, B = self._first[0], self._hover[0]
            bounds = get_current_bounds(context)
            if (B - A).length > 1e-9 and bounds is not None:
                if self.space == 'WORLD':
                    info = line3_screen(proj, tuple(A), tuple((B - A).normalized()))
                    seg = None if info is None else clip_segment_to_rect(info["A"], info["B"], bounds)
                else:
                    a, b = project_point(proj, A), project_point(proj, B)
                    seg = None
                    if a is not None and b is not None and math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
                        far = 1e5
                        d = ((b[0] - a[0]), (b[1] - a[1]))
                        L = math.hypot(*d)
                        d = (d[0] / L, d[1] / L)
                        seg = clip_segment_to_rect((a[0] - d[0] * far, a[1] - d[1] * far),
                                                   (a[0] + d[0] * far, a[1] + d[1] * far), bounds)
                if seg is not None:
                    _fx["thin"].append(seg)
                a = project_point(proj, A)
                if a is not None:
                    _fx["thick"].append((a, self._hover[2]))
        context.area.tag_redraw()

    # -- result ------------------------------------------------

    def _apply(self, context, A, B):
        settings = get_settings(context)
        if (B - A).length < 1e-6:
            self.report({'WARNING'}, "Pick a second point that is not the first one")
            return False
        if self.space == 'WORLD':
            if self.target == 'ACTIVE':
                guides = settings.guides_3d
                g = guides[max(0, min(settings.guides_3d_active_index, len(guides) - 1))]
            else:
                name = unique_name((g.name for g in settings.guides_3d), "Guide")
                g = add_3d_guide(settings, name, A, (B - A).normalized(),
                                 get_default_color(settings, context))
            set_guide_points(g, A, B)          # the clicks become Point A and Point B
            self.report({'INFO'}, "3D guide placed through two points")
            return True

        bounds = get_current_bounds(context)
        proj = view_projection(context)
        a, b = project_point(proj, A), project_point(proj, B)
        if bounds is None or a is None or b is None or math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
            self.report({'WARNING'}, "The two points coincide on screen")
            return False
        if self.target == 'ACTIVE':
            group = get_current_group(settings, context)
            guide = group.guides[max(0, min(group.active_index, len(group.guides) - 1))]
        else:
            group = ensure_group(settings, context)
            if group is None:
                self.report({'WARNING'}, "No active camera in this scene")
                return False
            name = unique_name((g.name for g in group.guides), "Guide")
            guide = group.guides.add()
            guide.name = name
            guide.color = get_default_color(settings, context)
            group.active_index = len(group.guides) - 1
        set_guide_points_px(guide, bounds, a, b)      # the clicks become Point A and Point B
        self.report({'INFO'}, "Guide placed through two points")
        return True


classes = (GUIDEBRO_OT_two_points,)
