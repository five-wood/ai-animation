"""Extra exam clips with other random seeds, in scratch projects (the real projects are only read).

  python make_exams.py <project-name> <seed> <smooth|strike>

Scratch project: /data/video2rig/experiments/surface_solver/exams/<project>_s<seed>/ with links to the real
project's rig.json / studio.json / prepare_opts.json / rest_kp2d.json / model/.  Renders the exam motion,
builds the exam video exactly like v2r.exam.exam, reads and solves it with the default (legacy) solver
(render=False: no preview frames), so out/<tag>/reading.npz + motion.npz have the same form as the real
project's exams.  Grading is left to the A/B script."""
import json
import os
import sys
import time

sys.path.insert(0, "/data/video2rig/code")
from v2r.cli import run_blender  # noqa: E402
from v2r.exam import build_exam_video  # noqa: E402
from v2r.pipeline import animate  # noqa: E402

ROOT = "/data/video2rig/experiments/surface_solver/exams"
proj, seed, style = sys.argv[1], int(sys.argv[2]), sys.argv[3]
src = f"/data/video2rig/projects/{proj}"
dst = f"{ROOT}/{proj}_s{seed}"
os.makedirs(dst, exist_ok=True)
for f in ("rig.json", "studio.json", "prepare_opts.json", "rest_kp2d.json", "model"):
    if os.path.exists(f"{src}/{f}") and not os.path.lexists(f"{dst}/{f}"):
        os.symlink(f"{src}/{f}", f"{dst}/{f}")
tag = "exam" if style == "smooth" else "exam_strike"
fr = f"{dst}/{tag}/frames"
os.makedirs(fr, exist_ok=True)
t0 = time.time()
if not os.path.exists(f"{fr}/motion_gt.json"):
    run_blender("gen_poses.py", dst, fr, json.dumps({"mode": "motion", "frames": 150, "seed": seed, "samples": 16,
                                                     "style": style}), log=f"{dst}/{tag}/render.log")
gt = json.load(open(f"{fr}/motion_gt.json"))
video = f"{dst}/{tag}/exam_video.mp4"
if not os.path.exists(video):
    build_exam_video(dst, fr, gt, video, True)
if not os.path.exists(f"{dst}/out/{tag}/motion.npz"):
    animate(dst, video, name=tag, split="half", device="cuda", calib_mode="off", render=False)
print(f"[exams] {proj} seed {seed} {style} done in {time.time() - t0:.0f}s", flush=True)
