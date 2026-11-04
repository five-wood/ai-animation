"""Round-trip test: GT motion -> export_anim.py -> re-import FBX/GLB -> compare keypoints with GT.
Step 1 (python): python check_export.py make <project> <motion_gt.json> <out.npz>
Step 2 (blender): blender -b --python check_export.py -- verify <project> <file.fbx|glb> <motion_gt.json>
"""
import sys, os, json

if "make" in sys.argv:
    import numpy as np
    i = sys.argv.index("make")
    proj, gt_path, out = sys.argv[i + 1:i + 4]
    gt = json.load(open(gt_path))
    G = np.array([f["G"] for f in gt["frames"]])
    Q = np.array([f["quat"] for f in gt["frames"]])
    np.savez(out, G=G, quat=Q, fps=30)
    print("wrote", out)
else:
    sys.path.insert(0, "/data/video2rig/code/v2r/blender")
    from common import *  # noqa
    a = script_args()
    _, proj, f, gt_path = a
    rig = load_json(os.path.join(proj, "rig.json"))
    gt = load_json(gt_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 30
    if f.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=f, use_anim=True, ignore_leaf_bones=False)
    else:
        bpy.ops.import_scene.gltf(filepath=f)
    arm = max([o for o in bpy.data.objects if o.type == "ARMATURE"], key=lambda o: len(o.data.bones))
    sc = bpy.context.scene
    act = arm.animation_data.action if arm.animation_data else None
    fr = act.frame_range if act else (1, 1)
    print("action", act and act.name, "range", tuple(fr), "bones", len(arm.data.bones))
    errs = []
    start = int(round(fr[0])) if f.endswith(".fbx") else 1
    for t, rec in enumerate(gt["frames"]):
        sc.frame_set(start + t)
        bpy.context.view_layer.update()
        kp = []
        for k in rig["keypoints"]:
            pb = arm.pose.bones.get(rig["bones"][k["bone"]]["name"])
            p = pb.head if k["type"] == "head" else pb.tail
            kp.append(list(arm.matrix_world @ p))
        e = np.linalg.norm(np.array(kp) - np.array(rec["kp3d"]), axis=1)
        # tails depend on imported bone length conventions; report heads separately
        heads = np.array([k["type"] == "head" for k in rig["keypoints"]])
        errs.append((e[heads].max(), e.max()))
    errs = np.array(errs)
    print(f"ROUNDTRIP {os.path.basename(f)}: head max err {errs[:, 0].max() / rig['size'] * 100:.3f}% size, "
          f"all max {errs[:, 1].max() / rig['size'] * 100:.3f}%")
