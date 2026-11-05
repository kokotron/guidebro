import sys, math, traceback
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import bpy
import guidebro
from guidebro import screen

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        fails.append(name)

try:
    guidebro.register()
    scene = bpy.context.scene
    s = scene.guidebro
    check("scene settings registered", s is not None)

    # --- group library + cameras
    cam_data = bpy.data.cameras.new("CamA")
    camA = bpy.data.objects.new("CamA", cam_data)
    scene.collection.objects.link(camA)
    scene.camera = camA
    g = screen.new_group(s, "CamA Guides")
    camA.guidebro_group = g.uid
    gd = g.guides.add(); gd.name = "H1"; gd.position = 0.25
    check("find_group by uid", screen.find_group(s, camA.guidebro_group).name == "CamA Guides")

    # duplicate camera via operator (Shift+D) and via copy()
    for o in scene.objects: o.select_set(False)
    camA.select_set(True); bpy.context.view_layer.objects.active = camA
    with bpy.context.temp_override(selected_objects=[camA], active_object=camA, object=camA):
        bpy.ops.object.duplicate()
    dup = [o for o in scene.objects if o.type == 'CAMERA' and o != camA]
    check("Shift+D duplicate keeps group", bool(dup) and dup[0].guidebro_group == g.uid,
          f"{[(o.name, o.guidebro_group) for o in dup]}")
    copy = camA.copy(); scene.collection.objects.link(copy)
    check("copy() keeps group", copy.guidebro_group == g.uid)
    users = screen.group_users(s, scene, g)
    check("group users counted", len(users) == 3, str(users))

    # second group, unique naming
    g2 = screen.new_group(s, "CamA Guides")
    check("unique group name", g2.name == "CamA Guides 2", g2.name)
    check("unique uids", g2.uid != g.uid)

    # removing a group leaves dangling uid harmless
    s.groups.remove(1)
    check("dangling uid -> None", screen.find_group(s, g2.uid) is None)

    # --- 3D guide heading / pitch
    g3 = s.guides_3d.add()
    g3.direction = (1, 0, 0)
    g3.heading = math.radians(90)
    d = tuple(round(c, 5) for c in g3.direction)
    check("heading 90 -> +Y", d == (0.0, 1.0, 0.0), str(d))
    g3.pitch = math.radians(90)
    d = tuple(round(c, 5) for c in g3.direction)
    check("pitch 90 -> +Z", d == (0.0, 0.0, 1.0), str(d))
    check("heading kept while vertical", round(math.degrees(g3.heading), 3) == 90.0, str(math.degrees(g3.heading)))
    g3.pitch = 0.0
    d = tuple(round(c, 5) for c in g3.direction)
    check("pitch back to 0 restores heading", d == (0.0, 1.0, 0.0), str(d))
    g3.direction = (1, 1, 1)
    check("direction normalised", abs(sum(c * c for c in g3.direction) - 1) < 1e-6)
    check("pitch of (1,1,1)", abs(math.degrees(g3.pitch) - 35.2644) < 1e-3, str(math.degrees(g3.pitch)))
    check("heading of (1,1,1)", abs(math.degrees(g3.heading) - 45) < 1e-4)
    g3.heading = math.radians(-135)
    d = g3.direction
    check("heading -135 keeps pitch", abs(math.degrees(g3.pitch) - 35.2644) < 1e-3 and d[0] < 0 and d[1] < 0, str(tuple(d)))

    # --- label size prop
    s.label_size = 20
    check("label size", s.label_size == 20)

    # --- objsnap helpers in background (no move running)
    from guidebro import objsnap
    check("modal_operators supported", objsnap.supported() in (True, False))
    s.obj_snap = True
    bpy.context.view_layer.update()
    check("depsgraph handler harmless without a move", True)

    # --- extension-style registration round trip
    guidebro.unregister()
    check("unregistered", not hasattr(bpy.types.Scene, "guidebro"))
    guidebro.register()
    guidebro.unregister()
    check("re-register round trip", True)
except Exception:
    traceback.print_exc()
    fails.append("exception")

print("RESULT:", "ALL PASS" if not fails else f"FAILED {fails}")
