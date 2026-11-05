"""Surface trial, Blender side (read-only on the project).

For every ground-truth frame of an exam clip: the posed mesh (world space), plus a fixed set of
area-uniform surface samples and their reference visibility in each studio camera, decided by
Blender BVH ray casts from the camera centre (a sample is seen when nothing lies on the ray before it).

blender -b --factory-startup --python export_trial_meshes.py -- <project> <exam_dir> <out.npz> [n_samples]
"""
import os
import sys
import time

sys.path.insert(0, "/data/video2rig/code/v2r/blender")
from common import *  # noqa: E402,F401,F403

args = script_args()
proj_dir, exam_dir, out_path = args[0], args[1], args[2]  # not "project": common.project() is used below
n_samp = int(args[3]) if len(args) > 3 else 3000

rig = load_json(os.path.join(proj_dir, "rig.json"))
studio = load_json(os.path.join(proj_dir, "studio.json"))
popt_p = os.path.join(proj_dir, "prepare_opts.json")
popt = load_json(popt_p) if os.path.exists(popt_p) else {}
arm, meshes = load_model(rig["model"], procedural_material=True if popt.get("procedural_material") else None,
                         up=popt.get("up", "+Z"))
assert len(arm.data.bones) == len(rig["bones"]), "rig.json does not match the model"
check_world_matrix(arm, rig)
# load_model returns the meshes in scene order, which differed between two runs on the same FBX;
# sort by name so vertex / triangle indices are reproducible
meshes = sorted(meshes, key=lambda o: o.name)
gt = load_json(os.path.join(proj_dir, exam_dir, "frames", "motion_gt.json"))
size = float(rig["size"])
bones = rig["bones"]
B = len(bones)
by_name = {b["name"]: i for i, b in enumerate(bones)}
active = {i for i, b in enumerate(bones) if b["active"]}


def chain_top(i):
    while bones[i]["parent"] >= 0:
        i = bones[i]["parent"]
    return i


def carrier(i):
    j = i
    while j >= 0 and j not in active:
        j = bones[j]["parent"]
    return j if j >= 0 else chain_top(i)


roots = [i for i, b in enumerate(bones) if b["parent"] < 0]
main_root = max(roots, key=lambda r: sum(1 for i in range(B) if chain_top(i) == r))

# ------------------------------------------------------------------ rest mesh, weights, carriers
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
counts = []
for m in meshes:
    ev = m.evaluated_get(dg)
    me = ev.to_mesh()
    counts.append((len(m.data.vertices), len(me.vertices)))
    ev.to_mesh_clear()
topo_ok = all(a == b for a, b in counts)
V0, F = mesh_world_arrays(meshes)
F = F.astype(np.int32)
nV = len(V0)

w_idx = np.full((nV, 4), -1, np.int32)
w_val = np.zeros((nV, 4), np.float32)
v_car = {}  # vertex -> {carrier: weight}
off = 0
for m in meshes:
    gname = {g.index: g.name for g in m.vertex_groups}
    rigid = by_name.get(m.parent_bone) if m.parent_type == "BONE" else None
    for vi, v in enumerate(m.data.vertices):
        ws = []
        for g in v.groups:
            bi = by_name.get(gname.get(g.group))
            if bi is not None and g.weight > 1e-4:
                ws.append((g.weight, bi))
        if not ws and rigid is not None:
            ws = [(1.0, rigid)]
        ws.sort(reverse=True)
        ws = ws[:4]
        tot = sum(w for w, _ in ws)
        d = {}
        for k, (w, bi) in enumerate(ws):
            w_idx[off + vi, k] = bi
            w_val[off + vi, k] = w / tot
            c = carrier(bi)
            d[c] = d.get(c, 0.0) + w
        v_car[off + vi] = d
    off += len(m.data.vertices)

tri_car = np.full(len(F), -1, np.int32)
for f, tri in enumerate(F):
    s = {}
    for vi in tri:
        for c, w in v_car.get(int(vi), {}).items():
            s[c] = s.get(c, 0.0) + w
    if s:
        tri_car[f] = max(s, key=s.get)
tri_prop = np.array([c >= 0 and chain_top(int(c)) != main_root for c in tri_car])

# area-uniform samples on the rest surface, fixed (triangle, barycentric) so they ride on the mesh
rng = np.random.RandomState(0)
a = V0[F[:, 1]] - V0[F[:, 0]]
b = V0[F[:, 2]] - V0[F[:, 0]]
area = 0.5 * np.linalg.norm(np.cross(a, b), axis=1)
s_tri = rng.choice(len(F), n_samp, p=area / area.sum()).astype(np.int32)
r1, r2 = rng.rand(n_samp), rng.rand(n_samp)
sq = np.sqrt(r1)
s_bary = np.stack([1 - sq, sq * (1 - r2), sq * r2], 1).astype(np.float32)

# ------------------------------------------------------------------ frames
cams = studio["cams"]
Cc = [(-np.array(c["R"]).T @ np.array(c["t"])) for c in cams]
eps = 1e-3 * size
T = len(gt["frames"])
Vs = np.zeros((T, nV, 3), np.float32)
vis = np.zeros((T, len(cams), n_samp), bool)
inimg = np.zeros((T, len(cams), n_samp), bool)
kp_err = np.zeros(T)
tri_mismatch = 0
t_pose = t_ray = 0.0
for t, fr in enumerate(gt["frames"]):
    t0 = time.time()
    apply_pose(arm, rig, np.array(fr["G"]), np.array(fr["quat"]))
    bpy.context.view_layer.update()
    kp = keypoints_world(arm, rig)
    kp_err[t] = np.abs(kp - np.array(fr["kp3d"])).max() / size
    V, Ft = mesh_world_arrays(meshes)
    if Ft.shape != F.shape or (Ft != F).any():
        tri_mismatch += 1
    Vs[t] = V
    t1 = time.time()
    bvh = BVHTree.FromPolygons(V.astype(float).tolist(), F.astype(int).tolist(), all_triangles=True)
    P = (s_bary[:, :, None] * V[F[s_tri]]).sum(1)
    for ci, c in enumerate(cams):
        uvz = project(c, P)
        inimg[t, ci] = (uvz[:, 0] >= 0) & (uvz[:, 0] < c["w"]) & (uvz[:, 1] >= 0) & (uvz[:, 1] < c["h"]) & (uvz[:, 2] > 0)
        o = Vector(Cc[ci].tolist())
        for n in range(n_samp):
            d = P[n] - Cc[ci]
            dist = float(np.linalg.norm(d))
            hit = bvh.ray_cast(o, Vector((d / dist).tolist()), dist - eps)
            vis[t, ci, n] = hit[0] is None
    t_pose += t1 - t0
    t_ray += time.time() - t1
    if t % 30 == 0:
        print(f"[trial] {exam_dir} frame {t}/{T} kp_err {kp_err[t]:.2e}", flush=True)

os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
np.savez_compressed(out_path, V=Vs, V_rest=V0.astype(np.float32), F=F, tri_carrier=tri_car, tri_prop=tri_prop,
                    w_idx=w_idx, w_val=w_val, s_tri=s_tri, s_bary=s_bary, vis_ref=vis, inimg=inimg,
                    kp_err=kp_err, size=size, main_root=main_root,
                    mesh_names=np.array([m.name for m in meshes]), mesh_vcount=np.array([c[0] for c in counts]))
print(f"[trial] meshes {len(meshes)} verts {nV} tris {len(F)} topo_ok {topo_ok} {counts} tri_mismatch_frames {tri_mismatch}")
print(f"[trial] prop tris {tri_prop.sum()} ({tri_prop.mean():.1%}), samples {n_samp}, prop samples {tri_prop[s_tri].sum()}")
print(f"[trial] kp_err max {kp_err.max():.2e} (size units); pose+mesh {t_pose / T * 1000:.0f} ms/frame, "
      f"blender rays {t_ray / T * 1000:.0f} ms/frame ({2 * n_samp} rays)")
print(f"[trial] ref visible fraction {vis[inimg].mean():.3f}")
print("[trial] EXPORT DONE", out_path)
