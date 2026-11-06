"""Export the creature's surface for the solver's surface terms: rest-pose mesh in world space, triangles,
vertex normals, the skin weights of every vertex (up to 8 bones, indexed like rig.json) and per-triangle labels.

blender -b --factory-startup --python export_surface.py -- <project> <out.npz> [json-options]
  options: check_exam (exam dir, e.g. "exam"), check_frames (list of frame indices): also store the Blender-
  deformed vertices of those exam frames (ground truth for the torch skinning test).

Loads the model exactly like the other scripts of the project (same --up, procedural material and prop
removal from prepare_opts.json), so the mesh, rig.json and the renders agree.  Meshes are sorted by name so
the vertex order is reproducible (load_model's scene order is not).
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa: E402,F401,F403

args = script_args()
proj_dir, out_path = args[0], args[1]  # not "project": common.project() is a function
opt = json.loads(args[2]) if len(args) > 2 else {}

rig = load_json(os.path.join(proj_dir, "rig.json"))
popt_p = os.path.join(proj_dir, "prepare_opts.json")
popt = load_json(popt_p) if os.path.exists(popt_p) else {}
arm, meshes = load_model(rig["model"], procedural_material=True if popt.get("procedural_material") else None,
                         up=popt.get("up", "+Z"), drop_props=bool(popt.get("drop_props")))
assert len(arm.data.bones) == len(rig["bones"]), "rig.json does not match the model"
check_world_matrix(arm, rig)
meshes = sorted(meshes, key=lambda o: o.name)
bones = rig["bones"]
B = len(bones)
by_name = {b["name"]: i for i, b in enumerate(bones)}
active = {i for i, b in enumerate(bones) if b["active"]}


def chain_top(i):
    while bones[i]["parent"] >= 0:
        i = bones[i]["parent"]
    return i


def carrier(i):
    """Nearest active bone at or above i (the bone whose motion carries it), else its chain's top bone."""
    j = i
    while j >= 0 and j not in active:
        j = bones[j]["parent"]
    return j if j >= 0 else chain_top(i)


roots = [i for i, b in enumerate(bones) if b["parent"] < 0]
main_root = max(roots, key=lambda r: sum(1 for i in range(B) if chain_top(i) == r))

bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
for m in meshes:  # the solver skins the original vertices: no modifier may change the vertex count
    ev = m.evaluated_get(dg)
    me = ev.to_mesh()
    n_ev = len(me.vertices)
    ev.to_mesh_clear()
    if n_ev != len(m.data.vertices):
        raise SystemExit(f"TOPOLOGY_CHANGED: mesh {m.name} has {len(m.data.vertices)} vertices but evaluates to {n_ev}")
V0, F = mesh_world_arrays(meshes)
F = F.astype(np.int32)
nV = len(V0)

MAXW = 8  # 3ds Max skins use up to 4-6 bones per vertex (wrists: twist + hand + finger + forearm ...)
w_idx = np.zeros((nV, MAXW), np.int32)
w_val = np.zeros((nV, MAXW), np.float32)
n_over = 0
mesh_id = np.zeros(nV, np.int16)
off = 0
n_unweighted = 0
deform = {b.name for b in arm.data.bones if b.use_deform}  # the Armature modifier ignores the other groups
blend_modes = set()
for mi, m in enumerate(meshes):
    for md in m.modifiers:
        if md.type == "ARMATURE":
            blend_modes.add(("dual_quat" if md.use_deform_preserve_volume else "linear")
                            + ("" if md.use_vertex_groups else "+no_groups"))
    gname = {g.index: g.name for g in m.vertex_groups}
    rigid = by_name.get(m.parent_bone) if m.parent_type == "BONE" else None
    for vi, v in enumerate(m.data.vertices):
        ws = [(g.weight, by_name[gname[g.group]]) for g in v.groups
              if gname.get(g.group) in by_name and gname[g.group] in deform and g.weight > 1e-4]
        if not ws:
            n_unweighted += 1
            ws = [(1.0, rigid if rigid is not None else main_root)]
        ws.sort(reverse=True)
        n_over += len(ws) > MAXW
        ws = ws[:MAXW]
        tot = sum(w for w, _ in ws)  # Blender's Armature modifier normalises by the weight sum as well
        for k, (w, bi) in enumerate(ws):
            w_idx[off + vi, k] = bi
            w_val[off + vi, k] = w / tot
        mesh_id[off + vi] = mi
    off += len(m.data.vertices)

# per-triangle carrier bone (largest summed carrier weight) and whether it belongs to a prop chain
car_w = {}
tri_car = np.full(len(F), -1, np.int32)
for f, tri in enumerate(F):
    s = {}
    for vi in tri:
        for k in range(MAXW):
            if w_val[vi, k] > 0:
                c = carrier(int(w_idx[vi, k]))
                s[c] = s.get(c, 0.0) + float(w_val[vi, k])
    if s:
        tri_car[f] = max(s, key=s.get)
tri_prop = np.array([c >= 0 and chain_top(int(c)) != main_root for c in tri_car])

# vertex normals of the rest mesh (area weighted)
fn = np.cross(V0[F[:, 1]] - V0[F[:, 0]], V0[F[:, 2]] - V0[F[:, 0]])
N0 = np.zeros_like(V0)
for k in range(3):
    np.add.at(N0, F[:, k], fn)
N0 /= np.linalg.norm(N0, axis=1, keepdims=True).clip(1e-12)

extra = {}
if opt.get("check_exam"):
    gt = load_json(os.path.join(proj_dir, opt["check_exam"], "frames", "motion_gt.json"))
    idx = [int(i) for i in opt.get("check_frames", [0, 37, 75, 112, 149]) if int(i) < len(gt["frames"])]
    Vc = []
    for t in idx:
        fr = gt["frames"][t]
        apply_pose(arm, rig, np.array(fr["G"]), np.array(fr["quat"]))
        bpy.context.view_layer.update()
        Vt, _ = mesh_world_arrays(meshes)
        Vc.append(Vt.astype(np.float32))
    extra = {"check_frames": np.array(idx, np.int32), "check_V": np.stack(Vc),
             "check_G": np.array([gt["frames"][t]["G"] for t in idx], np.float32),
             "check_quat": np.array([gt["frames"][t]["quat"] for t in idx], np.float32)}

os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
np.savez_compressed(out_path, V=V0.astype(np.float32), F=F, N=N0.astype(np.float32), w_idx=w_idx, w_val=w_val,
                    mesh_id=mesh_id, mesh_names=np.array([m.name for m in meshes]), tri_carrier=tri_car,
                    tri_prop=tri_prop, size=float(rig["size"]), **extra)
print(f"[v2r] surface: {len(meshes)} meshes, {nV} vertices, {len(F)} triangles, prop triangles {int(tri_prop.sum())}, "
      f"vertices without weights {n_unweighted}, with more than {MAXW} bones {n_over}, armature blending {sorted(blend_modes)} -> {out_path}")
if blend_modes - {"linear"}:
    print("[v2r] WARNING the solver skins linearly; this model uses another blending (check the skinning test)")
