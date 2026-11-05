# ------------------------------------------------------------
#  Sidebar UI: GuideBro tab
# ------------------------------------------------------------
#
#  GuideBro            Edit Guides / Hide, what this view is
#  ├─ 2D Guides        camera, group library, guides of the group shown here
#  ├─ 3D Guides        world-space guides (the same in every view)
#  ├─ Snapping         guides snapping to things, objects snapping to guides
#  └─ Appearance

import math

import bpy

from .common import _edit, get_pixel_dimensions, get_settings, is_camera_view
from .screen import (
    get_current_group, get_default_color_attr, grid_cell, group_users, guide_angle_abs,
    guide_is_tilted, guide_tag,
)
from . import objsnap


# ------------------------------------------------------------
#  Menus
# ------------------------------------------------------------

class GUIDEBRO_MT_camera_select(bpy.types.Menu):
    bl_label = "Select Active Camera"
    bl_idname = "GUIDEBRO_MT_camera_select"

    def draw(self, context):
        layout = self.layout
        active = context.scene.camera
        cameras = [obj for obj in context.scene.objects if obj.type == 'CAMERA']

        if not cameras:
            layout.label(text="No cameras in this scene", icon='INFO')
        else:
            for cam in sorted(cameras, key=lambda o: o.name.lower()):
                icon = 'CHECKMARK' if cam == active else 'BLANK1'
                op = layout.operator("guidebro.set_active_camera", text=cam.name, icon=icon)
                op.camera_name = cam.name

        layout.separator()
        layout.operator("object.camera_add", text="New Camera", icon='ADD')


# ------------------------------------------------------------
#  UI lists
# ------------------------------------------------------------

class GUIDEBRO_UL_groups(bpy.types.UIList):
    """The scene's guide groups. The highlighted one is shown in this view."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='GROUP')

        users = group_users(data, context.scene, item)
        info = row.row(align=True)
        info.alignment = 'RIGHT'
        info.active = False
        if len(users) > 1:
            info.label(text=str(len(users)), icon='LINKED')
        info.label(text=str(len(item.guides)))

    def draw_filter(self, context, layout):
        pass


class GUIDEBRO_UL_guides(bpy.types.UIList):
    """One compact line per guide:  H/V tag | name | position or angle | colour | visibility."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()

        tag = row.row()
        tag.ui_units_x = 1.0
        tag.active = item.visible
        tag.label(text=guide_tag(item))

        name = row.row()
        name.active = item.visible
        name.prop(item, "name", text="", emboss=False)

        value = row.row()
        value.ui_units_x = 3.2
        value.alignment = 'RIGHT'
        value.active = False
        if guide_is_tilted(item):
            value.label(text=f"{math.degrees(guide_angle_abs(item)):.0f}°")
        else:
            value.label(text=f"{item.px_h if item.axis == 'H' else item.px_v} px")

        swatch = row.row()
        swatch.ui_units_x = 1.8
        swatch.prop(item, "color", text="")

        row.prop(item, "visible", text="",
                 icon='HIDE_OFF' if item.visible else 'HIDE_ON', emboss=False)

    def draw_filter(self, context, layout):
        pass


class GUIDEBRO_UL_guides3d(bpy.types.UIList):
    """3D guides:  3D tag | name | colour | visibility."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()

        tag = row.row()
        tag.ui_units_x = 1.2
        tag.active = item.visible
        tag.label(text="3D")

        name = row.row()
        name.active = item.visible
        name.prop(item, "name", text="", emboss=False)

        swatch = row.row()
        swatch.ui_units_x = 1.8
        swatch.prop(item, "color", text="")

        row.prop(item, "visible", text="",
                 icon='HIDE_OFF' if item.visible else 'HIDE_ON', emboss=False)

    def draw_filter(self, context, layout):
        pass


# ------------------------------------------------------------
#  Panels
# ------------------------------------------------------------

class GuideBroPanel:
    """Shared panel plumbing: 3D View sidebar + error-safe draw."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GuideBro"

    def draw(self, context):
        try:
            self._draw_inner(context)
        except Exception as e:
            self.layout.label(text=f"Error: {e}", icon='ERROR')


class GuideBroSubPanel(GuideBroPanel):
    bl_parent_id = "GUIDEBRO_PT_main"


class GUIDEBRO_PT_main(GuideBroPanel, bpy.types.Panel):
    bl_label = "GuideBro"
    bl_idname = "GUIDEBRO_PT_main"

    def _draw_inner(self, context):
        layout = self.layout
        settings = get_settings(context)
        edit_on = _edit["active"]

        row = layout.row(align=True)
        row.scale_y = 1.15
        row.operator("guidebro.edit_mode", text="Edit Guides", icon='VIEW_PAN', depress=edit_on)
        row.operator(
            "guidebro.toggle_hide_all",
            text="Show Guides" if settings.guides_hidden else "Hide Guides",
            icon='HIDE_ON' if settings.guides_hidden else 'HIDE_OFF',
            depress=settings.guides_hidden,
        )

        dim_x, dim_y = get_pixel_dimensions(context)
        info = layout.row()
        info.active = False
        if is_camera_view(context):
            info.label(text=f"Camera view  ·  {dim_x} × {dim_y} px", icon='VIEW_CAMERA')
        else:
            info.label(text=f"Viewport  ·  {dim_x} × {dim_y} px", icon='VIEW3D')

        if edit_on:
            hint = layout.column(align=True)
            hint.active = False
            hint.label(text="Drag a line to move it.")
            hint.label(text="Snap: toggles in Snapping, or hold Ctrl.")


class GUIDEBRO_PT_guides2d(GuideBroSubPanel, bpy.types.Panel):
    bl_label = "2D Guides"
    bl_idname = "GUIDEBRO_PT_guides2d"

    def draw_header(self, context):
        group = get_current_group(get_settings(context), context)
        if group is not None and len(group.guides):
            self.layout.operator("guidebro.clear", text="", icon='TRASH', emboss=False)

    def _draw_inner(self, context):
        layout = self.layout
        settings = get_settings(context)
        in_cam = is_camera_view(context)
        camera = context.scene.camera

        # -- Where these guides are shown -----------------------
        layout.menu(
            "GUIDEBRO_MT_camera_select",
            text=camera.name if camera else "No Active Camera",
            icon='OUTLINER_OB_CAMERA',
        )
        if in_cam and camera is None:
            layout.label(text="No active camera in this scene.", icon='INFO')
            return

        # -- Group library ----------------------------------------
        group = get_current_group(settings, context)
        col = layout.column(align=True)
        head = col.row()
        head.active = False
        head.label(text=f"Group shown in this {'camera' if in_cam else 'viewport'}:")

        row = col.row()
        row.template_list("GUIDEBRO_UL_groups", "", settings, "groups",
                          settings, "group_list_index", rows=3, maxrows=6)
        side = row.column(align=True)
        side.operator("guidebro.group_add", text="", icon='ADD')
        sub = side.column(align=True)
        sub.enabled = group is not None
        uid = group.uid if group is not None else 0
        sub.operator("guidebro.group_duplicate", text="", icon='DUPLICATE').uid = uid
        sub.operator("guidebro.group_remove", text="", icon='REMOVE').uid = uid
        sub.separator()
        sub.operator("guidebro.group_unlink", text="", icon='X')

        if group is not None:
            here = camera.name if in_cam else "Viewport"
            others = [u for u in group_users(settings, context.scene, group) if u != here]
            if others:
                note = layout.row()
                note.active = False
                note.label(text=f"Also shown in: {', '.join(others)}", icon='LINKED')

        # -- Add ----------------------------------------------------
        layout.separator()
        row = layout.row(align=True)
        row.operator("guidebro.add", text="Horizontal", icon='ADD').axis = 'H'
        row.operator("guidebro.add", text="Vertical", icon='ADD').axis = 'V'
        row = layout.row(align=True)
        op = row.operator("guidebro.edge_guide", text="From Edge", icon='EDGESEL')
        op.target, op.space = 'NEW', 'SCREEN'
        op = row.operator("guidebro.two_points", text="Two Points", icon='IPO_LINEAR')
        op.target, op.space = 'NEW', 'SCREEN'

        # -- List -----------------------------------------------------
        guides = group.guides if group is not None else None
        if not guides:
            empty = layout.row()
            empty.active = False
            empty.label(text="No guides here yet." if group is None else "This group has no guides yet.")
            return

        layout.template_list(
            "GUIDEBRO_UL_guides", "", group, "guides", group, "active_index",
            rows=max(2, min(len(guides), 6)), maxrows=6,
        )

        # -- Active guide ---------------------------------------------
        index = max(0, min(group.active_index, len(guides) - 1))
        guide = guides[index]

        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        if guide_is_tilted(guide):
            col.prop(guide, "pivot_x", text="Pivot X", slider=True)
            col.prop(guide, "pivot_y", text="Pivot Y", slider=True)
        else:
            col.prop(guide, "position_pct", text="Position", slider=True)
            col.prop(guide, "px_h" if guide.axis == 'H' else "px_v", text="Pixels")
        col.prop(guide, "angle_abs", text="Angle")

        row = layout.row(align=True)
        op = row.operator("guidebro.edge_guide", text="Align to Edge", icon='EDGESEL')
        op.target, op.space = 'ACTIVE', 'SCREEN'
        op = row.operator("guidebro.two_points", text="To 2 Points", icon='IPO_LINEAR')
        op.target, op.space = 'ACTIVE', 'SCREEN'
        layout.operator("guidebro.convert_to_3d", text="Convert to 3D Guide", icon='VIEW_PERSPECTIVE')

        row = layout.row(align=True)
        row.operator("guidebro.add_mirror", text="Mirror", icon='MOD_MIRROR').index = index
        row.operator("guidebro.duplicate", text="Duplicate", icon='DUPLICATE').index = index
        row.operator("guidebro.remove", text="Delete", icon='TRASH').index = index


class GUIDEBRO_PT_guides3d(GuideBroSubPanel, bpy.types.Panel):
    bl_label = "3D Guides"
    bl_idname = "GUIDEBRO_PT_guides3d"

    def _draw_inner(self, context):
        layout = self.layout
        settings = get_settings(context)

        row = layout.row(align=True)
        for axis in ('X', 'Y', 'Z'):
            row.operator("guidebro.guide3d_add_axis", text=f"{axis} Axis", icon='ADD').axis = axis
        row = layout.row(align=True)
        op = row.operator("guidebro.edge_guide", text="From Edge", icon='EDGESEL')
        op.target, op.space = 'NEW', 'WORLD'
        op = row.operator("guidebro.two_points", text="Two Points", icon='IPO_LINEAR')
        op.target, op.space = 'NEW', 'WORLD'

        guides = settings.guides_3d
        if len(guides) == 0:
            hint = layout.column(align=True)
            hint.active = False
            hint.label(text="No 3D guides yet. Add an axis, click two")
            hint.label(text="points or an edge, or convert a 2D guide.")
            return

        index = max(0, min(settings.guides_3d_active_index, len(guides) - 1))
        row = layout.row()
        row.template_list(
            "GUIDEBRO_UL_guides3d", "", settings, "guides_3d", settings, "guides_3d_active_index",
            rows=max(2, min(len(guides), 5)), maxrows=5,
        )
        col = row.column(align=True)
        col.operator("guidebro.guide3d_duplicate", text="", icon='DUPLICATE').index = index
        col.operator("guidebro.guide3d_remove", text="", icon='REMOVE').index = index

        guide = guides[index]
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(guide, "point", text="Point A")
        col.prop(guide, "point_b", text="Point B")
        col.separator()
        col.prop(guide, "direction", text="Direction")
        col.separator()
        col.prop(guide, "heading", text="Heading")
        col.prop(guide, "pitch", text="Pitch")

        row = col.row(align=True)
        row.label(text="Align to")
        for axis in ('X', 'Y', 'Z'):
            row.operator("guidebro.guide3d_set_axis", text=axis).axis = axis

        row = layout.row(align=True)
        op = row.operator("guidebro.edge_guide", text="Align to Edge", icon='EDGESEL')
        op.target, op.space = 'ACTIVE', 'WORLD'
        op = row.operator("guidebro.two_points", text="To 2 Points", icon='IPO_LINEAR')
        op.target, op.space = 'ACTIVE', 'WORLD'


class GUIDEBRO_PT_snapping(GuideBroSubPanel, bpy.types.Panel):
    bl_label = "Snapping"
    bl_idname = "GUIDEBRO_PT_snapping"

    def _draw_inner(self, context):
        layout = self.layout
        settings = get_settings(context)

        # -- Guides snap to things (Edit Guides) ----------------------
        layout.label(text="Guides snap to:")
        row = layout.row(align=True)        # hover a toggle to see its shortcut
        row.prop(settings, "snap_grid", toggle=True)
        row.prop(settings, "snap_origin", toggle=True)
        row.prop(settings, "snap_bounds", toggle=True)
        row.prop(settings, "snap_vertex", toggle=True)

        # -- Objects snap to guides (Blender's own Move) --------------
        layout.separator()
        layout.prop(settings, "obj_snap", text="Objects snap to guides")
        body = layout.column(align=True)
        body.active = settings.obj_snap
        row = body.row(align=True)
        row.prop(settings, "obj_snap_grid", toggle=True)
        row.prop(settings, "obj_snap_origin", toggle=True)
        row.prop(settings, "obj_snap_bounds", toggle=True)
        row.prop(settings, "obj_snap_vertex", toggle=True)
        if settings.obj_snap:
            hint = body.column(align=True)
            hint.active = False
            if objsnap.supported():
                hint.label(text="While moving with G / Move tool")
            else:
                hint.label(text="Needs a newer Blender (Window.modal_operators).", icon='ERROR')

        # -- Shared settings ------------------------------------------
        layout.separator()
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "snap_radius", text="Distance")
        col.prop(settings, "angle_step", text="Angle Step")
        col.prop(settings, "grid_step_3d", text="3D Grid Step")

        # -- Frame grid (2D) ------------------------------------------
        layout.separator()
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "grid_step", text="Frame Grid")
        sub = col.column(heading=" ", align=True)
        sub.prop(settings, "grid_square", text="Square Cells")
        sub.prop(settings, "grid_show", text="Show Grid")
        dim_x, dim_y = get_pixel_dimensions(context)
        if dim_x and dim_y:
            cw, ch = grid_cell(settings, dim_x, dim_y)
            info = col.row()
            info.active = False
            info.alignment = 'RIGHT'
            info.label(text=f"Cell {cw:.4g} \u00d7 {ch:.4g} px")


class GUIDEBRO_PT_appearance(GuideBroSubPanel, bpy.types.Panel):
    bl_label = "Appearance"
    bl_idname = "GUIDEBRO_PT_appearance"
    bl_options = {'DEFAULT_CLOSED'}

    def _draw_inner(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = get_settings(context)

        col = layout.column()
        col.prop(settings, get_default_color_attr(context), text="New Guide Color")
        col.prop(settings, "guide_opacity", text="Opacity", slider=True)
        col.prop(settings, "grid_color", text="Grid Color")

        col = layout.column(heading="Labels", align=True)
        col.prop(settings, "label_show_names", text="Name")
        col.prop(settings, "label_show_pixels", text="Pixels")
        col.prop(settings, "label_show_percentage", text="Percent")
        col.prop(settings, "label_show_angle", text="Angle")
        layout.prop(settings, "label_size", text="Label Size")


classes = (
    GUIDEBRO_MT_camera_select,
    GUIDEBRO_UL_groups,
    GUIDEBRO_UL_guides,
    GUIDEBRO_UL_guides3d,
    # Sub-panels are listed after their parent, in the order they appear.
    GUIDEBRO_PT_main,
    GUIDEBRO_PT_guides2d,
    GUIDEBRO_PT_guides3d,
    GUIDEBRO_PT_snapping,
    GUIDEBRO_PT_appearance,
)
