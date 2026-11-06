"""Render an existing exam motion (motion_gt.json: per-frame G + every bone's quaternion) on another project of
the same armature - here m11101's exams on m11101_nogun - with the exam's own render settings and labels.

blender -b --factory-startup --python replay_exam.py -- <project> <outdir> '{"replay": "<motion_gt.json>"}'

gen_poses.py is executed with mode "replay" (none of its loops runs) to get the identical scene, cameras,
render settings and labelling; this script then keys the given poses and renders them like mode "motion".
"""
import json
import os
import sys
import time

GEN = "/data/video2rig/code/v2r/blender/gen_poses.py"
_opt = json.loads(sys.argv[sys.argv.index("--") + 3])
_src = json.load(open(_opt["replay"]))
_opt["mode"] = "replay"
sys.argv[sys.argv.index("--") + 3] = json.dumps(_opt)
g = {"__file__": GEN, "__name__": "__gen__"}
exec(compile(open(GEN).read(), GEN, "exec"), g)

np = g["np"]
T = len(_src["frames"])
g["setup_lights"](g["studio"])
for t, fr in enumerate(_src["frames"]):
    g["key_pose"](t + 1, np.array(fr["quat"]), np.array(fr["G"]))
g["set_linear_interp"](g["arm"])
sc = g["sc"]
sc.frame_start, sc.frame_end = 1, T
frames = []
t0 = time.time()
for t, fr in enumerate(_src["frames"]):
    sc.frame_set(t + 1)
    kp, lab = g["labels_for_current_pose"]()
    rec = {"t": t, "kp3d": kp.tolist(), "G": fr["G"], "quat": fr["quat"]}
    for c, co in zip(g["studio"]["cams"], g["cams"]):
        fn = f"{t:05d}_{c['name']}.png"
        g["render_to"](os.path.join(g["outdir"], fn), co)
        rec[c["name"]] = {"img": fn, "uvz": lab[c["name"]]["uvz"].tolist(), "vis": lab[c["name"]]["vis"].tolist()}
    frames.append(rec)
    if t % 30 == 0:
        print(f"[replay] frame {t + 1}/{T}", flush=True)
with open(os.path.join(g["outdir"], "motion_gt.json"), "w") as f:
    json.dump({"fps": _src.get("fps", 30), "frames": frames, "euler": _src.get("euler"), "global": _src.get("global"),
               "style": _src.get("style", "smooth"), "impacts": _src.get("impacts", []),
               "replayed_from": _opt["replay"]}, f)
print(f"[replay] done {T} frames in {time.time() - t0:.0f}s")
