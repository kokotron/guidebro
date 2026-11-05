"""Bounds snapping probe: move the cube 1 px at a time past a guide and log
where its on-screen box lands, plus how often the handler writes."""
import sys, os, json, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
OUT = argv[1]
PERSP = argv[2] if len(argv) > 2 else 'CAMERA'
import bpy
import numpy as np
from bpy_extras import view3d_utils
import guidebro
from guidebro import screen, objsnap
from guidebro.common import get_camera_frame_bounds

guidebro.register()
writes = [0]
_orig_apply = objsnap._apply_offset
def counting_apply(objs, D, depsgraph):
    writes[0] += 1
    return _orig_apply(objs, D, depsgraph)
objsnap._apply_offset = counting_apply

log = []

def view():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region, area.spaces.active.region_3d

def box():
    win, area, region, rv3d = view()
    ob = bpy.data.objects["Cube"]
    pts = [ob.matrix_world @ v.co for v in ob.data.vertices]
    xy = [view3d_utils.location_3d_to_region_2d(region, rv3d, p) for p in pts]
    return (min(p.x for p in xy), min(p.y for p in xy), max(p.x for p in xy), max(p.y for p in xy))

def to_win(x, y):
    win, area, region, rv3d = view()
    return int(region.x + x), int(region.y + y)

def ev(t, v, x, y):
    bpy.context.window_manager.windows[0].event_simulate(t, v, x=x, y=y)

def steps():
    win, area, region, rv3d = view()
    scene = bpy.context.scene; s = scene.guidebro
    cx0, cy0 = to_win(region.width / 2, region.height / 2)
    ev('MOUSEMOVE', 'NOTHING', cx0, cy0); ev('ESC', 'PRESS', cx0, cy0); ev('ESC', 'RELEASE', cx0, cy0)
    rv3d.view_perspective = PERSP
    if PERSP != 'CAMERA':
        rv3d.view_distance = 15
    g = screen.new_group(s, "G")
    if PERSP == 'CAMERA':
        scene.camera.guidebro_group = g.uid
    else:
        s.viewport_group = g.uid
    v = g.guides.add(); v.axis = 'V'; v.position = 0.7
    h = g.guides.add(); h.axis = 'H'; h.position = 0.3
    s.obj_snap = True; s.obj_snap_origin = False; s.obj_snap_bounds = True
    cube = bpy.data.objects["Cube"]
    for o in scene.objects: o.select_set(o == cube)
    bpy.context.view_layer.objects.active = cube
    yield 1.0
    with bpy.context.temp_override(window=win, area=area, region=region):
        b = get_camera_frame_bounds(bpy.context) if PERSP == 'CAMERA' else (0, 0, region.width, region.height)
    gx = b[0] + 0.7 * (b[2] - b[0])
    b0 = box()
    start = ((b0[0] + b0[2]) / 2, (b0[1] + b0[3]) / 2)
    ev('MOUSEMOVE', 'NOTHING', *to_win(*start)); yield 0.2
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.transform.translate('INVOKE_DEFAULT')
    yield 0.2
    # right edge of the box starts at b0[2]; sweep it from gx-30 to gx+30
    for i in range(0, 61, 2):
        target_right = gx - 30 + i
        mx = start[0] + (target_right - b0[2])
        writes[0] = 0
        ev('MOUSEMOVE', 'NOTHING', *to_win(mx, start[1])); yield 0.08
        r1 = box()
        w1 = writes[0]
        yield 0.15                       # no mouse movement: is it stable?
        r2 = box()
        log.append({"free_right_minus_guide": target_right - gx,
                    "right_minus_guide": round(r1[2] - gx, 3),
                    "drift_without_mouse": round(r2[2] - r1[2], 3),
                    "writes": w1, "writes_idle": writes[0] - w1})
    ev('ESC', 'PRESS', *to_win(start[0], start[1]))

def run():
    gen = steps()
    def tick():
        try:
            return next(gen)
        except StopIteration:
            pass
        except Exception:
            log.append({"EXC": traceback.format_exc()})
        with open(os.path.join(OUT, f"bounds_{PERSP}.json"), "w") as f:
            json.dump(log, f, indent=0)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(tick, first_interval=2.0)

run()
