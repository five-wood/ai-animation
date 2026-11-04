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

arm, meshes = load_model(model, procedural_material=True if opt.get("procedural_material") else None,
                         up=opt.get("up", "+Z"))
rig = extract_rig(arm, meshes, facing=opt.get("facing", "-Y"),
                  exclude=opt.get("exclude", DEFAULT_EXCLUDE),
                  merge_frac=opt.get("merge_frac", 0.015),
                  min_seg_frac=opt.get("min_seg_frac", 0.06),
                  keep_bones=opt.get("keep_bones"),
                  limits_override=opt.get("limits"),
                  limits_mode=opt.get("limits_mode", "anatomical"))
rig["model"] = os.path.abspath(model)
rig["up"] = opt.get("up", "+Z")
studio = make_studio(rig, panel_w=opt.get("panel_w", 640), panel_h=opt.get("panel_h", 720),
                     lens=opt.get("lens", 50.0), elev_deg=opt.get("elev", 8.0), fill=opt.get("fill", 0.5))
# Optional exact rig/camera template for a controlled lighting A/B experiment.
# Changing lighting must not silently change the existing creature's keypoint set.
if opt.get("geometry_from"):
    template = os.path.abspath(opt["geometry_from"])
    rig = load_json(os.path.join(template, "rig.json"))
    studio = load_json(os.path.join(template, "studio.json"))
    if len(arm.data.bones) != len(rig["bones"]) or any(b["name"] not in arm.data.bones for b in rig["bones"]):
        raise ValueError("Geometry template does not match imported armature")
    rig["model"] = os.path.abspath(model)
profile = opt.get("visual_profile", "legacy")
if profile not in ("legacy", "soft-clean"):
    raise ValueError("Unknown visual profile")
studio["visual_profile"] = profile
save_json(os.path.join(outdir, "prepare_opts.json"), opt)
save_json(os.path.join(outdir, "rig.json"), rig)
save_json(os.path.join(outdir, "studio.json"), studio)

rig["n_active"] = int(sum(b["active"] for b in rig["bones"]))
save_json(os.path.join(outdir, "rig.json"), rig)
folded = [b["name"] for b in rig["bones"] if b.get("folded")]
if folded:
    print(f"[v2r] folded (rigid with parent, too short to read): {', '.join(folded)}")
print(f"[v2r] bones={len(rig['bones'])} active={rig['n_active']} keypoints={len(rig['keypoints'])} "
      f"size={rig['size']:.3f} facing={rig['facing']} up={rig.get('up', '+Z')}")
lo, hi = np.array(rig["bbox"][0]), np.array(rig["bbox"][1])
ext = hi - lo
if ext[2] < 0.75 * max(ext[0], ext[1]):
    print(f"[v2r] WARNING model is flat in Z (extent x={ext[0]:.3f} y={ext[1]:.3f} z={ext[2]:.3f}); "
          f"it may be lying down - check the reference picture and try --up")
for k in rig["keypoints"]:
    print(f"   kp {k['name']:30s} side={k['side']} mirror={k['mirror']}")
print(f"[v2r] joint limits ({rig.get('limits_mode', 'generic')}), degrees per local Euler axis X/Y/Z:")
for b in rig["bones"]:
    if b["active"]:
        print(f"   lim {b['name']:28s} {b.get('joint', '-'):10s} {b['limits']}  [{b.get('limits_source')}] "
              f"{b.get('limits_note', '')}")
for w in rig["warnings"]:
    print("[v2r] WARNING", w)

# ---------------------------------------------------------------- reference render (Cycles + shadow catcher)
cams = build_cameras(studio)
sc = setup_render(studio, samples=32)
setup_lights(studio)
if profile == "legacy":
    bpy.ops.mesh.primitive_plane_add(size=50 * rig["size"], location=(0, 0, rig["ground_z"]))
    plane = bpy.context.active_object
    plane.is_shadow_catcher = True
else:
    print("[v2r] soft-clean: no ground receiver, no cast floor shadows; broad-source form shading retained")
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
