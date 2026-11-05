"""Run with:  blender --factory-startup --enable-event-simulate --python test_gui.py -- <dir with guidebro> <out dir>"""
import sys, os, json, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy
from bpy_extras import view3d_utils
import guidebro
from guidebro import screen, objsnap
from guidebro.common import get_camera_frame_bounds

log = []
def rec(**kw):
    log.append(kw)
    print("STEP", kw)

guidebro.register()

def view():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            region = [r for r in area.regions if r.type == 'WINDOW'][0]
            return win, area, region, area.spaces.active.region_3d
    raise RuntimeError("no 3d view")

def cube_xy():
    win, area, region, rv3d = view()
    p = view3d_utils.location_3d_to_region_2d(region, rv3d, bpy.data.objects["Cube"].matrix_world.translation)
    return (p.x, p.y)

def bounds():
    win, area, region, rv3d = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        return get_camera_frame_bounds(bpy.context)

def modal_ops():
    win = bpy.context.window_manager.windows[0]
    return [op.bl_idname for op in win.modal_operators]

def to_win(x, y):
    win, area, region, rv3d = view()
    return int(region.x + x), int(region.y + y)

def ev(type, value, x=None, y=None, **kw):
    win = bpy.context.window_manager.windows[0]
    if x is None:
        win.event_simulate(type, value, **kw)
    else:
        win.event_simulate(type, value, x=x, y=y, **kw)

def start_move():
    win, area, region, rv3d = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.transform.translate('INVOKE_DEFAULT')

def shot(name):
    win, area, region, rv3d = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, name))

def steps():
    win, area, region, rv3d = view()
    scene = bpy.context.scene
    s = scene.guidebro
    space = area.spaces.active
    rv3d.view_perspective = 'CAMERA'
    space.show_region_ui = True
    cam = scene.camera
    g = screen.new_group(s, "Cam Guides")
    cam.guidebro_group = g.uid
    v = g.guides.add(); v.name = "Thirds V"; v.axis = 'V'; v.position = 0.7
    h = g.guides.add(); h.name = "Thirds H"; h.axis = 'H'; h.position = 0.3
    s.obj_snap = True                       # starts the view tracker via a timer
    cx0, cy0 = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx0, cy0); ev('ESC', 'PRESS', cx0, cy0); ev('ESC', 'RELEASE', cx0, cy0)  # splash
    cube = bpy.data.objects["Cube"]
    for o in scene.objects:
        o.select_set(o == cube)
    bpy.context.view_layer.objects.active = cube
    yield 1.0

    rec(step="trackers", trackers=len(objsnap._trackers))
    b = bounds()
    gx = b[0] + 0.7 * (b[2] - b[0])
    gy = b[1] + 0.3 * (b[3] - b[1])
    c0 = cube_xy()
    rec(step="setup", bounds=b, guide_x=gx, guide_y=gy, cube=c0)
    loc0 = tuple(cube.location)

    # pointer onto the cube, press G
    x, y = to_win(*c0)
    ev('MOUSEMOVE', 'NOTHING', x, y); yield 0.2
    start_move(); yield 0.3
    rec(step="after G", modal=modal_ops())

    # walk towards the guide: unsnapped cube would end 5 px left of it
    tx = gx - 5
    for i in range(1, 11):
        px = c0[0] + (tx - c0[0]) * i / 10.0
        ev('MOUSEMOVE', 'NOTHING', *to_win(px, c0[1])); yield 0.05
    yield 0.3
    cx, cy = cube_xy()
    rec(step="near guide (5px)", cube_x=cx, guide_x=gx, err=cx - gx, modal=modal_ops())

    # far from the guide: must move freely
    tx2 = gx + 40
    ev('MOUSEMOVE', 'NOTHING', *to_win(tx2, c0[1])); yield 0.4
    cx, cy = cube_xy()
    rec(step="far (40px)", cube_x=cx, expected_free=tx2, err=cx - tx2)

    # near the crossing of both guides: should land on the crossing
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx + 6, gy - 7)); yield 0.4
    cx, cy = cube_xy()
    rec(step="near crossing", err_x=cx - gx, err_y=cy - gy)

    # confirm
    mx, my = to_win(gx + 6, gy - 7)
    ev('LEFTMOUSE', 'PRESS', mx, my); ev('LEFTMOUSE', 'RELEASE', mx, my); yield 0.5
    cx, cy = cube_xy()
    rec(step="confirmed", err_x=cx - gx, err_y=cy - gy, modal=modal_ops(),
        fx_owner=guidebro.common._fx.get("owner"))
    shot("after_move.png")

    # undo should return the cube to where it started
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.ed.undo()
    yield 0.5
    # undo replaces all data: re-fetch every reference
    scene = bpy.context.scene; s = scene.guidebro
    win, area, region, rv3d = view()
    cube = bpy.data.objects["Cube"]
    rec(step="undo", loc=tuple(cube.location), loc0=loc0)
    rec(step="state after undo", obj_snap=s.obj_snap, groups=len(s.groups))
    g = screen.new_group(s, "Cam Guides"); scene.camera.guidebro_group = g.uid
    v = g.guides.add(); v.name = "Thirds V"; v.axis = 'V'; v.position = 0.7
    h = g.guides.add(); h.name = "Thirds H"; h.axis = 'H'; h.position = 0.3
    s.obj_snap = True
    yield 0.5

    # cancel (RMB) must restore, not snap
    cube = bpy.data.objects["Cube"]
    c0 = cube_xy()
    x, y = to_win(*c0)
    ev('MOUSEMOVE', 'NOTHING', x, y); yield 0.2
    start_move(); yield 0.2
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx - 4, c0[1])); yield 0.3
    rec(step="before cancel", err=cube_xy()[0] - gx, modal=modal_ops())
    ev('ESC', 'PRESS', *to_win(gx - 4, c0[1])); ev('ESC', 'RELEASE', *to_win(gx - 4, c0[1])); yield 0.5
    rec(step="cancelled", loc=tuple(bpy.data.objects["Cube"].location), loc0=loc0)

    # 3D guide: a Z-axis guide through (2, 0, 0); move cube near it
    g3 = s.guides_3d.add(); g3.name = "Z line"; g3.point = (2.0, 0.0, 0.0); g3.direction = (0, 0, 1)
    yield 0.3
    p = view3d_utils.location_3d_to_region_2d(region, rv3d, g3.point)
    c0 = cube_xy()
    x, y = to_win(*c0)
    ev('MOUSEMOVE', 'NOTHING', x, y); yield 0.2
    start_move(); yield 0.2
    from guidebro.world import line3_screen, view_projection
    with bpy.context.temp_override(window=win, area=area, region=region):
        info = line3_screen(view_projection(bpy.context), (2, 0, 0), (0, 0, 1))
    A, d = info["A"], info["d"]
    t = (c0[1] - A[1]) / d[1]
    lx = A[0] + d[0] * t                   # the line's screen x at the cube's height
    b = bounds(); gx2 = b[0] + 0.7 * (b[2] - b[0])
    rec(step="3D aim", line_x=lx, guide2d_x=gx2, cube=c0)
    ev('MOUSEMOVE', 'NOTHING', *to_win(lx - 6, c0[1])); yield 0.4
    loc = bpy.data.objects["Cube"].matrix_world.translation
    rec(step="3D guide", loc=tuple(loc), modal=modal_ops(), note="x,y should be 2,0 (on the Z line)")
    ev('ESC', 'PRESS', *to_win(p.x + 6, c0[1])); ev('ESC', 'RELEASE', *to_win(p.x + 6, c0[1])); yield 0.4

    # labels while zoomed past the camera frame
    s.label_size = 16
    rv3d.view_camera_zoom = 30
    rv3d.view_camera_offset = (0.2, 0.15)
    yield 0.5
    shot("zoomed_labels.png")
    rv3d.view_camera_zoom = 0
    rv3d.view_camera_offset = (0, 0)
    yield 0.3
    shot("normal_labels.png")
    yield 0.2

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            rec(step="EXCEPTION", tb=traceback.format_exc())
        with open(os.path.join(OUT, "gui_log.json"), "w") as f:
            json.dump(log, f, indent=1, default=str)
        bpy.ops.wm.quit_blender()
        return None
    bpy.app.timers.register(tick, first_interval=2.0)

run()
