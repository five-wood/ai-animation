"""Control reader's keypoints (m11101_tex) + dense correspondences read by the dense reader (m11101_tex_dense), same
exam clips: temporal solve without / with the dense observations, and with several strengths.
surface_trial venv.  Output: experiments/surface_solver/dense_combine_m11101_tex.json"""
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

CTRL = "/data/video2rig/projects/m11101_tex"
DENSE = "/data/video2rig/projects/m11101_tex_dense"
rig = json.load(open(f"{CTRL}/rig.json"))
studio = json.load(open(f"{CTRL}/studio.json"))
surf = load_surface(f"{CTRL}/surface.npz", device="cuda")
out = {}
for tag in ("exam", "exam_strike"):
    g1 = json.load(open(f"{CTRL}/{tag}/frames/motion_gt.json"))
    g2 = json.load(open(f"{DENSE}/{tag}/frames/motion_gt.json"))
    assert np.allclose(np.array([f["G"] for f in g1["frames"]]), np.array([f["G"] for f in g2["frames"]])), "different exam"
    rd = {k: x for k, x in np.load(f"{CTRL}/out/{tag}/reading.npz").items()}
    fps = float(np.load(f"{CTRL}/out/{tag}/motion.npz")["fps"])
    obs = dict(np.load(f"{D}/dense/read_m11101_tex_dense_{tag}.npz"))
    res = {}
    for name, s in (("temporal", 0.0), ("dense_0.3", 0.3), ("dense_1.0", 1.0), ("dense_3.0", 3.0)):
        kw = {} if s == 0 else {"dense": {"vid": obs["vid"], "w": obs["w"], "uv": obs["uv"]},
                                 "dense_strength": {"dense_uv": s}, "surface": surf}
        torch.manual_seed(0)
        np.random.seed(0)
        sol = ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps, "temporal", kw)
        with contextlib.redirect_stdout(io.StringIO()):
            m = ab_solver.exam_metrics(f"{D}/code", CTRL, rig, sol, exam_dir=tag)
        m["body_deg"], m["body_p90_deg"] = ab_multi.body_error(rig, g1, sol, Skeleton, quat_to_mat_np, unit_rot_np)
        res[name] = m
        im = m.get("impact") or {}
        print(f"[combine] {tag:12s} {name:10s} joint {m['joint_err_mean_pct_of_size']:.2f}% limb {m['bone_angle_mean_deg']:.2f} "
              f"twist {m['twist_mean_deg']:.2f} body {m['body_deg']:.2f}"
              + (f" peak {im['peak_speed_ratio']:.0%} lag {im['stop_lag_frames']:.2f}" if im else ""), flush=True)
        if s == 1.0:
            np.savez_compressed(f"{D}/dense/sol_combine_{tag}.npz", G=sol["G"], eul=sol["eul"], world=sol["world"])
    out[tag] = res
json.dump(out, open(f"{D}/dense_combine_m11101_tex.json", "w"), indent=1, default=float)
