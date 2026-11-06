# GuideBro - Photoshop-style 2D and 3D guides for Blender's camera and viewport.
#
# ------------------------------------------------------------
#  v0.6.0
#  - Rulers in camera view (toggle under Edit Guides / Hide Guides), in
#    render pixels, with marks where the 2D guides are. Drag out of the top
#    ruler for a horizontal guide, out of the left one for a vertical guide;
#    release over a ruler to discard it.
#  - Sidebar: each section's content sits in a box, and sections start
#    closed. 3D guides: Point A / Point B as a title over one X Y Z row; the
#    Direction field is gone (Heading / Pitch / Align to remain).
#
#  v0.5.4
#  - Objects snap to guides now also works in Edit Mode: with Vertex on,
#    the selected vertices you move (G / Move tool / gizmo) snap to 2D and
#    3D guides (and the frame grid with Grid on).
#
#  v0.5.3
#  - 2D guides get the same two points: the rotate handle is now Point B,
#    drawn like Point A (the pivot). Drag either and the guide turns about
#    the other; drag the line to move both. B snaps to vertex / origin /
#    bounds; with grid snapping (Ctrl) it turns in Angle Step steps. A B
#    outside the frame is shown where the guide leaves the frame.
#  - Two Points / From Edge on 2D guides put A and B on the picked points;
#    Convert to 3D carries Point B over.
#
#  v0.5.2
#  - 3D guides have two real points, A and B. In Edit Guides both show as
#    the same kind of handle (replacing the old rotate handle): drag either
#    one and the guide turns about the other; drag the line to move both.
#    Both snap like any guide drag (vertex / origin / bounds / 3D grid).
#    Point A / Point B are also number fields in the sidebar. Guides made
#    from an edge or from Two Points keep those exact points.
#  - Frame grid: Show Grid draws it (color in Appearance), Square Cells
#    makes 1:1 cells sized from the frame's shorter side, centred on the
#    frame. The sidebar shows the cell size in pixels. The same grid is
#    used for guide dragging, object snapping and drawing.
#
#  v0.5.1
#  - "Two Points": click two points and a guide runs through both (2D or
#    3D, new or the active guide). Points snap to vertices / origins /
#    bounds / grid like guide dragging does, else land on the surface under
#    the pointer, so a 3D guide can join any two vertices.
#  - Fixed: object snapping by Bounds jumped the selection far past the
#    guide (the move was measured from the selection centre, not the edge).
#
#  v0.5.0 (first release as GuideBro, formerly "Camera Guides")
#  - Now a Blender extension, split into modules:
#      common.py     view lookup, runtime state, 2D maths
#      screen.py     2D guides and the guide-group library
#      world.py      3D guides
#      props.py      property groups
#      snapping.py   snap targets on screen
#      objsnap.py    objects snapping to guides during Blender's own Move
#      ops.py        operators
#      edit_mode.py  Edit Guides and Guide from Edge
#      two_points.py Guide through two points
#      rulers.py     Rulers in camera view (and dragging guides out of them)
#      draw.py       viewport drawing
#      ui.py         sidebar ("GuideBro" tab)
#  - 2D guide groups live in one library per scene. Each camera (and the
#    viewport) shows one of them, so a group can be reused by several
#    cameras, and a duplicated camera keeps its guides.
#  - 3D guides: direction shown and typed as numbers (X / Y / Z), and as a
#    heading + pitch angle, instead of the direction ball. Align to X/Y/Z.
#  - Appearance: Label Size.
#  - Labels stay inside the visible part of the view when you zoom in past
#    the camera frame.
#  - "Move Selected to Guides" is replaced by "Objects snap to guides" in the
#    Snapping panel: it works while you move objects normally (G, the Move
#    tool, its gizmo), by origin / bounds / vertex, plus the frame grid.
# ------------------------------------------------------------

import bpy
from bpy.app.handlers import persistent

from . import draw, edit_mode, objsnap, ops, props, two_points, ui
from .common import edit_state_reset, tag_redraw_3d_views


classes = (
    *ops.classes,
    *edit_mode.classes,
    *two_points.classes,
    *ui.classes,
)

addon_keymaps = []

# Identity object used only as a msgbus subscription owner, so we can
# reliably unsubscribe in unregister() without leaking anything.
_camera_msgbus_owner = object()


def _on_scene_camera_changed():
    """Redraw viewports so a camera switch shows that camera's guide group immediately."""
    tag_redraw_3d_views()


def _subscribe():
    bpy.msgbus.clear_by_owner(_camera_msgbus_owner)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.Scene, "camera"),
        owner=_camera_msgbus_owner,
        args=(),
        notify=_on_scene_camera_changed,
    )


@persistent
def _on_load_post(_dummy):
    """Blender kills modal operators on file load (and drops msgbus
    subscriptions); make sure our state agrees."""
    edit_state_reset()
    _subscribe()


def register():
    props.register()
    for cls in classes:
        bpy.utils.register_class(cls)
    objsnap.register()
    draw.register()

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    _subscribe()

    # Shortcuts; change or remove them in Preferences > Keymap.
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new("guidebro.toggle_hide_all", 'QUOTE', 'PRESS', ctrl=True, alt=True)
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new("guidebro.edit_mode", 'SEMI_COLON', 'PRESS', ctrl=True, alt=True)
        addon_keymaps.append((km, kmi))


def unregister():
    # Ask any running Edit Guides modal to stop itself.
    edit_state_reset()

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.msgbus.clear_by_owner(_camera_msgbus_owner)

    draw.unregister()
    objsnap.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    props.unregister()
