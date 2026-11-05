"""Screenshot of the GuideBro sidebar. blender --factory-startup --python test_ui.py -- <src dir> <out dir>"""
import sys, os, math
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy
import guidebro
from guidebro import screen
import guidebro.ui

guidebro.ui.GuideBroPanel.bl_category = "Item"   # screenshot only: Item is the open tab
guidebro.register()


def setup():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    space = area.spaces.active
    space.show_region_ui = True
    space.region_3d.view_perspective = 'CAMERA'
    scene = bpy.context.scene
    s = scene.guidebro
    cam = scene.camera
    cam2 = cam.copy(); cam2.name = "Camera Close"; scene.collection.objects.link(cam2)
    g = screen.new_group(s, "Thirds")
    cam.guidebro_group = g.uid
    cam2.guidebro_group = g.uid          # shared, like a duplicated camera
    for name, axis, pos in (("Left third", 'V', 1 / 3), ("Right third", 'V', 2 / 3),
                            ("Top third", 'H', 2 / 3), ("Bottom third", 'H', 1 / 3)):
        gd = g.guides.add(); gd.name = name; gd.axis = axis; gd.position = pos
    screen.new_group(s, "Golden ratio")
    g3 = s.guides_3d.add(); g3.name = "Horizon tilt"; g3.direction = (1, 0, 0)
    g3.heading = math.radians(30); g3.pitch = math.radians(10)
    s.obj_snap = True
    ui = next(r for r in area.regions if r.type == 'UI')
    try:
        ui.active_panel_category = "GuideBro"
    except Exception as exc:
        print("category:", exc)
    for r in area.regions:
        r.tag_redraw()
    return None


def shoot():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "sidebar.png"))
    ui = next(r for r in area.regions if r.type == 'UI')
    with bpy.context.temp_override(window=win, area=area, region=ui):
        for _i in range(40):
            bpy.ops.view2d.scroll_down()
    bpy.app.timers.register(shoot2, first_interval=0.5)


def shoot2():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "sidebar2.png"))
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(setup, first_interval=1.5)
bpy.app.timers.register(shoot, first_interval=4.0)
