"""List every mesh of the m11101 model with its bones, size and whether it hangs under the main skeleton."""
import os
import sys

sys.path.insert(0, "/data/video2rig/code/v2r/blender")
from common import *  # noqa: E402,F401,F403

P = "/data/video2rig/projects/m11101"
rig = load_json(os.path.join(P, "rig.json"))
popt = load_json(os.path.join(P, "prepare_opts.json"))
arm, meshes = load_model(rig["model"], procedural_material=True, up=popt.get("up", "+Z"))
bones = rig["bones"]
by_name = {b["name"]: i for i, b in enumerate(bones)}


def top(i):
    while bones[i]["parent"] >= 0:
        i = bones[i]["parent"]
    return bones[i]["name"]


for m in sorted(meshes, key=lambda o: o.name):
    gname = {g.index: g.name for g in m.vertex_groups}
    tot = {}
    for v in m.data.vertices:
        for g in v.groups:
            n = gname.get(g.group)
            if n in by_name and g.weight > 1e-4:
                tot[n] = tot.get(n, 0.0) + g.weight
    roots = {}
    for n, w in tot.items():
        roots[top(by_name[n])] = roots.get(top(by_name[n]), 0.0) + w
    V, _ = mesh_world_arrays([m])
    ext = V.max(0) - V.min(0)
    print(f"[mesh] {m.name:16s} verts {len(m.data.vertices):6d} parent {m.parent.name if m.parent else None}/{m.parent_type}/{m.parent_bone} "
          f"extent {ext.round(3).tolist()} centre {V.mean(0).round(3).tolist()} roots {({k: round(v, 1) for k, v in roots.items()})} "
          f"top bones {sorted(tot, key=tot.get, reverse=True)[:4]}")
for i, b in enumerate(bones):
    if top(i) != "Bip001":
        print(f"[bone] {i} {b['name']:20s} parent {bones[b['parent']]['name'] if b['parent'] >= 0 else None} active {b['active']}")
for k in rig["keypoints"]:
    if top(k["bone"]) != "Bip001":
        print(f"[kp] {k['name']} bone {bones[k['bone']]['name']} type {k['type']} side {k['side']}")
print("[rig] keypoints", len(rig["keypoints"]), "active", sum(b["active"] for b in bones), "size", rig["size"],
      "bbox", rig["bbox"])
