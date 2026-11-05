"""Put a solved motion on the skeleton, export FBX / GLB / BVH (+ .blend) and render
a preview of the animated model from the studio cameras.

blender -b --factory-startup --python export_anim.py -- <project> <motion.npz> <outdir> <json-options>
  options: name, fps, render (bool), samples
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa: E402,F401,F403

args = script_args()
project, motion_path, outdir = args[0], args[1], args[2]
opt = json.loads(args[3]) if len(args) > 3 else {}
name = opt.get("name", "clip")
fps = float(opt.get("fps", 30))
os.makedirs(outdir, exist_ok=True)

rig = load_json(os.path.join(project, "rig.json"))
studio = load_json(os.path.join(project, "studio.json"))
popt = load_json(os.path.join(project, "prepare_opts.json")) if os.path.exists(
    os.path.join(project, "prepare_opts.json")) else {}
arm, meshes = load_model(rig["model"], procedural_material=True if popt.get("procedural_material") else None,
                         up=popt.get("up", "+Z"), drop_props=bool(popt.get("drop_props")))
check_world_matrix(arm, rig)
mot = np.load(motion_path)
G, Q = mot["G"], mot["quat"]
T = G.shape[0]
sc = bpy.context.scene
sc.render.fps = int(round(fps))
sc.render.fps_base = sc.render.fps / fps
sc.frame_start, sc.frame_end = 1, T
arm.animation_data_create()
act = bpy.data.actions.new(name)
arm.animation_data.action = act
for t in range(T):
    apply_pose(arm, rig, G[t], Q[t], frame=t + 1, key=True)
# Dense per-frame rotations should interpolate monotonically between keys, without
# Blender's default Bezier handles introducing sub-frame overshoot.
set_linear_interp(arm)
print(f"[v2r] keyed {T} frames on {len(rig['bones'])} bones, LINEAR interpolation")

# ---------------------------------------------------------------- exports
bpy.ops.object.select_all(action="DESELECT")
for o in [arm] + meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = arm
fbx = os.path.join(outdir, f"{name}.fbx")
bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, bake_anim=True, bake_anim_use_all_actions=False,
                         bake_anim_use_nla_strips=False, bake_anim_force_startend_keying=True,
                         bake_anim_simplify_factor=0.0, add_leaf_bones=False, path_mode="COPY", embed_textures=True)
try:
    bpy.ops.export_scene.gltf(filepath=os.path.join(outdir, f"{name}.glb"), use_selection=True,
                              export_animations=True, export_force_sampling=True, export_frame_range=True)
except Exception as e:  # pragma: no cover
    print("[v2r] glb export failed:", e)
try:
    bpy.ops.export_anim.bvh(filepath=os.path.join(outdir, f"{name}.bvh"), frame_start=1, frame_end=T,
                            rotate_mode="NATIVE", root_transform_only=False)
except Exception as e:  # pragma: no cover
    print("[v2r] bvh export failed:", e)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(outdir, f"{name}.blend"), copy=True)
print("[v2r] exported", fbx)

# ---------------------------------------------------------------- preview render (our model, same cameras)
if opt.get("render", True):
    cams = build_cameras(studio)
    setup_render(studio, samples=opt.get("samples", 16), blur=False)
    setup_lights(studio)
    prev = os.path.join(outdir, "preview_frames")
    os.makedirs(prev, exist_ok=True)
    kp_all = []
    for t in range(T):
        sc.frame_set(t + 1)
        kp_all.append(keypoints_world(arm, rig).tolist())
        for c, co in zip(studio["cams"], cams):
            render_to(os.path.join(prev, f"{t:05d}_{c['name']}.png"), co)
    with open(os.path.join(outdir, "blender_kp3d.json"), "w") as f:
        json.dump(kp_all, f)
print("[v2r] export done")
