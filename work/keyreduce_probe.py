"""Quick feasibility probe: how many keys does a solved clip need?

Not production code.  Compares (a) per-channel independent keys and (b) shared
"pose keys" (all channels keyed on the same frames), each with two curve types:
  auto   - keys sit on the dense curve, Catmull-Rom style tangents (like Max "Smooth/Auto")
  lsq    - key values and tangents are least-squares fitted (Max Bezier with adjusted handles)
Error is measured on the dense Euler channels (deg) and root centre path (% body size).
"""
import json
import sys
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "work/analyze/left_hind/temporal/motion.npz"
rig_path = sys.argv[2] if len(sys.argv) > 2 else "work/analyze/left_hind/rig.json"
m = np.load(path)
rig = json.load(open(rig_path, encoding="utf-8"))
T = m["G"].shape[0]
fps = float(m["fps"])
size = float(rig["size"])
eul = np.degrees(m["eul"].astype(np.float64)).reshape(T, -1)          # (T, A*3) deg
G = m["G"].astype(np.float64)
centre = np.array(rig["center"], float)
root_pos = np.einsum("tij,j->ti", G[:, :3, :3], centre) + G[:, :3, 3]
root_pos = root_pos / size * 100.0                                     # % body size
# root rotation as unwrapped XYZ euler, deg
R = G[:, :3, :3]
rx = np.degrees(np.arctan2(R[:, 2, 1], R[:, 2, 2]))
ry = np.degrees(np.arcsin(np.clip(-R[:, 2, 0], -1, 1)))
rz = np.degrees(np.arctan2(R[:, 1, 0], R[:, 0, 0]))
root_rot = np.unwrap(np.radians(np.stack([rx, ry, rz], 1)), axis=0)
root_rot = np.degrees(root_rot)
ang = np.concatenate([eul, root_rot], 1)                               # deg channels
t = np.arange(T, dtype=np.float64)


def hermite_basis(s):
    s2, s3 = s * s, s * s * s
    return 2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s, -2 * s3 + 3 * s2, s3 - s2


def eval_auto(keys, y):
    """keys sorted frame indices; y (T,C) dense; keys take dense values, CR tangents."""
    k = np.asarray(keys)
    v = y[k]
    n = len(k)
    m_ = np.zeros_like(v)
    for i in range(n):
        a, b = max(i - 1, 0), min(i + 1, n - 1)
        if a == b:
            continue
        m_[i] = (v[b] - v[a]) / (k[b] - k[a])
    out = np.empty_like(y)
    for i in range(n - 1):
        f0, f1 = k[i], k[i + 1]
        h = f1 - f0
        s = (t[f0:f1 + 1] - f0) / h
        h00, h10, h01, h11 = hermite_basis(s)
        out[f0:f1 + 1] = (h00[:, None] * v[i] + h10[:, None] * h * m_[i]
                          + h01[:, None] * v[i + 1] + h11[:, None] * h * m_[i + 1])
    return out


def eval_lsq(keys, y):
    """Free key values + tangents (C1 Hermite), linear least squares over dense frames."""
    k = np.asarray(keys)
    n = len(k)
    A = np.zeros((T, 2 * n))
    for i in range(n - 1):
        f0, f1 = k[i], k[i + 1]
        h = f1 - f0
        rng = np.arange(f0, f1 + 1) if i == n - 2 else np.arange(f0, f1)
        s = (rng - f0) / h
        h00, h10, h01, h11 = hermite_basis(s)
        A[rng, 2 * i] = h00
        A[rng, 2 * i + 1] = h10 * h
        A[rng, 2 * i + 2] = h01
        A[rng, 2 * i + 3] = h11 * h
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return A @ coef


def greedy(y, tol, evaluator, shared):
    """Insert the frame with the worst error until every channel is within tol.
    shared=True: one key set for all columns; else run each column alone."""
    if not shared:
        total = 0
        for c in range(y.shape[1]):
            total += len(greedy(y[:, c:c + 1], tol, evaluator, True))
        return total
    keys = [0, T - 1]
    while True:
        err = np.abs(evaluator(sorted(keys), y) - y)          # (T,C)
        per_frame = (err / tol).max(1)
        worst = int(per_frame.argmax())
        if per_frame[worst] <= 1.0 or len(keys) >= T:
            return sorted(keys)
        if worst in keys:  # lsq may peak on a key; split the widest neighbouring span
            ks = sorted(keys)
            gaps = np.diff(ks)
            j = int(gaps.argmax())
            worst = (ks[j] + ks[j + 1]) // 2
            if worst in keys:
                return sorted(keys)
        keys.append(worst)


print(f"clip {path}: {T} frames @ {fps:g} fps, {ang.shape[1]} rotation channels "
      f"+ 3 root-position channels")
dense_keys = T * (ang.shape[1] + 3)
print(f"dense: {dense_keys} channel-keys ({T} keys per channel)\n")
print("tol(deg / %size) | per-channel auto | per-channel lsq | shared auto | shared lsq")
for tol_a, tol_p in [(0.5, 0.25), (1.0, 0.5), (2.0, 1.0), (3.0, 1.5)]:
    row = []
    for shared in (False, True):
        for ev in (eval_auto, eval_lsq):
            if shared:
                y = np.concatenate([ang / tol_a, root_pos / tol_p], 1)
                ks = greedy(y, 1.0, ev, True)
                row.append(f"{len(ks):4d} poses")
            else:
                n = greedy(ang, tol_a, ev, False) + greedy(root_pos, tol_p, ev, False)
                row.append(f"{n:5d} ({n / dense_keys * 100:4.1f}%)")
    print(f"{tol_a:4.1f} / {tol_p:4.2f}       | " + " | ".join(row))
