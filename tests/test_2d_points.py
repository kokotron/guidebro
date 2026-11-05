"""2D guides with Point A / Point B handles."""
import sys, os, json, math, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
import guidebro
from guidebro import screen
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

def drag(a, b, n=8, **mods):
    """generator: yield from drag(...)"""
    ev('MOUSEMOVE', 'NOTHING', *to_win(*a)); yield 0.1
    ev('LEFTMOUSE', 'PRESS', *to_win(*a)); yield 0.1
    rec(step="  drag state", drag=(_edit["drag"], _edit["drag_kind"], _edit["drag_space"]),
        modal=[o.bl_idname for o in bpy.context.window_manager.windows[0].modal_operators])
    for i in range(1, n + 1):
        ev('MOUSEMOVE', 'NOTHING', *to_win(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n), **mods)
    yield 0.1
    ev('LEFTMOUSE', 'RELEASE', *to_win(*b), **mods)

def dist_line(guide, b, p):
    P, d, n, A, B = screen.guide_segment(guide, b)
    return (p[0] - P[0]) * n[0] + (p[1] - P[1]) * n[1]

def steps():
    win, area, region, rv3d = view()
    scene = bpy.context.scene; s = scene.guidebro
    cx, cy = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx, cy); ev('ESC', 'PRESS', cx, cy); ev('ESC', 'RELEASE', cx, cy)
    yield 0.5                      # let the splash-closing Esc go by first
    rv3d.view_perspective = 'CAMERA'
    grp = screen.new_group(s, "G"); scene.camera.guidebro_group = grp.uid
    g = grp.guides.add(); g.name = "V"; g.axis = 'V'; g.position = 0.3; g.offset = 0.4
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.guidebro.edit_mode('INVOKE_DEFAULT')
    yield 0.6
    with bpy.context.temp_override(window=win, area=area, region=region):
        b = get_camera_frame_bounds(bpy.context)
    g = s.groups[0].guides[0]
    A0, B0 = screen.handle_positions(g, b)
    rec(step="handles", A=A0, B=B0, b_minus_a=(B0[0] - A0[0], B0[1] - A0[1]))

    # 1) drag B onto a cube corner (vertex snapping on)
    s.snap_vertex = True
    corner = scr(bpy.data.objects["Cube"].matrix_world @ Vector((1, -1, 1)))
    yield from drag(B0, (corner[0] + 3, corner[1] - 4)); yield 0.4
    g = s.groups[0].guides[0]
    A1, B1 = screen.handle_positions(g, b)
    rec(step="B to corner", A_moved=math.dist(A1, A0), B_err=math.dist(B1, corner),
        line_through_corner=dist_line(g, b, corner))

    # 2) drag A elsewhere: B (the corner) stays on the guide
    s.snap_vertex = False
    yield from drag(A1, (A1[0] - 120, A1[1] + 60)); yield 0.4
    g = s.groups[0].guides[0]
    A2, B2 = screen.handle_positions(g, b)
    rec(step="A moved", B_err=math.dist(B2, corner), A_moved=round(math.dist(A2, A1), 1),
        line_through_corner=dist_line(g, b, corner))

    # 3) Ctrl-drag B: angle in 15 degree steps
    yield from drag(B2, (B2[0] + 37, B2[1] + 23), ctrl=True); yield 0.4
    g = s.groups[0].guides[0]
    ang = math.degrees(screen.guide_theta(g)) % 180.0
    rec(step="ctrl B angle", angle=round(ang, 4), multiple_of_15=abs(ang / 15 - round(ang / 15)) < 1e-6)

    # 4) B outside the frame: the handle sits on the frame edge and still works
    g.length = 5.0
    yield 0.2
    A4, B4 = screen.handle_positions(g, b)
    on_edge = min(abs(B4[0] - b[0]), abs(B4[0] - b[2]), abs(B4[1] - b[1]), abs(B4[1] - b[3]))
    rec(step="B clamped", dist_to_frame_edge=round(on_edge, 3))
    yield from drag(B4, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2 + 100)); yield 0.4
    g = s.groups[0].guides[0]
    A5, B5 = screen.handle_positions(g, b)
    rec(step="clamped B dragged", A_moved=round(math.dist(A5, A4), 3),
        B_err=round(math.dist(B5, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2 + 100)), 3))
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "points_2d.png"))

    # 5) Esc during a drag restores the guide
    before = (g.axis, g.position, g.offset, g.angle, g.length)
    ev('MOUSEMOVE', 'NOTHING', *to_win(*B5)); ev('LEFTMOUSE', 'PRESS', *to_win(*B5))
    ev('MOUSEMOVE', 'NOTHING', *to_win(B5[0] + 80, B5[1] - 50)); yield 0.2
    ev('ESC', 'PRESS', *to_win(B5[0] + 80, B5[1] - 50)); ev('LEFTMOUSE', 'RELEASE', *to_win(B5[0] + 80, B5[1] - 50)); yield 0.3
    g = s.groups[0].guides[0]
    after = (g.axis, g.position, g.offset, g.angle, g.length)
    rec(step="esc restores", same=all(abs(a - b_) < 1e-6 if isinstance(a, float) else a == b_ for a, b_ in zip(before, after)))

    # 6) mirror keeps the handles mirrored
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.guidebro.add_mirror(index=0)
    m = s.groups[0].guides[1]
    Am, Bm = screen.handle_positions(m, b)
    cxf, cyf = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    if g.axis == 'V':      # V guides mirror left/right, H guides top/bottom
        err_a = abs((Am[0] - cxf) + (A5[0] - cxf)) + abs(Am[1] - A5[1])
        err_b = abs((Bm[0] - cxf) + (B5[0] - cxf)) + abs(Bm[1] - B5[1])
    else:
        err_a = abs((Am[1] - cyf) + (A5[1] - cyf)) + abs(Am[0] - A5[0])
        err_b = abs((Bm[1] - cyf) + (B5[1] - cyf)) + abs(Bm[0] - B5[0])
    rec(step="mirror", axis=g.axis, A_err=round(err_a, 3), B_err=round(err_b, 3))

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            rec(EXC=traceback.format_exc())
        with open(os.path.join(OUT, "points_2d.json"), "w") as f:
            json.dump(log, f, indent=1, default=str)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(tick, first_interval=2.0)

run()
