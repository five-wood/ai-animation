"""Solver sanity test: feed ground-truth 2D (optionally noisy / with swapped near-far labels)
into fix_swaps + triangulation + skeleton fit and grade against the known motion."""
import sys, os, json
import numpy as np
import torch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.geom import Cams, Skeleton, quat_to_mat_np
from v2r.solve import fix_swaps, triangulate_all, consistency_report, fit
from v2r.exam import angle_between

proj, gt_path = sys.argv[1], sys.argv[2]
noise = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
swap_p = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
rig = json.load(open(os.path.join(proj, "rig.json")))
studio = json.load(open(os.path.join(proj, "studio.json")))
gt = json.load(open(gt_path))
cams = Cams(studio)
views = [c["name"] for c in studio["cams"]]
uv = np.array([[f[v]["uvz"] for v in views] for f in gt["frames"]])[..., :2]
T, V, K, _ = uv.shape
rng = np.random.default_rng(0)
uv = uv + rng.normal(0, noise, uv.shape)
conf = np.ones((T, V, K)) * 0.9
from v2r.solve import limb_chains
chains = limb_chains(rig)
print("limb chains", chains)
n_inj = 0
for t in range(T):
    for L, R in chains:
        if rng.random() < swap_p:  # whole limb exchanged in one view (typical reader error)
            v = rng.integers(V)
            a, b = uv[t, v, L].copy(), uv[t, v, R].copy()
            uv[t, v, L], uv[t, v, R] = b, a
            n_inj += 1
uv_true = np.array([[f[v]["uvz"] for v in views] for f in gt["frames"]])[..., :2]
uv2, conf2, nsw = fix_swaps(cams, rig, uv, conf)
wrong_before = (np.linalg.norm(uv - uv_true, axis=-1) > 6 * max(noise, 1)).sum()
wrong_after = (np.linalg.norm(uv2 - uv_true, axis=-1) > 6 * max(noise, 1)).sum()
print(f"mislabelled points before {wrong_before} after {wrong_after}")
X, res = triangulate_all(cams, uv2, conf2)
rep = consistency_report(cams, rig, X, res, conf2, uv2)
print(f"injected swaps {n_inj}, fixed {nsw}; same_move {rep['same_move']} bad {rep['bad_frames']} "
      f"median rel {rep['median_residual_rel']:.4f}")
sol = fit(rig, studio, uv2, conf2, X, verbose=False)
sk = Skeleton(rig)
G_gt = np.array([f["G"] for f in gt["frames"]])
R_gt = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
with torch.no_grad():
    w_gt, kp_gt = sk.fk_rot(torch.tensor(G_gt, dtype=torch.float32), torch.tensor(R_gt, dtype=torch.float32))
w_gt, kp_gt = w_gt.numpy(), kp_gt.numpy()
e3 = np.linalg.norm(sol["kp"] - kp_gt, axis=-1)
ang = angle_between(sk.bone_dirs(w_gt)[:, sk.active], sk.bone_dirs(sol["world"])[:, sk.active])
print(f"noise {noise}px: joint err {e3.mean() / sk.size * 100:.2f}% size, bone angle mean {ang.mean():.2f} "
      f"median {np.median(ang):.2f} p90 {np.percentile(ang, 90):.2f}, reproj {np.median(sol['rep_px']):.2f}px")
