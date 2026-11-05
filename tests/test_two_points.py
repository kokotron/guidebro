"""Two-point guide: click near two cube corners (vertex snapping on)."""
import sys, os, json, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
import guidebro

guidebro.register()
log = []

def view():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region, area.spaces.active.region_3d

def to_win(x, y):
    win, area, region, rv3d = view()
    return int(region.x + x), int(region.y + y)

def ev(t, v, x, y, **kw):
    bpy.context.window_manager.windows[0].event_simulate(t, v, x=x, y=y, **kw)

def scr(p):
    win, area, region, rv3d = view()
    return view3d_utils.location_3d_to_region_2d(region, rv3d, p)

def click(x, y):
    ev('LEFTMOUSE', 'PRESS', x, y); ev('LEFTMOUSE', 'RELEASE', x, y)

def start(space, target='NEW'):
    win, area, region, rv3d = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.guidebro.two_points('INVOKE_DEFAULT', space=space, target=target)

def steps():
    win, area, region, rv3d = view()
    s = bpy.context.scene.guidebro
    cx, cy = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx, cy); ev('ESC', 'PRESS', cx, cy); ev('ESC', 'RELEASE', cx, cy)
    s.snap_vertex = True
    yield 1.0
    cube = bpy.data.objects["Cube"]
    va = cube.matrix_world @ Vector((1, 1, 1))      # two visible corners
    vb = cube.matrix_world @ Vector((1, -1, -1))
    pa, pb = scr(va), scr(vb)
    log.append({"corners_screen": [tuple(pa), tuple(pb)]})

    # 3D guide through the two corners, clicking 5 px off each corner
    ev('MOUSEMOVE', 'NOTHING', *to_win(pa.x + 4, pa.y - 3)); yield 0.2
    start('WORLD'); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(pa.x + 4, pa.y - 3)); yield 0.2
    click(*to_win(pa.x + 4, pa.y - 3)); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(pb.x - 3, pb.y + 4)); yield 0.3
    win.screen  # keep alive
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "two_points_preview.png"))
    click(*to_win(pb.x - 3, pb.y + 4)); yield 0.4
    g = s.guides_3d[-1] if len(s.guides_3d) else None
    if g is not None:
        p, d = Vector(g.point), Vector(g.direction)
        want = (vb - va).normalized()
        log.append({"3d_point_err": (p - va).length, "3d_dir_dot": abs(d.dot(want)),
                    "name": g.name, "count": len(s.guides_3d)})
    else:
        log.append({"3d": "no guide made"})

    # surface point (snapping off): first click on the cube face, second in empty space
    s.snap_vertex = False
    face = scr(cube.matrix_world @ Vector((1, 0, 0)))     # centre of the +X face
    ev('MOUSEMOVE', 'NOTHING', *to_win(face.x, face.y)); yield 0.2
    start('WORLD'); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(face.x, face.y)); yield 0.2
    click(*to_win(face.x, face.y)); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(face.x + 150, face.y + 90)); yield 0.2
    click(*to_win(face.x + 150, face.y + 90)); yield 0.4
    g = s.guides_3d[-1]
    log.append({"surface_point": tuple(round(c, 4) for c in g.point), "expect_x": 1.0,
                "count": len(s.guides_3d)})

    # 2D guide through two corners (as seen on screen)
    s.snap_vertex = True
    start('SCREEN'); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(pa.x + 4, pa.y - 3)); yield 0.2
    click(*to_win(pa.x + 4, pa.y - 3)); yield 0.3
    ev('MOUSEMOVE', 'NOTHING', *to_win(pb.x - 3, pb.y + 4)); yield 0.2
    click(*to_win(pb.x - 3, pb.y + 4)); yield 0.4
    from guidebro.screen import get_current_group, guide_segment
    from guidebro.common import get_current_bounds
    with bpy.context.temp_override(window=win, area=area, region=region):
        grp = get_current_group(s, bpy.context)
        b = get_current_bounds(bpy.context)
        seg = guide_segment(grp.guides[-1], b)
    P, dd, n = seg[0], seg[1], seg[2]
    da = (pa.x - P[0]) * n[0] + (pa.y - P[1]) * n[1]
    db = (pb.x - P[0]) * n[0] + (pb.y - P[1]) * n[1]
    log.append({"2d_dist_to_corner_a": da, "2d_dist_to_corner_b": db})

    # Esc mid-way leaves nothing behind
    n3 = len(s.guides_3d)
    start('WORLD'); yield 0.3
    click(*to_win(pa.x, pa.y)); yield 0.2
    ev('ESC', 'PRESS', *to_win(pa.x, pa.y)); ev('ESC', 'RELEASE', *to_win(pa.x, pa.y)); yield 0.3
    log.append({"cancel_added": len(s.guides_3d) - n3,
                "modal": [o.bl_idname for o in win.modal_operators]})

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            log.append({"EXC": traceback.format_exc()})
        with open(os.path.join(OUT, "two_points.json"), "w") as f:
            json.dump(log, f, indent=1, default=str)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(tick, first_interval=2.0)

run()
