"""One reader for both (m11101_tex_2step: frozen control keypoints + its own dense decoder): temporal solve without /
with its dense observations at several strengths, on the same exam clips as combine_eval.py (whose keypoints are the
same control keypoints, dense from the jointly trained reader).  surface_trial venv.
Output: experiments/surface_solver/dense_combine_m11101_tex_2step.json"""
import contextlib
import io
import json
import sys

import numpy as np
import torch

D = "/data/video2rig/experiments/surface_solver"
sys.path.insert(0, f"{D}/scripts")
import ab_multi  # noqa: E402
import ab_solver  # noqa: E402

mods = ab_solver.load_code(f"{D}/code")
from v2r.geom import Skeleton, load_surface, quat_to_mat_np, unit_rot_np  # noqa: E402

P = "/data/video2rig/projects/m11101_tex_2step"
rig = json.load(open(f"{P}/rig.json"))
studio = json.load(open(f"{P}/studio.json"))
surf = load_surface(f"{P}/surface.npz", device="cuda")
out = {}
for tag in ("exam", "exam_strike"):
    gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
    rd = {k: x for k, x in np.load(f"{P}/out/{tag}/reading.npz").items()}
    fps = float(np.load(f"{P}/out/{tag}/motion.npz")["fps"])
    obs = dict(np.load(f"{D}/dense/read_m11101_tex_2step_{tag}.npz"))
    res = {}
    for name, s in (("temporal", 0.0), ("dense_1.0", 1.0), ("dense_3.0", 3.0)):
        kw = {} if s == 0 else {"dense": {"vid": obs["vid"], "w": obs["w"], "uv": obs["uv"]},
                                 "dense_strength": {"dense_uv": s}, "surface": surf}
        torch.manual_seed(0)
        np.random.seed(0)
        sol = ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps, "temporal", kw)
        with contextlib.redirect_stdout(io.StringIO()):
            m = ab_solver.exam_metrics(f"{D}/code", P, rig, sol, exam_dir=tag)
        m["body_deg"], m["body_p90_deg"] = ab_multi.body_error(rig, gt, sol, Skeleton, quat_to_mat_np, unit_rot_np)
        res[name] = m
        im = m.get("impact") or {}
        print(f"[combine2] {tag:12s} {name:10s} joint {m['joint_err_mean_pct_of_size']:.2f}% limb {m['bone_angle_mean_deg']:.2f} "
              f"twist {m['twist_mean_deg']:.2f} body {m['body_deg']:.2f}"
              + (f" peak {im['peak_speed_ratio']:.0%} lag {im['stop_lag_frames']:.2f}" if im else ""), flush=True)
    out[tag] = res
json.dump(out, open(f"{D}/dense_combine_m11101_tex_2step.json", "w"), indent=1, default=float)
