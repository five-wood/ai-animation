"""Verify the torch FK reproduces Blender keypoints and projection labels."""
import sys, os, json, glob
import numpy as np
import torch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.geom import Skeleton, Cams, quat_to_mat_np, euler_xyz_np, mat_to_euler_xyz_np

proj, gen = sys.argv[1], sys.argv[2]
rig = json.load(open(os.path.join(proj, "rig.json")))
studio = json.load(open(os.path.join(proj, "studio.json")))
sk = Skeleton(rig)
cams = Cams(studio)
meta = []
for f in sorted(glob.glob(os.path.join(gen, "meta_*.json"))):
    meta += json.load(open(f))
errs, perr, eerr = [], [], []
for r in meta:
    G = torch.tensor([r["G"]], dtype=torch.float32)
    R = torch.tensor(quat_to_mat_np(np.array(r["quat"]))[None], dtype=torch.float32)
    _, kp = sk.fk_rot(G, R)
    kp = kp[0].numpy()
    errs.append(np.abs(kp - np.array(r["kp3d"])).max() / sk.size)
    p = cams.project_np(np.array(r["kp3d"]))
    for v, c in enumerate(studio["cams"]):
        perr.append(np.abs(p[v] - np.array(r[c["name"]]["uvz"])).max())
    e = mat_to_euler_xyz_np(quat_to_mat_np(np.array(r["quat"])))
    eerr.append(np.abs(euler_xyz_np(e) - quat_to_mat_np(np.array(r["quat"]))).max())
print(f"FK max rel err {max(errs):.2e}  proj max px err {max(perr):.2e}  euler roundtrip {max(eerr):.2e}")
vis = np.array([[r[c['name']]['vis'] for c in studio['cams']] for r in meta])
print("visible fraction per view", vis.mean((0, 2)))
