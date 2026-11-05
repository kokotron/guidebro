"""Edit Mode: moving selected vertices snaps them to guides (Objects snap + Vertex)."""
import sys, os, json, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
import bpy, bmesh
from bpy_extras import view3d_utils
from mathutils import Vector
import guidebro
from guidebro import screen
from guidebro.common import get_camera_frame_bounds

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

def vert_world(i):
    o = bpy.data.objects["Cube"]
    bm = bmesh.from_edit_mesh(o.data); bm.verts.ensure_lookup_table()
    return o.matrix_world @ bm.verts[i].co

def scr(p):
    win, area, region, rv3d = view()
    v = view3d_utils.location_3d_to_region_2d(region, rv3d, p)
    return (v.x, v.y)

def start_move():
    win, area, region, rv3d = view()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.transform.translate('INVOKE_DEFAULT')

def steps():
    win, area, region, rv3d = view()
    scene = bpy.context.scene; s = scene.guidebro
    cx, cy = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx, cy); ev('ESC', 'PRESS', cx, cy); ev('ESC', 'RELEASE', cx, cy)
    yield 0.5
    rv3d.view_perspective = 'CAMERA'
    grp = screen.new_group(s, "G"); scene.camera.guidebro_group = grp.uid
    v = grp.guides.add(); v.axis = 'V'; v.position = 0.7
    s.obj_snap = True; s.obj_snap_origin = True; s.obj_snap_vertex = True
    cube = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = cube; cube.select_set(True)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(cube.data); bm.verts.ensure_lookup_table()
    # pick the vertex nearest the camera-view centre to move
    idx = 0
    bm.verts[idx].select = True
    bm.select_flush_mode(); bmesh.update_edit_mesh(cube.data)
    yield 0.5
    with bpy.context.temp_override(window=win, area=area, region=region):
        b = get_camera_frame_bounds(bpy.context)
    gx = b[0] + 0.7 * (b[2] - b[0])
    w0 = vert_world(idx).copy(); p0 = scr(w0)
    others0 = [tuple(vert_world(i)) for i in range(1, 8)]
    rec(step="setup", vert=p0, guide_x=gx, mode=bpy.context.mode)

    ev('MOUSEMOVE', 'NOTHING', *to_win(*p0)); yield 0.2
    start_move(); yield 0.2
    for i in range(1, 9):
        ev('MOUSEMOVE', 'NOTHING', *to_win(p0[0] + (gx - 6 - p0[0]) * i / 8, p0[1])); yield 0.03
    yield 0.3
    p = scr(vert_world(idx))
    rec(step="near guide (6px)", err=p[0] - gx,
        modal=[o.bl_idname for o in win.modal_operators])
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx + 40, p0[1])); yield 0.3
    p = scr(vert_world(idx))
    rec(step="far (40px)", free_err=p[0] - (gx + 40))
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx + 5, p0[1])); yield 0.3
    ev('LEFTMOUSE', 'PRESS', *to_win(gx + 5, p0[1])); ev('LEFTMOUSE', 'RELEASE', *to_win(gx + 5, p0[1])); yield 0.4
    p = scr(vert_world(idx))
    others = [tuple(vert_world(i)) for i in range(1, 8)]
    rec(step="confirmed", err=p[0] - gx, others_untouched=others == others0)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, "editmode.png"))

    # cancel restores
    w1 = vert_world(idx).copy(); p1 = scr(w1)
    ev('MOUSEMOVE', 'NOTHING', *to_win(*p1)); yield 0.2
    start_move(); yield 0.2
    ev('MOUSEMOVE', 'NOTHING', *to_win(p1[0] + 70, p1[1] + 30)); yield 0.3
    ev('ESC', 'PRESS', *to_win(p1[0] + 70, p1[1] + 30)); ev('ESC', 'RELEASE', *to_win(p1[0] + 70, p1[1] + 30)); yield 0.4
    rec(step="cancel", restored=(vert_world(idx) - w1).length < 1e-6)

    # Vertex toggle off: no snapping in Edit Mode
    s.obj_snap_vertex = False
    p1 = scr(vert_world(idx))
    ev('MOUSEMOVE', 'NOTHING', *to_win(*p1)); yield 0.2
    start_move(); yield 0.2
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx - 30, p1[1])); yield 0.2
    ev('MOUSEMOVE', 'NOTHING', *to_win(gx - 6, p1[1])); yield 0.3
    p = scr(vert_world(idx))
    rec(step="vertex toggle off", free_err=p[0] - (gx - 6))
    ev('ESC', 'PRESS', *to_win(gx - 6, p1[1])); ev('ESC', 'RELEASE', *to_win(gx - 6, p1[1])); yield 0.3
    s.obj_snap_vertex = True

    # 3D guide: a Z line through (3, 0, 0); drag the vertex near it
    g3 = s.guides_3d.add(); g3.point = (3.0, 0.0, 0.0); g3.direction = (0, 0, 1)
    yield 0.2
    from guidebro.world import line3_screen, view_projection
    p1 = scr(vert_world(idx))
    with bpy.context.temp_override(window=win, area=area, region=region):
        info = line3_screen(view_projection(bpy.context), (3, 0, 0), (0, 0, 1))
    A, d = info["A"], info["d"]
    lx = A[0] + d[0] * (p1[1] - A[1]) / d[1]
    ev('MOUSEMOVE', 'NOTHING', *to_win(*p1)); yield 0.2
    start_move(); yield 0.2
    ev('MOUSEMOVE', 'NOTHING', *to_win(lx - 5, p1[1])); yield 0.4
    w = vert_world(idx)
    rec(step="3D guide", xy=(round(w.x, 5), round(w.y, 5)), want=(3.0, 0.0))
    ev('ESC', 'PRESS', *to_win(lx - 5, p1[1])); ev('ESC', 'RELEASE', *to_win(lx - 5, p1[1])); yield 0.3

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            rec(EXC=traceback.format_exc())
        with open(os.path.join(OUT, "editmode.json"), "w") as f:
            json.dump(log, f, indent=1, default=str)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(tick, first_interval=2.0)

run()
