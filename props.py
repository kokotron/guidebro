# ------------------------------------------------------------
#  Property groups
# ------------------------------------------------------------

import math

import bpy
from mathutils import Vector

from .common import get_pixel_dimensions, guide_update_callback, norm_to_pixel, pixel_to_norm
from .screen import (
    assign_group, get_current_group, group_index,
    guide_angle_abs, guide_base_angle, guide_pivot_uv, set_guide_pivot_uv,
)
from .world import angles_to_direction, direction_to_angles


class GuideBroScreenGuide(bpy.types.PropertyGroup):
    """A 2D guide (see screen.py for how it is stored)."""

    name: bpy.props.StringProperty(
        name="Name",
        default="Guide",
        update=guide_update_callback,
    )

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[('H', "Horizontal", ""), ('V', "Vertical", "")],
        default='H',
        update=guide_update_callback,
    )

    # Stored as 0..1 normalised within either the camera frame or viewport region.
    position: bpy.props.FloatProperty(
        name="Position",
        default=0.5,
        min=0.0,
        max=1.0,
        precision=4,
        update=guide_update_callback,
    )

    color: bpy.props.FloatVectorProperty(
        name="Color",
        description="Guide color. Opacity is controlled globally in Appearance",
        subtype='COLOR_GAMMA',
        size=3,
        default=(0.0, 0.6, 1.0),
        min=0.0, max=1.0,
        update=guide_update_callback,
    )

    visible: bpy.props.BoolProperty(
        name="Visible",
        default=True,
        update=guide_update_callback,
    )

    # -- derived px fields ------------------------------------

    def _get_px_h(self):
        _, height = get_pixel_dimensions(bpy.context)
        return norm_to_pixel(self.position, height)

    def _set_px_h(self, value):
        _, height = get_pixel_dimensions(bpy.context)
        self.position = pixel_to_norm(value, height)

    def _get_px_v(self):
        width, _ = get_pixel_dimensions(bpy.context)
        return norm_to_pixel(self.position, width)

    def _set_px_v(self, value):
        width, _ = get_pixel_dimensions(bpy.context)
        self.position = pixel_to_norm(value, width)

    def _get_pct(self):
        return self.position * 100.0

    def _set_pct(self, value):
        self.position = max(0.0, min(1.0, value / 100.0))

    position_pct: bpy.props.FloatProperty(
        name="Position",
        description="Guide position as a percentage of the frame (0 = left/bottom)",
        subtype='PERCENTAGE',
        min=0.0, max=100.0,
        precision=1,
        get=_get_pct, set=_set_pct,
    )

    offset: bpy.props.FloatProperty(
        name="Pivot Along Line",
        description="Where the pivot sits along the guide's base direction (X for horizontal, Y for vertical)",
        default=0.5, min=0.0, max=1.0, precision=4,
        update=guide_update_callback,
    )

    angle: bpy.props.FloatProperty(
        name="Tilt",
        description="Rotation about the pivot, added to the horizontal/vertical base direction",
        subtype='ANGLE', unit='ROTATION',
        default=0.0,
        update=guide_update_callback,
    )

    length: bpy.props.FloatProperty(
        name="Point B Distance",
        description="Where Point B (the second handle) sits along the guide, from the pivot "
                    "(Point A), in frame heights. Negative = the other side",
        default=0.2,
        update=guide_update_callback,
    )

    def _get_angle_abs(self):
        return guide_angle_abs(self)

    def _set_angle_abs(self, value):
        self.angle = value - guide_base_angle(self)

    angle_abs: bpy.props.FloatProperty(
        name="Angle",
        description="Direction of the guide: 0 is horizontal, 90 is vertical",
        subtype='ANGLE', unit='ROTATION',
        min=-math.pi, max=math.pi,
        get=_get_angle_abs, set=_set_angle_abs,
    )

    def _get_pivot_x(self):
        return guide_pivot_uv(self)[0] * 100.0

    def _set_pivot_x(self, value):
        _u, v = guide_pivot_uv(self)
        set_guide_pivot_uv(self, value / 100.0, v)

    def _get_pivot_y(self):
        return guide_pivot_uv(self)[1] * 100.0

    def _set_pivot_y(self, value):
        u, _v = guide_pivot_uv(self)
        set_guide_pivot_uv(self, u, value / 100.0)

    pivot_x: bpy.props.FloatProperty(
        name="Pivot X", subtype='PERCENTAGE', min=0.0, max=100.0, precision=1,
        get=_get_pivot_x, set=_set_pivot_x,
    )

    pivot_y: bpy.props.FloatProperty(
        name="Pivot Y", subtype='PERCENTAGE', min=0.0, max=100.0, precision=1,
        get=_get_pivot_y, set=_set_pivot_y,
    )

    px_h: bpy.props.IntProperty(
        name="px (Y)",
        get=_get_px_h, set=_set_px_h,
        min=0,
    )

    px_v: bpy.props.IntProperty(
        name="px (X)",
        get=_get_px_v, set=_set_px_v,
        min=0,
    )


class GuideBroGroup(bpy.types.PropertyGroup):
    """A named set of 2D guides in the scene's library. Cameras and the
    viewport point at a group by its uid."""

    name: bpy.props.StringProperty(
        name="Group Name",
        default="Guide Group",
        update=guide_update_callback,
    )
    uid: bpy.props.IntProperty(default=0)
    guides: bpy.props.CollectionProperty(type=GuideBroScreenGuide)
    active_index: bpy.props.IntProperty(default=0)


class GuideBroWorldGuide(bpy.types.PropertyGroup):
    """A 3D guide: an infinite line in world space (see world.py)."""

    name: bpy.props.StringProperty(
        name="Name",
        default="3D Guide",
        update=guide_update_callback,
    )

    point: bpy.props.FloatVectorProperty(
        name="Point A",
        description="The first point the guide passes through (world space)",
        size=3, subtype='TRANSLATION', unit='LENGTH',
        default=(0.0, 0.0, 0.0),
        update=guide_update_callback,
    )

    def _get_direction(self):
        return self.get("_direction", (1.0, 0.0, 0.0))

    def _set_direction(self, value):
        v = Vector(value)
        if v.length < 1e-9:
            return                       # keep the old direction
        v.normalize()
        self["_direction"] = (v.x, v.y, v.z)
        heading, _pitch = direction_to_angles(v)
        if heading is not None:
            self["_heading"] = heading

    direction: bpy.props.FloatVectorProperty(
        name="Direction",
        description="Direction of the guide as a world-space vector. Stored normalised, "
                    "so the other components adjust when you change one",
        size=3, subtype='XYZ',
        default=(1.0, 0.0, 0.0),
        get=_get_direction, set=_set_direction,
        update=guide_update_callback,
    )

    # The second point (the end handle in Edit Guides) is stored as a
    # distance along the direction, so the direction fields keep working.
    length: bpy.props.FloatProperty(
        name="Length",
        description="Distance from Point A to Point B",
        default=1.0, min=1e-4,
        subtype='DISTANCE', unit='LENGTH',
        update=guide_update_callback,
    )

    def _get_point_b(self):
        return tuple(Vector(self.point) + Vector(self.direction) * self.length)

    def _set_point_b(self, value):
        v = Vector(value) - Vector(self.point)
        if v.length < 1e-6:
            return                       # B can't sit on A
        self.direction = tuple(v)
        self.length = v.length

    point_b: bpy.props.FloatVectorProperty(
        name="Point B",
        description="The second point the guide passes through (world space). "
                    "Moving it turns the guide about Point A",
        size=3, subtype='TRANSLATION', unit='LENGTH',
        get=_get_point_b, set=_set_point_b,
        update=guide_update_callback,
    )

    # Heading / pitch: the same direction as two angles.

    def _get_heading(self):
        heading, _pitch = direction_to_angles(self.direction)
        # A vertical line has no heading of its own: keep the last one, so
        # pitching back down returns to where it was.
        return self.get("_heading", 0.0) if heading is None else heading

    def _set_heading(self, value):
        _h, pitch = direction_to_angles(self.direction)
        self.direction = angles_to_direction(value, pitch)
        self["_heading"] = value

    def _get_pitch(self):
        return direction_to_angles(self.direction)[1]

    def _set_pitch(self, value):
        self.direction = angles_to_direction(self._get_heading(), value)

    heading: bpy.props.FloatProperty(
        name="Heading",
        description="Rotation of the guide about the world Z axis, measured from +X towards +Y",
        subtype='ANGLE', unit='ROTATION',
        min=-2.0 * math.pi, max=2.0 * math.pi,
        get=_get_heading, set=_set_heading,
        update=guide_update_callback,
    )

    pitch: bpy.props.FloatProperty(
        name="Pitch",
        description="Angle of the guide above the XY (ground) plane: 0 = level, 90 = straight up",
        subtype='ANGLE', unit='ROTATION',
        min=-math.pi / 2.0, max=math.pi / 2.0,
        get=_get_pitch, set=_set_pitch,
        update=guide_update_callback,
    )

    color: bpy.props.FloatVectorProperty(
        name="Color",
        description="Guide color. Opacity is controlled globally in Appearance",
        subtype='COLOR_GAMMA',
        size=3,
        default=(0.0, 0.6, 1.0),
        min=0.0, max=1.0,
        update=guide_update_callback,
    )

    visible: bpy.props.BoolProperty(
        name="Visible",
        default=True,
        update=guide_update_callback,
    )


def _obj_snap_update(self, context):
    from . import objsnap
    objsnap.ensure_trackers_later()
    guide_update_callback(self, context)


class GuideBroSettings(bpy.types.PropertyGroup):

    # -- 2D guide groups ---------------------------------------

    groups: bpy.props.CollectionProperty(type=GuideBroGroup)
    next_uid: bpy.props.IntProperty(default=1)
    viewport_group: bpy.props.IntProperty(
        name="Viewport Group",
        description="uid of the group shown in views that are not looking through a camera",
        default=0,
        update=guide_update_callback,
    )

    # The group list in the sidebar: the highlighted row is the group the
    # current view (camera / viewport) shows; clicking a row assigns it.
    def _get_group_list_index(self):
        group = get_current_group(self, bpy.context)
        return -1 if group is None else group_index(self, group)

    def _set_group_list_index(self, value):
        if 0 <= value < len(self.groups):
            assign_group(self, bpy.context, self.groups[value])

    group_list_index: bpy.props.IntProperty(
        get=_get_group_list_index, set=_set_group_list_index,
    )

    # -- appearance ---------------------------------------------

    label_show_names: bpy.props.BoolProperty(
        name="Name",
        description="Show guide names in viewport labels",
        default=True,
        update=guide_update_callback,
    )

    label_show_pixels: bpy.props.BoolProperty(
        name="Pixels",
        description="Show pixel positions in viewport labels",
        default=True,
        update=guide_update_callback,
    )

    label_show_percentage: bpy.props.BoolProperty(
        name="Percent",
        description="Show percentage positions in viewport labels",
        default=True,
        update=guide_update_callback,
    )

    label_show_angle: bpy.props.BoolProperty(
        name="Angle",
        description="Show the angle of rotated guides in viewport labels",
        default=True,
        update=guide_update_callback,
    )

    label_size: bpy.props.IntProperty(
        name="Label Size",
        description="Text size of the guide labels in the viewport",
        default=11, min=6, max=48,
        subtype='PIXEL',
        update=guide_update_callback,
    )

    camera_default_color: bpy.props.FloatVectorProperty(
        name="Camera Guide Color",
        subtype='COLOR_GAMMA',
        size=3,
        default=(0.0, 0.6, 1.0),
        min=0.0, max=1.0,
        update=guide_update_callback,
    )

    viewport_default_color: bpy.props.FloatVectorProperty(
        name="Viewport Guide Color",
        subtype='COLOR_GAMMA',
        size=3,
        default=(1.0, 0.55, 0.0),
        min=0.0, max=1.0,
        update=guide_update_callback,
    )

    guide_opacity: bpy.props.FloatProperty(
        name="Opacity",
        description="Universal opacity for all guide lines and labels",
        default=0.85,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=guide_update_callback,
    )

    show_rulers: bpy.props.BoolProperty(
        name="Rulers",
        description="Show rulers (render pixels) along the top and left of the camera view. "
                    "Drag out of a ruler to make a guide: from the top for a horizontal one, "
                    "from the left for a vertical one",
        default=True,
        update=_obj_snap_update,           # (re)starts the listener that catches ruler drags
    )

    guides_hidden: bpy.props.BoolProperty(
        name="Hide All Guides",
        description="Temporarily hide every guide, 2D and 3D, in every view",
        default=False,
        update=guide_update_callback,
    )

    # -- snapping: dragging guides (Edit Guides) ------------------
    # A toggle that is ON applies to every drag; its shortcut applies it to
    # one drag only. Several can be on: the nearest target wins.

    snap_grid: bpy.props.BoolProperty(
        name="Grid",
        description="Snap guides to a grid (positions) or to angle steps (rotation) while dragging.\n"
                    "Shortcut: hold Ctrl while dragging",
        default=False,
    )

    snap_origin: bpy.props.BoolProperty(
        name="Origin",
        description="Snap guides to object origins while dragging.\n"
                    "Shortcut: hold Ctrl+Shift while dragging",
        default=False,
    )

    snap_bounds: bpy.props.BoolProperty(
        name="Bounds",
        description="Snap guides to the on-screen bounds of objects (the box an object covers in the "
                    "view: its edges, centre lines and corners) while dragging.\n"
                    "Shortcut: hold Ctrl+Alt while dragging",
        default=False,
    )

    snap_vertex: bpy.props.BoolProperty(
        name="Vertex",
        description="Snap guides to visible mesh vertices while dragging.\n"
                    "Shortcut: hold Ctrl+Alt+Shift while dragging",
        default=False,
    )

    snap_radius: bpy.props.IntProperty(
        name="Snap Distance",
        description="How close (in screen pixels) a target must be to snap. "
                    "Used for guides and for objects",
        default=14, min=2, max=60, subtype='PIXEL',
    )

    grid_step: bpy.props.FloatProperty(
        name="Grid Step",
        description="Spacing of the frame grid, as a percentage of the frame (of its shorter "
                    "side with Square Cells). Used for guides and for objects",
        default=5.0, min=0.1, max=50.0, precision=1, subtype='PERCENTAGE',
        update=guide_update_callback,
    )

    grid_square: bpy.props.BoolProperty(
        name="Square Cells",
        description="Make the frame grid's cells square (1:1), sized from the frame's shorter "
                    "side and centred on the frame. Off: the grid divides width and height "
                    "by the same percentage, so cells take the frame's shape",
        default=False,
        update=guide_update_callback,
    )

    grid_show: bpy.props.BoolProperty(
        name="Show Grid",
        description="Draw the frame grid in the view (camera frame, or the whole viewport)",
        default=False,
        update=guide_update_callback,
    )

    grid_color: bpy.props.FloatVectorProperty(
        name="Grid Color",
        description="Color and opacity of the drawn frame grid",
        subtype='COLOR_GAMMA',
        size=4,
        default=(1.0, 1.0, 1.0, 0.18),
        min=0.0, max=1.0,
        update=guide_update_callback,
    )

    angle_step: bpy.props.FloatProperty(
        name="Angle Step",
        description="Angle increment when rotating a guide with grid snapping",
        default=math.radians(15.0), min=math.radians(1.0), max=math.radians(90.0),
        subtype='ANGLE', unit='ROTATION',
    )

    grid_step_3d: bpy.props.FloatProperty(
        name="3D Grid Step",
        description="Grid spacing for grid-snapping 3D guides (scene units)",
        default=1.0, min=0.0001, soft_max=100.0,
        subtype='DISTANCE', unit='LENGTH',
    )

    # -- snapping: moving objects (Blender's own Move / G) --------------

    obj_snap: bpy.props.BoolProperty(
        name="Snap Objects to Guides",
        description="While you move objects with Blender's own Move (G, the Move tool or "
                    "its gizmo), they snap to nearby visible guides, 2D and 3D. "
                    "In Edit Mode (with Vertex on), the vertices you move snap",
        default=False,
        update=_obj_snap_update,
    )

    obj_snap_origin: bpy.props.BoolProperty(
        name="Origin",
        description="The origins of the moving objects snap to guides",
        default=True,
    )

    obj_snap_bounds: bpy.props.BoolProperty(
        name="Bounds",
        description="The bounds of the moving selection snap to guides: its on-screen box "
                    "(edges, centre lines, corners) for 2D guides, its 3D box for 3D guides",
        default=False,
    )

    obj_snap_vertex: bpy.props.BoolProperty(
        name="Vertex",
        description="Any vertex of the moving meshes snaps to guides. "
                    "In Edit Mode: the selected vertices you move snap to guides",
        default=False,
    )

    obj_snap_grid: bpy.props.BoolProperty(
        name="Grid",
        description="Also snap to the frame grid (Grid Step), as if every grid line were a 2D guide",
        default=False,
    )

    # -- 3D (world-space) guides. They belong to the scene, not to a view. --

    guides_3d: bpy.props.CollectionProperty(type=GuideBroWorldGuide)
    guides_3d_active_index: bpy.props.IntProperty(default=0, update=guide_update_callback)


classes = (
    GuideBroScreenGuide,
    GuideBroGroup,
    GuideBroWorldGuide,
    GuideBroSettings,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.guidebro = bpy.props.PointerProperty(type=GuideBroSettings)
    # Which library group a camera shows. On the object, so it is copied
    # when the camera is duplicated.
    bpy.types.Object.guidebro_group = bpy.props.IntProperty(
        name="GuideBro Group",
        description="uid of the 2D guide group this camera shows",
        default=0,
        update=guide_update_callback,
    )


def unregister():
    del bpy.types.Object.guidebro_group
    del bpy.types.Scene.guidebro
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
