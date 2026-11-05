"""v0.5.2: Point B handle, frame grid (square / shown). GUI + event simulation."""
import sys, os, json, math, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
import guidebro
from guidebro import screen, world
from guidebro.common import get_camera_frame_bounds, _edit

guidebro.register()
log = []
def rec(**kw):
    log.append(kw); print("STEP", kw)

def view():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region, area.spaces.active.region_3d

def to_win(x, y):
    win, area, region, rv3d = view()
    return int(round(region.x + x)), int(round(region.y + y))

def ev(t, v, x, y, **kw):
    bpy.context.window_manager.windows[0].event_simulate(t, v, x=x, y=y, **kw)

def scr(p):
    win, area, region, rv3d = view()
    v = view3d_utils.location_3d_to_region_2d(region, rv3d, p)
    return (v.x, v.y)

def drag(a, b, steps=8, **mods):
    ev('MOUSEMOVE', 'NOTHING', *to_win(*a))
    ev('LEFTMOUSE', 'PRESS', *to_win(*a))
    for i in range(1, steps + 1):
        ev('MOUSEMOVE', 'NOTHING', *to_win(a[0] + (b[0] - a[0]) * i / steps,
                                         a[1] + (b[1] - a[1]) * i / steps), **mods)

def steps():
    win, area, region, rv3d = view()
    s = bpy.context.scene.guidebro
    cx, cy = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx, cy); ev('ESC', 'PRESS', cx, cy); ev('ESC', 'RELEASE', cx, cy)
    yield 0.5

    # ---- unit checks -------------------------------------------------
    g = s.guides_3d.add(); g.point = (1, 2, 3); g.direction = (1, 0, 0); g.length = 2
    rec(step="point_b derived", b=tuple(round(c, 5) for c in g.point_b))
    g.point_b = (1, 2, 7)
    rec(step="point_b set", dir=tuple(round(c, 5) for c in g.direction), length=round(g.length, 5),
        a=tuple(g.point))
    g.heading = math.radians(90)
    rec(step="heading keeps A & length", b=tuple(round(c, 4) for c in g.point_b))
    s.guides_3d.remove(0)
    s.grid_step = 10.0
    s.grid_square = False
    xs, ys = screen.frame_grid(s, (0, 0, 1920, 1080))
    rec(step="grid percent", nx=len(xs), ny=len(ys), dx=xs[1] - xs[0], dy=ys[1] - ys[0])
    s.grid_square = True
    xs, ys = screen.frame_grid(s, (0, 0, 1920, 1080))
    rec(step="grid square", nx=len(xs), ny=len(ys), dx=xs[1] - xs[0], dy=ys[1] - ys[0],
        centred=(960.0 in [round(x, 6) for x in xs], 540.0 in [round(y, 6) for y in ys]))

    # ---- Point B handle: drag it onto a cube corner (vertex snapping) --
    rv3d.view_perspective = 'PERSP'
    yield 0.3
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.guidebro.guide3d_add_axis(axis='X')
    g = s.guides_3d[-1]
    g.point = (0.0, -2.5, 1.0)                       # in front of the cube, above it
    a0 = Vector(g.point)
    rec(step="axis guide length (view based)", length=round(g.length, 3))
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.guidebro.edit_mode('INVOKE_DEFAULT')
    yield 0.3
    corner = bpy.data.objects["Cube"].matrix_world @ Vector((1, -1, 1))
    pb = scr(g.point_b); pc = scr(corner)
    s.snap_vertex = True
    drag(pb, (pc[0] + 4, pc[1] - 3)); yield 0.3
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "pointB_drag.png"))
    rec(step="dragging B", hover=(_edit["drag_kind"], _edit["drag_space"]))
    ev('LEFTMOUSE', 'RELEASE', *to_win(pc[0] + 4, pc[1] - 3)); yield 0.3
    g = s.guides_3d[-1]
    rec(step="B on corner", b_err=(Vector(g.point_b) - corner).length,
        a_moved=(Vector(g.point) - a0).length)

    # drag A somewhere free: B must stay on the corner
    pa = scr(g.point)
    s.snap_vertex = False
    drag(pa, (pa[0] - 60, pa[1] + 40)); ev('LEFTMOUSE', 'RELEASE', *to_win(pa[0] - 60, pa[1] + 40)); yield 0.3
    g = s.guides_3d[-1]
    rec(step="A moved, B fixed", b_err=(Vector(g.point_b) - corner).length,
        a_moved=round((Vector(g.point) - a0).length, 3))

    # drag the line body: both points move together, length kept
    b_before, len_before = Vector(g.point_b), g.length
    mid = scr((Vector(g.point) + Vector(g.point_b)) / 2)
    drag(mid, (mid[0], mid[1] + 50)); ev('LEFTMOUSE', 'RELEASE', *to_win(mid[0], mid[1] + 50)); yield 0.3
    g = s.guides_3d[-1]
    rec(step="line drag", b_moved=round((Vector(g.point_b) - b_before).length, 3),
        length_same=abs(g.length - len_before) < 1e-5)

    # ---- frame grid in camera view: show + square + drag a guide with Ctrl
    rv3d.view_perspective = 'CAMERA'
    grp = screen.new_group(s, "G"); bpy.context.scene.camera.guidebro_group = grp.uid
    v = grp.guides.add(); v.axis = 'V'; v.position = 0.5
    s.grid_show = True; s.grid_square = True; s.grid_step = 10.0
    yield 0.4
    with bpy.context.temp_override(window=win, area=area, region=region):
        b = get_camera_frame_bounds(bpy.context)
    xs, ys = screen.frame_grid(s, b)
    gx = b[0] + 0.5 * (b[2] - b[0])
    y_mid = (b[1] + b[3]) / 2 + 37
    drag((gx, y_mid), (gx + 1.37 * (xs[1] - xs[0]), y_mid), ctrl=True); yield 0.2
    ev('LEFTMOUSE', 'RELEASE', *to_win(gx + 1.37 * (xs[1] - xs[0]), y_mid)); yield 0.3
    px = b[0] + v.position * (b[2] - b[0])
    rec(step="ctrl drag to square grid", err=min(abs(px - x) for x in xs),
        cell_px=(xs[1] - xs[0], ys[1] - ys[0]))
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "grid_square.png"))
    s.grid_square = False
    yield 0.3
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "grid_percent.png"))

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            rec(EXC=traceback.format_exc())
        with open(os.path.join(OUT, "v052.json"), "w") as f:
            json.dump(log, f, indent=1, default=str)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(tick, first_interval=2.0)

run()
