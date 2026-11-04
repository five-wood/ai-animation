"""Step 1: load a rigged monster, extract the keypoint rig, build the two-camera
studio and render the reference picture (front + side in ONE image) that is fed
to Seedance / Kling as the first frame.

blender -b --factory-startup --python prepare.py -- <model> <outdir> [json-options]
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa: E402,F401,F403

args = script_args()
model, outdir = args[0], args[1]
opt = json.loads(args[2]) if len(args) > 2 else {}
os.makedirs(outdir, exist_ok=True)

arm, meshes = load_model(model, procedural_material=True if opt.get("procedural_material") else None)
rig = extract_rig(arm, meshes, facing=opt.get("facing", "-Y"),
                  exclude=opt.get("exclude", DEFAULT_EXCLUDE),
                  merge_frac=opt.get("merge_frac", 0.015),
                  min_seg_frac=opt.get("min_seg_frac", 0.06),
                  keep_bones=opt.get("keep_bones"),
                  limits_override=opt.get("limits"))
rig["model"] = os.path.abspath(model)
studio = make_studio(rig, panel_w=opt.get("panel_w", 640), panel_h=opt.get("panel_h", 720),
                     lens=opt.get("lens", 50.0), elev_deg=opt.get("elev", 8.0), fill=opt.get("fill", 0.5))
save_json(os.path.join(outdir, "prepare_opts.json"), opt)
save_json(os.path.join(outdir, "rig.json"), rig)
save_json(os.path.join(outdir, "studio.json"), studio)

rig["n_active"] = int(sum(b["active"] for b in rig["bones"]))
save_json(os.path.join(outdir, "rig.json"), rig)
folded = [b["name"] for b in rig["bones"] if b.get("folded")]
if folded:
    print(f"[v2r] folded (rigid with parent, too short to read): {', '.join(folded)}")
print(f"[v2r] bones={len(rig['bones'])} active={rig['n_active']} keypoints={len(rig['keypoints'])} "
      f"size={rig['size']:.3f} facing={rig['facing']}")
for k in rig["keypoints"]:
    print(f"   kp {k['name']:30s} side={k['side']} mirror={k['mirror']}")
for w in rig["warnings"]:
    print("[v2r] WARNING", w)

# ---------------------------------------------------------------- reference render (Cycles + shadow catcher)
cams = build_cameras(studio)
sc = setup_render(studio, samples=32)
setup_lights(studio)
bpy.ops.mesh.primitive_plane_add(size=50 * rig["size"], location=(0, 0, rig["ground_z"]))
plane = bpy.context.active_object
plane.is_shadow_catcher = True
sc.render.engine = "CYCLES"
sc.cycles.samples = 64
sc.cycles.use_denoising = True
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for dev_type in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = dev_type
            prefs.get_devices()
            if any(d.type == dev_type for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == dev_type
                sc.cycles.device = "GPU"
                break
        except Exception:
            continue
except Exception as e:  # pragma: no cover
    print("[v2r] cycles gpu unavailable:", e)

for c, co in zip(studio["cams"], cams):
    render_to(os.path.join(outdir, f"ref_{c['name']}.png"), co)

# 2D rest keypoints (for drawing + frame-0 calibration)
kp_w = keypoints_world(arm, rig)
proj = {c["name"]: project(c, kp_w).tolist() for c in studio["cams"]}
save_json(os.path.join(outdir, "rest_kp2d.json"), {"world": kp_w.tolist(), "proj": proj})
print("[v2r] prepare done")
