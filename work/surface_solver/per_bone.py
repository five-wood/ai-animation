"""Mean change of selected bones' limb error vs the temporal baseline, over the exam clips of each creature."""
import json
import sys

import numpy as np

files = sys.argv[1:]
res = {}
for f in files:
    for v, c in json.load(open(f)).items():
        res.setdefault(v, {}).update(c)
ref = res["temporal"]
BONES = {"m11101": ["Bip001 L Thigh", "Bone004", "Bip001 L Hand", "Bip001 R Hand", "Bip001 L Forearm", "Bip001 R Forearm"],
         "lizard": None}
for v in res:
    if v in ("temporal", "legacy"):
        continue
    out = []
    for cr in ("m11101", "lizard"):
        clips = [c for c in res[v] if c.split(":")[0].split("_s")[0] == cr and not c.endswith(":real") and c in ref]
        if not clips:
            continue
        names = BONES[cr] or sorted(ref[clips[0]]["per_bone"], key=lambda n: -np.mean([ref[c]["per_bone"][n][0] for c in clips]))[:3]
        parts = []
        for n in names:
            a = np.array([res[v][c]["per_bone"][n][0] for c in clips])
            b = np.array([ref[c]["per_bone"][n][0] for c in clips])
            if np.isfinite(a).all():
                parts.append(f"{n.replace('Bip001 ', '')} {b.mean():.1f}->{a.mean():.1f}")
        out.append(f"{cr}: " + ", ".join(parts))
    print(f"{v[:70]}\n   " + "\n   ".join(out))
