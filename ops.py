# ------------------------------------------------------------
#  Operators: guide groups, 2D guides, 3D guides
# ------------------------------------------------------------

import bpy
from mathutils import Vector

from .common import (
    _edit, get_current_bounds, get_region_3d, get_settings, get_view_region,
    is_camera_view, screen_to_plane, tag_redraw_3d_views,
)
from .screen import (
    assign_group, copy_guide, default_group_name, ensure_group, find_group,
    get_current_group, get_default_color, group_users, guide_end_px, guide_segment,
    make_unique_guide_name, new_group,
)
from .world import AXIS_COLORS, AXIS_VECTORS, handle_length, set_guide_points


# ------------------------------------------------------------
#  Groups
# ------------------------------------------------------------

class GUIDEBRO_OT_group_add(bpy.types.Operator):
    """Create a new, empty guide group and show it in this camera (or the viewport)"""
    bl_idname = "guidebro.group_add"
    bl_label = "New Guide Group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        if is_camera_view(context) and context.scene.camera is None:
            self.report({'WARNING'}, "No active camera in this scene")
            return {'CANCELLED'}
        group = new_group(settings, default_group_name(context))
        assign_group(settings, context, group)
        return {'FINISHED'}


class GUIDEBRO_OT_group_duplicate(bpy.types.Operator):
    """Copy this group and show the copy here, so it can be changed without
    affecting the other cameras that use the original"""
    bl_idname = "guidebro.group_duplicate"
    bl_label = "Duplicate Guide Group"
    bl_options = {'REGISTER', 'UNDO'}

    uid: bpy.props.IntProperty()

    def execute(self, context):
        settings = get_settings(context)
        src = find_group(settings, self.uid)
        if src is None:
            return {'CANCELLED'}
        dst = new_group(settings, f"{src.name} Copy")
        src = find_group(settings, self.uid)       # adding can move the collection
        for g in src.guides:
            copy_guide(dst.guides.add(), g)
        dst.active_index = min(src.active_index, max(0, len(dst.guides) - 1))
        assign_group(settings, context, dst)
        return {'FINISHED'}


class GUIDEBRO_OT_group_unlink(bpy.types.Operator):
    """Stop showing this group here. The group stays in the list for other cameras"""
    bl_idname = "guidebro.group_unlink"
    bl_label = "Unlink Guide Group"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not assign_group(get_settings(context), context, None):
            return {'CANCELLED'}
        return {'FINISHED'}


class GUIDEBRO_OT_group_remove(bpy.types.Operator):
    """Delete this guide group and its guides, from every camera that uses it"""
    bl_idname = "guidebro.group_remove"
    bl_label = "Delete Guide Group"
    bl_options = {'REGISTER', 'UNDO'}

    uid: bpy.props.IntProperty()

    def invoke(self, context, event):
        settings = get_settings(context)
        group = find_group(settings, self.uid)
        if group is None:
            return {'CANCELLED'}
        users = group_users(settings, context.scene, group)
        if len(users) > 1 or (users and len(group.guides)):
            return context.window_manager.invoke_confirm(
                self, event, title="Delete Guide Group",
                message=f"\"{group.name}\" is used by: {', '.join(users)}",
                confirm_text="Delete")
        return self.execute(context)

    def execute(self, context):
        settings = get_settings(context)
        for i, g in enumerate(settings.groups):
            if g.uid == self.uid:
                settings.groups.remove(i)
                break
        else:
            return {'CANCELLED'}
        tag_redraw_3d_views()
        return {'FINISHED'}


# ------------------------------------------------------------
#  2D guides
# ------------------------------------------------------------

class GUIDEBRO_OT_add(bpy.types.Operator):
    bl_idname = "guidebro.add"
    bl_label = "Add Guide"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        items=[('H', "Horizontal", ""), ('V', "Vertical", "")],
        default='H',
    )

    @classmethod
    def description(cls, context, properties):
        axis = "horizontal" if properties.axis == 'H' else "vertical"
        return (f"Add a {axis} guide at the centre of the frame and switch on Edit Guides, "
                f"so you can drag it into place (hold Ctrl to snap)")

    def execute(self, context):
        settings = get_settings(context)
        group = ensure_group(settings, context)
        if group is None:
            self.report({'WARNING'}, "No active camera in this scene")
            return {'CANCELLED'}

        guides = group.guides
        g = guides.add()
        g.name = make_unique_guide_name(guides, self.axis)
        g.axis = self.axis
        g.position = 0.5
        g.offset = 0.5
        g.angle = 0.0
        g.color = get_default_color(settings, context)
        group.active_index = len(guides) - 1
        tag_redraw_3d_views()

        # Dragging is how guides get placed, so make sure it is possible.
        if not _edit["active"] and context.area is not None and context.area.type == 'VIEW_3D':
            try:
                bpy.ops.guidebro.edit_mode('INVOKE_DEFAULT')
            except RuntimeError:
                pass
        return {'FINISHED'}


class _ActiveGroupOp:
    """Shared bits of the operators that work on one guide of the current group."""
    index: bpy.props.IntProperty()

    def _guides(self, context):
        group = get_current_group(get_settings(context), context)
        return group, (group.guides if group is not None else None)


class GUIDEBRO_OT_remove(_ActiveGroupOp, bpy.types.Operator):
    bl_idname = "guidebro.remove"
    bl_label = "Remove Guide"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        group, guides = self._guides(context)
        if guides is None or not (0 <= self.index < len(guides)):
            return {'CANCELLED'}
        guides.remove(self.index)
        group.active_index = max(0, min(self.index, len(guides) - 1))
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_clear(bpy.types.Operator):
    bl_idname = "guidebro.clear"
    bl_label = "Clear Group"
    bl_description = "Delete every guide in the current group"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        group = get_current_group(get_settings(context), context)
        if group is None:
            return {'CANCELLED'}
        group.guides.clear()
        group.active_index = 0
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_add_mirror(_ActiveGroupOp, bpy.types.Operator):
    """Add a mirrored counterpart at (1 - position)"""
    bl_idname = "guidebro.add_mirror"
    bl_label = "Mirror Guide"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        group, guides = self._guides(context)
        if guides is None or not (0 <= self.index < len(guides)):
            return {'CANCELLED'}
        g = guides.add()
        src = guides[self.index]
        copy_guide(g, src)
        g.name = f"{src.name} Mirror"
        g.position = 1.0 - src.position
        g.angle = -src.angle
        group.active_index = len(guides) - 1
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_duplicate(_ActiveGroupOp, bpy.types.Operator):
    bl_idname = "guidebro.duplicate"
    bl_label = "Duplicate Guide"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        group, guides = self._guides(context)
        if guides is None or not (0 <= self.index < len(guides)):
            return {'CANCELLED'}
        g = guides.add()
        src = guides[self.index]
        copy_guide(g, src)
        g.name = f"{src.name} Copy"
        g.position = min(src.position + 0.05, 1.0)
        group.active_index = len(guides) - 1
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_set_active_camera(bpy.types.Operator):
    """Set the scene's active camera (which also picks the guide group shown)"""
    bl_idname = "guidebro.set_active_camera"
    bl_label = "Set Active Camera"
    bl_options = {'REGISTER', 'UNDO'}

    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.camera_name)
        if obj is None or obj.type != 'CAMERA':
            self.report({'WARNING'}, f"Camera \"{self.camera_name}\" not found")
            return {'CANCELLED'}
        context.scene.camera = obj
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_toggle_hide_all(bpy.types.Operator):
    """Hide or show every guide, 2D and 3D, in every view"""
    bl_idname = "guidebro.toggle_hide_all"
    bl_label = "Toggle All Guides Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        settings.guides_hidden = not settings.guides_hidden
        tag_redraw_3d_views()
        return {'FINISHED'}


# ------------------------------------------------------------
#  3D guides
# ------------------------------------------------------------

def add_3d_guide(settings, name, point, direction, color, length=1.0):
    g = settings.guides_3d.add()
    g.name = name
    g.point = tuple(point)
    g.direction = tuple(direction)
    g.length = length
    g.color = tuple(color)
    g.visible = True
    settings.guides_3d_active_index = len(settings.guides_3d) - 1
    return g


class GUIDEBRO_OT_convert_to_3d(bpy.types.Operator):
    """Turn the guide into a 3D guide: a line in world space that stays put when you orbit.

    The line is placed on a plane that faces the current view and passes
    through the chosen reference point, so in this view it looks exactly the same"""
    bl_idname = "guidebro.convert_to_3d"
    bl_label = "Convert to 3D Guide"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(
        name="Convert",
        items=[('ACTIVE', "Active Guide", "Convert only the active guide"),
               ('GROUP', "Whole Group", "Convert every visible guide of the current group")],
        default='ACTIVE',
    )
    depth: bpy.props.EnumProperty(
        name="Depth",
        description="How far from the viewer the new guide sits: it lies on a plane facing "
                    "the view that passes through this point",
        items=[('CURSOR', "3D Cursor", "Through the 3D cursor"),
               ('ACTIVE_OBJECT', "Active Object", "Through the active object's origin"),
               ('ORIGIN', "World Origin", "Through the world origin")],
        default='CURSOR',
    )
    keep_original: bpy.props.BoolProperty(
        name="Keep Original",
        description="Keep the 2D guide as well as the new 3D guide",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        settings = get_settings(context)
        region, rv3d = get_view_region(context), get_region_3d(context)
        bounds = get_current_bounds(context)
        group = get_current_group(settings, context)
        if region is None or rv3d is None or bounds is None or group is None or not group.guides:
            self.report({'WARNING'}, "There is no guide to convert")
            return {'CANCELLED'}

        if self.depth == 'ACTIVE_OBJECT' and context.active_object is not None:
            ref = context.active_object.matrix_world.translation.copy()
        elif self.depth == 'ORIGIN':
            ref = Vector((0.0, 0.0, 0.0))
        else:
            ref = context.scene.cursor.location.copy()
            if self.depth == 'ACTIVE_OBJECT':
                self.report({'INFO'}, "No active object: using the 3D cursor")

        if self.scope == 'GROUP':
            picked = [i for i, g in enumerate(group.guides) if g.visible]
        else:
            picked = [max(0, min(group.active_index, len(group.guides) - 1))]

        converted = []
        for i in picked:
            g = group.guides[i]
            seg = guide_segment(g, bounds)
            if seg is None:
                continue
            P, _d, _n, A, B = seg
            Wp = screen_to_plane(region, rv3d, P, ref)
            Wa = screen_to_plane(region, rv3d, A, ref)
            Wb = screen_to_plane(region, rv3d, B, ref)
            if Wp is None or Wa is None or Wb is None or (Wb - Wa).length < 1e-9:
                continue
            d3 = (Wb - Wa).normalized()
            g3 = add_3d_guide(settings, g.name, Wp, d3, g.color, handle_length(context, Wp, d3))
            We = screen_to_plane(region, rv3d, guide_end_px(g, bounds), ref)
            if We is not None and (We - Wp).length > 1e-6:
                set_guide_points(g3, Wp, We)       # Point B carries over too
            converted.append(i)

        if not converted:
            self.report({'WARNING'}, "Nothing could be converted (guide is outside the view?)")
            return {'CANCELLED'}

        if not self.keep_original:
            for i in sorted(converted, reverse=True):
                group.guides.remove(i)
            group.active_index = max(0, min(group.active_index, len(group.guides) - 1))

        self.report({'INFO'}, f"Converted {len(converted)} guide(s) to 3D")
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_guide3d_add_axis(bpy.types.Operator):
    bl_idname = "guidebro.guide3d_add_axis"
    bl_label = "Add Axis Guide"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
        default='X',
    )

    @classmethod
    def description(cls, context, properties):
        return f"Add a 3D guide along the world {properties.axis} axis, through the 3D cursor"

    def execute(self, context):
        settings = get_settings(context)
        cursor = context.scene.cursor.location
        add_3d_guide(settings, f"{self.axis} Axis", cursor, AXIS_VECTORS[self.axis],
                     AXIS_COLORS[self.axis], handle_length(context, cursor, AXIS_VECTORS[self.axis]))
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_guide3d_set_axis(bpy.types.Operator):
    bl_idname = "guidebro.guide3d_set_axis"
    bl_label = "Align 3D Guide to Axis"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
        default='X',
    )

    @classmethod
    def description(cls, context, properties):
        return f"Point the active 3D guide along the world {properties.axis} axis (it keeps its point)"

    def execute(self, context):
        settings = get_settings(context)
        guides = settings.guides_3d
        if not guides:
            return {'CANCELLED'}
        g = guides[max(0, min(settings.guides_3d_active_index, len(guides) - 1))]
        g.direction = AXIS_VECTORS[self.axis]
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_guide3d_remove(bpy.types.Operator):
    bl_idname = "guidebro.guide3d_remove"
    bl_label = "Remove 3D Guide"
    bl_description = "Delete this 3D guide"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        settings = get_settings(context)
        guides = settings.guides_3d
        if not (0 <= self.index < len(guides)):
            return {'CANCELLED'}
        guides.remove(self.index)
        settings.guides_3d_active_index = max(0, min(self.index, len(guides) - 1))
        tag_redraw_3d_views()
        return {'FINISHED'}


class GUIDEBRO_OT_guide3d_duplicate(bpy.types.Operator):
    bl_idname = "guidebro.guide3d_duplicate"
    bl_label = "Duplicate 3D Guide"
    bl_description = "Duplicate this 3D guide (then drag it into place)"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        settings = get_settings(context)
        guides = settings.guides_3d
        if not (0 <= self.index < len(guides)):
            return {'CANCELLED'}
        src = guides[self.index]
        add_3d_guide(settings, f"{src.name} Copy", tuple(src.point), tuple(src.direction),
                     tuple(src.color), src.length)
        tag_redraw_3d_views()
        return {'FINISHED'}


classes = (
    GUIDEBRO_OT_group_add,
    GUIDEBRO_OT_group_duplicate,
    GUIDEBRO_OT_group_unlink,
    GUIDEBRO_OT_group_remove,
    GUIDEBRO_OT_add,
    GUIDEBRO_OT_remove,
    GUIDEBRO_OT_clear,
    GUIDEBRO_OT_add_mirror,
    GUIDEBRO_OT_duplicate,
    GUIDEBRO_OT_set_active_camera,
    GUIDEBRO_OT_toggle_hide_all,
    GUIDEBRO_OT_convert_to_3d,
    GUIDEBRO_OT_guide3d_add_axis,
    GUIDEBRO_OT_guide3d_set_axis,
    GUIDEBRO_OT_guide3d_remove,
    GUIDEBRO_OT_guide3d_duplicate,
)
