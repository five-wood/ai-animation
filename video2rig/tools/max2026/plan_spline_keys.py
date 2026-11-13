"""Offline key-timing plan for the "spline" deliverable: per-part pose keys plus
1..3 breakdowns per pose interval, all on integer target frames.

System Python + NumPy (+ matplotlib for the optional figure). No Max calls, no
scene writes. Interpolation is a proxy (non-uniform Catmull-Rom on quaternions /
positions, i.e. TCB with t=c=b=0); Max Biped TCB and Bone controllers must still be
evaluated in Max before any acceptance claim.

Error is measured on the reader's 3D keypoints (the observed quantity), in cm,
per part in its parent frame (isolates the part) and for the whole body.
"""
from pathlib import Path
import argparse, hashlib, json
import numpy as np

BASE = Path(__file__).resolve().parents[3]
SOURCE = BASE / 'work/maxkeys/m11101'
OUT = BASE / 'work/max_editable_20261111_spline'
CM = 100.0  # source world units -> Max centimeters (verified 2026-11-09)

# Part -> active bones. Order is parent-first.
PARTS = {
    'COM':    ['@G', 'Bip001 Pelvis'],
    'Torso':  ['Bip001 Spine', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 Head'],
    'L Arm':  ['Bip001 L Clavicle', 'Bip001 L UpperArm', 'Bip001 L Forearm', 'Bip001 L Hand'],
    'R Arm':  ['Bip001 R Clavicle', 'Bip001 R UpperArm', 'Bip001 R Forearm', 'Bip001 R Hand'],
    'L Leg':  ['Bip001 L Thigh'],
    'R Leg':  ['Bip001 R Thigh'],
    'L Ear':  ['Bone001', 'Bone002', 'Bone142'],
    'R Ear':  ['Bone0013', 'Bone0021', 'Bone0022'],
    'Tail':   ['Bone004'],
}


# ----------------------------------------------------------------------------- math
def qmat(q):
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                     2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                     2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1
                    ).reshape(q.shape[:-1] + (3, 3))


def matq(R):
    """Rotation matrices (...,3,3) -> wxyz quaternions."""
    R = np.asarray(R, float)
    flat = R.reshape(-1, 3, 3)
    out = np.empty((len(flat), 4))
    for n, m in enumerate(flat):
        K = np.array([
            [m[0, 0] - m[1, 1] - m[2, 2], m[1, 0] + m[0, 1], m[2, 0] + m[0, 2], m[2, 1] - m[1, 2]],
            [m[1, 0] + m[0, 1], m[1, 1] - m[0, 0] - m[2, 2], m[2, 1] + m[1, 2], m[0, 2] - m[2, 0]],
            [m[2, 0] + m[0, 2], m[2, 1] + m[1, 2], m[2, 2] - m[0, 0] - m[1, 1], m[1, 0] - m[0, 1]],
            [m[2, 1] - m[1, 2], m[0, 2] - m[2, 0], m[1, 0] - m[0, 1], m[0, 0] + m[1, 1] + m[2, 2]]]) / 3.0
        w, v = np.linalg.eigh(K)
        x, y, z, s = v[:, -1]
        out[n] = [s, x, y, z]
    return out.reshape(R.shape[:-2] + (4,))


def cr_eval(knots, vals, t, quat=False):
    """Non-uniform Catmull-Rom (Hermite with finite-difference tangents).
    knots (K,), vals (K,...), t (T,) -> (T,...). quat=True: hemisphere-align + renormalise."""
    knots = np.asarray(knots, float)
    v = np.array(vals, float)
    if quat:
        for k in range(1, len(v)):
            dot = np.sum(v[k] * v[k - 1], -1, keepdims=True)
            v[k] = np.where(dot < 0, -v[k], v[k])
    K = len(knots)
    m = np.empty_like(v)
    for k in range(K):
        a, b = max(k - 1, 0), min(k + 1, K - 1)
        m[k] = (v[b] - v[a]) / (knots[b] - knots[a])
    t = np.asarray(t, float)
    seg = np.clip(np.searchsorted(knots, t, side='right') - 1, 0, K - 2)
    t0, t1 = knots[seg], knots[seg + 1]
    h = t1 - t0
    s = ((t - t0) / h).reshape((-1,) + (1,) * (v.ndim - 1))
    hh = h.reshape(s.shape)
    out = ((2 * s**3 - 3 * s**2 + 1) * v[seg] + (s**3 - 2 * s**2 + s) * hh * m[seg]
           + (-2 * s**3 + 3 * s**2) * v[seg + 1] + (s**3 - s**2) * hh * m[seg + 1])
    if quat:
        out = out / np.linalg.norm(out, axis=-1, keepdims=True)
    return out


# ----------------------------------------------------------------------------- rig / FK
class Rig:
    def __init__(self, rig, motion, target_fps):
        self.bones = rig['bones']
        self.names = [b['name'] for b in self.bones]
        self.parent = np.array([b['parent'] for b in self.bones])
        assert all(p < i for i, p in enumerate(self.parent)), 'bones must be parent-first'
        self.W = np.array(rig['world_matrix'])
        self.M = np.array([b['rest'] for b in self.bones])
        self.Mrel = np.array([np.eye(4) if p < 0 else np.linalg.solve(self.M[p], self.M[i])
                              for i, p in enumerate(self.parent)])
        self.kps = rig['keypoints']
        self.kp_bone = np.array([k['bone'] for k in self.kps])
        loc = []
        for k in self.kps:
            p = np.ones(4)
            if k['type'] == 'offaxis':
                p[:3] = k['local']
            elif k['type'] == 'tail':
                p[:3] = [0, k.get('tail_len') or self.bones[k['bone']]['length'], 0]
            else:
                p[:3] = 0
            loc.append(p)
        self.kp_local = np.array(loc)
        self.active = [i for i, b in enumerate(self.bones) if b['active']]
        # source -> target time: integer target frames over the same duration (no stretching)
        src_fps = float(motion['fps'])
        T = len(motion['G'])
        self.src_fps, self.fps = src_fps, target_fps
        self.duration = (T - 1) / src_fps
        self.frames = np.arange(int(round(self.duration * target_fps)) + 1)
        assert abs((len(self.frames) - 1) / target_fps - self.duration) < 1e-9, 'non-integer end frame'
        src_frames = np.arange(T) * target_fps / src_fps
        # dense reference on integer target frames (Catmull-Rom through every source sample)
        self.q_ref = cr_eval(src_frames, motion['quat'], self.frames, quat=True)
        G = motion['G'].astype(float)
        self.root_rest = (self.W @ self.M[0])[:3, 3]
        groot = np.einsum('tij,j->ti', G[:, :3, :3], self.root_rest) + G[:, :3, 3]
        self.gq_ref = cr_eval(src_frames, matq(G[:, :3, :3]), self.frames, quat=True)
        self.gp_ref = cr_eval(src_frames, groot, self.frames)
        # FK self-check against the solver's keypoints at the original samples
        S = self.fk(motion['quat'], matq(G[:, :3, :3]), groot)
        err = np.abs(self.kp(S) - motion['kp']).max()
        if err > 1e-5:
            raise ValueError(f'FK mismatch {err}')
        self.fk_check = float(err)

    def fk(self, q, gq, gp):
        T = len(q)
        Rg = qmat(gq)
        G = np.broadcast_to(np.eye(4), (T, 4, 4)).copy()
        G[:, :3, :3] = Rg
        G[:, :3, 3] = gp - np.einsum('tij,j->ti', Rg, self.root_rest)
        Q = np.broadcast_to(np.eye(4), q.shape[:2] + (4, 4)).copy()
        Q[:, :, :3, :3] = qmat(q)
        S = np.empty_like(Q)
        for i, p in enumerate(self.parent):
            if p < 0:
                S[:, i] = G @ self.W @ self.M[i] @ Q[:, i]
            else:
                S[:, i] = S[:, p] @ self.Mrel[i] @ Q[:, i]
        return S

    def kp(self, S):
        return np.einsum('tkij,kj->tki', S[:, self.kp_bone], self.kp_local)[..., :3]

    def nearest_active(self, i):
        while i >= 0 and i not in self.active:
            i = self.parent[i]
        return i


# ----------------------------------------------------------------------------- key planning
def douglas_peucker(X, tol, min_gap):
    """X (T,P,3). Keep frames whose max point deviation from the chord exceeds tol."""
    keys = {0, len(X) - 1}

    def rec(a, b):
        if b - a < 2:
            return
        s = (np.arange(a, b + 1) - a)[:, None, None] / (b - a)
        chord = (1 - s) * X[a] + s * X[b]
        d = np.linalg.norm(X[a:b + 1] - chord, axis=-1).max(-1)
        d[:min_gap] = 0
        d[-min_gap:] = 0
        j = int(d.argmax())
        if d[j] > tol:
            keys.add(a + j)
            rec(a, a + j)
            rec(a + j, b)

    rec(0, len(X) - 1)
    return sorted(keys)


def plan_part(err_fn, X, n_frames, pose_tol, bd_tol, bd_min, bd_max, min_gap, bd_gap=None):
    """Pose keys are kept >= 2*min_gap apart so every interval can hold a breakdown.
    bd_gap: min spacing for a breakdown that is added because the interval is OVER tolerance
    (fast moves); defaults to min_gap.
    Hard rule: bd_min..bd_max breakdowns per pose interval. If bd_max breakdowns cannot
    meet bd_tol, the worst frame becomes a pose (only if both halves stay >= 2*min_gap);
    otherwise the residual is reported, never hidden by extra keys."""
    span = 2 * min_gap
    bd_gap = min_gap if bd_gap is None else bd_gap
    poses = douglas_peucker(X, pose_tol, span)
    bds = set()
    promoted = 0
    for _ in range(10 * n_frames):
        keys = sorted(set(poses) | bds)
        e = err_fn(keys)  # (T,) max point error, cm
        worst = None
        for a, b in zip(poses[:-1], poses[1:]):
            inside = [k for k in bds if a < k < b]
            emax = float(e[a:b + 1].max())
            over = emax > bd_tol
            g = bd_gap if over else min_gap
            cand = [f for f in range(a + g, b - g + 1)
                    if all(abs(f - k) >= g for k in keys)]
            need_min = len(inside) < bd_min and cand
            if not (need_min or over):
                continue
            if over and (len(inside) >= bd_max or not cand):
                ok = [f for f in range(a + span, b - span + 1)]
                if not ok:
                    continue  # cannot split without breaking the 1..3 rule; residual reported
                f = max(ok, key=lambda x: e[x])
                score = (2, emax, f, 'promote', a, b)
            elif cand:
                f = max(cand, key=lambda x: e[x])
                score = (1 if over else 0, emax, f, 'add', a, b)
            else:
                continue
            if worst is None or score[:2] > worst[:2]:
                worst = score
        if worst is None:
            break
        _, _, f, act, a, b = worst
        if act == 'promote':
            bds = {k for k in bds if not a < k < b}  # re-place breakdowns in both halves
            poses = sorted(set(poses) | {f})
            promoted += 1
        else:
            bds.add(f)
    keys = sorted(set(poses) | bds)
    e = err_fn(keys)
    per_int = [sum(1 for k in bds if a < k < b) for a, b in zip(poses[:-1], poses[1:])]
    over_int = [[int(a), int(b), round(float(e[a:b + 1].max()), 3)]
                for a, b in zip(poses[:-1], poses[1:]) if e[a:b + 1].max() > bd_tol]
    return dict(poses=[int(x) for x in poses], breakdowns=[int(x) for x in sorted(bds)], promoted=promoted,
                breakdowns_per_interval=per_int,
                rule_violations=[i for i, n in enumerate(per_int) if not bd_min <= n <= bd_max],
                over_tolerance_intervals=over_int,
                max_err_cm=float(e.max()), p95_err_cm=float(np.percentile(e, 95)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--bd-min', type=int, default=1)
    ap.add_argument('--bd-max', type=int, default=3)
    ap.add_argument('--min-gap', type=int, default=2, help='min frames between any two keys of one part')
    ap.add_argument('--pose-floor', type=float, default=0.4, help='min pose-selection tolerance, cm')
    ap.add_argument('--bd-floor', type=float, default=0.3, help='min breakdown tolerance, cm')
    ap.add_argument('--tag', default='', help='output file suffix')
    ap.add_argument('--no-figure', action='store_true')
    a = ap.parse_args()

    rig = json.loads((SOURCE / 'rig.json').read_text(encoding='utf8'))
    mo = np.load(SOURCE / 'motion_temporal.npz', allow_pickle=False)
    R = Rig(rig, mo, a.fps)
    F = R.frames
    idx = {n: i for i, n in enumerate(R.names)}
    part_of = {}
    for p, bl in PARTS.items():
        for n in bl:
            part_of[idx[n] if n != '@G' else -1] = p
    act_set = set(R.active)
    assert act_set == {i for i in part_of if i >= 0}, 'every active bone must belong to one part'

    S_ref = R.fk(R.q_ref, R.gq_ref, R.gp_ref)
    kp_ref = R.kp(S_ref) * CM
    # metric points of a part: keypoints whose nearest active ancestor is in the part,
    # plus attachment heads of child parts (so a part also answers for where it carries them)
    kp_part = []
    for k in R.kps:
        na = R.nearest_active(k['bone'])
        kp_part.append(part_of.get(na, 'COM') if na >= 0 else 'COM')
    kp_part = np.array(kp_part)
    heads_ref = S_ref[:, :, :3, 3] * CM

    def part_points(p, S):
        pts = [R.kp(S)[:, kp_part == p] * CM]
        own = [i for i in R.active if part_of[i] == p]
        att = [i for i in R.active if part_of[i] != p and R.nearest_active(R.parent[i]) in own]
        if p == 'COM':
            att = [i for i in R.active if R.nearest_active(R.parent[i]) in own or R.parent[i] == 0]
        if att:
            pts.append(S[:, att][:, :, :3, 3] * CM)
        return np.concatenate(pts, 1)

    def parent_frame(p, S):
        first = PARTS[p][0]
        if first == '@G':
            return None
        return S[:, R.parent[idx[first]]]

    def local_pts(p, S):
        P = part_points(p, S)
        Fp = parent_frame(p, S)
        if Fp is None:
            return P
        Fi = np.linalg.inv(Fp)
        Ph = np.concatenate([P / CM, np.ones(P.shape[:2] + (1,))], -1)
        L = np.einsum('tij,tpj->tpi', Fi, Ph)[..., :3]
        scale = np.cbrt(np.abs(np.linalg.det(Fp[:, :3, :3])))[:, None, None]  # W carries 0.01
        return L * scale * CM  # back to cm, expressed in the parent's (moving) frame

    def interp_part(p, keys, q, gq, gp):
        q = q.copy()
        for n in PARTS[p]:
            if n == '@G':
                gq = cr_eval(keys, R.gq_ref[keys], F, quat=True)
                gp = cr_eval(keys, R.gp_ref[keys], F)
            else:
                i = idx[n]
                q[:, i] = cr_eval(keys, R.q_ref[keys, i], F, quat=True)
        return q, gq, gp

    ref_pts = {p: part_points(p, S_ref) for p in PARTS}
    report = {'schema': 'v2r.keyplan/1', 'source_motion': str(SOURCE / 'motion_temporal.npz'),
              'source_sha256': hashlib.sha256((SOURCE / 'motion_temporal.npz').read_bytes()).hexdigest(),
              'source_fps': R.src_fps, 'target_fps': R.fps, 'frames': [int(F[0]), int(F[-1])],
              'fk_selfcheck_source_units': R.fk_check,
              'interpolation': 'proxy: non-uniform Catmull-Rom on wxyz quats/positions (TCB 0/0/0); NOT Max-evaluated',
              'rule': f'per part: pose keys (chord simplification of parent-frame keypoint paths) + '
                      f'{a.bd_min}..{a.bd_max} breakdowns per pose interval; if {a.bd_max} are not enough the '
                      f'worst frame is promoted to a pose; all keys on integer {a.fps}fps frames',
              'parts': {}}
    q_all, gq_all, gp_all = R.q_ref, R.gq_ref, R.gp_ref
    for p in PARTS:
        X = local_pts(p, S_ref)
        rng = float(np.linalg.norm(X - X.mean(0), axis=-1).max() * 2)
        pose_tol = float(np.clip(0.12 * rng, a.pose_floor, 3.0))
        bd_tol = float(np.clip(0.03 * rng, a.bd_floor, max(1.0, a.bd_floor)))

        def err_fn(keys, p=p):
            q, gq, gp = interp_part(p, keys, R.q_ref, R.gq_ref, R.gp_ref)
            P = part_points(p, R.fk(q, gq, gp))
            return np.linalg.norm(P - ref_pts[p], axis=-1).max(-1)

        res = plan_part(err_fn, X, len(F), pose_tol, bd_tol, a.bd_min, a.bd_max, a.min_gap)
        res.update(motion_range_cm=rng, pose_tol_cm=pose_tol, breakdown_tol_cm=bd_tol, bones=PARTS[p])
        report['parts'][p] = res
        q_all, gq_all, gp_all = interp_part(p, sorted(set(res['poses']) | set(res['breakdowns'])),
                                            q_all, gq_all, gp_all)
        print(f"{p:7s} range {rng:6.2f}cm  poses {len(res['poses']):2d}  breakdowns {len(res['breakdowns']):2d}"
              f"  per-interval {res['breakdowns_per_interval']}  promoted {res['promoted']}"
              f"  local max {res['max_err_cm']:.2f}cm (tol {bd_tol:.2f})"
              f"  violations {res['rule_violations']}  over {res['over_tolerance_intervals']}")

    kp_all = R.kp(R.fk(q_all, gq_all, gp_all)) * CM
    werr = np.linalg.norm(kp_all - kp_ref, axis=-1)
    world = {}
    for p in PARTS:
        m = kp_part == p
        if m.any():
            world[p] = dict(max_cm=float(werr[:, m].max()), p95_cm=float(np.percentile(werr[:, m], 95)))
    union = sorted(set().union(*[set(r['poses']) | set(r['breakdowns']) for r in report['parts'].values()]))
    report['whole_body_keypoint_error_cm'] = world
    report['whole_body_max_cm'] = float(werr.max())
    report['union_key_times'] = len(union)
    report['total_part_keys'] = int(sum(len(r['poses']) + len(r['breakdowns']) for r in report['parts'].values()))
    report['comparison'] = {'dense_source_samples': int(len(mo['G'])), 'previous_sparse_shared_times': 70,
                            'previous_sparse_non_integer_times': 52}
    print(f"whole-body keypoint max {werr.max():.2f}cm; union key times {len(union)}; total part keys {report['total_part_keys']}")

    OUT.mkdir(parents=True, exist_ok=True)
    report['tolerance_floors_cm'] = {'pose': a.pose_floor, 'breakdown': a.bd_floor}
    sfx = f'_{a.tag}' if a.tag else ''
    (OUT / f'keyplan{sfx}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf8')
    if not a.no_figure:
        figure(report, F, kp_ref, kp_all, R, OUT / f'keyplan_timeline{sfx}.png')
    print('wrote', OUT)


def figure(report, F, kp_ref, kp_all, R, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    parts = list(report['parts'])
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(13, 7.5), height_ratios=[1.1, 1.6], sharex=True)
    names = [k['name'] for k in R.kps]
    for n, c in [('Bip001 R Hand', 'tab:red'), ('Bip001 L Hand', 'tab:blue')]:
        j = names.index(n)
        ax0.plot(F, kp_ref[:, j, 2] - kp_ref[0, j, 2], color=c, lw=2.2, alpha=.35, label=f'{n} 高度 · 源')
        ax0.plot(F, kp_all[:, j, 2] - kp_ref[0, j, 2], color=c, lw=1.1, ls='--', label=f'{n} 高度 · 稀疏键重建')
    ax0.set_ylabel('相对第0帧高度 (cm)')
    ax0.legend(loc='upper left', fontsize=8, ncol=2)
    ax0.grid(alpha=.3)
    for r, p in enumerate(parts):
        d = report['parts'][p]
        y = len(parts) - 1 - r
        ax1.hlines(y, F[0], F[-1], color='#ddd', lw=1)
        ax1.scatter(d['poses'], [y] * len(d['poses']), marker='D', s=46, color='#1f3b73', zorder=3)
        ax1.scatter(d['breakdowns'], [y] * len(d['breakdowns']), marker='o', s=16, color='#e3872d', zorder=3)
        ax1.text(F[-1] + 2, y, f"{len(d['poses'])}+{len(d['breakdowns'])}", va='center', fontsize=8)
    ax1.set_yticks(range(len(parts)), parts[::-1])
    ax1.set_xlabel(f"帧 ({report['target_fps']}fps)")
    ax1.scatter([], [], marker='D', color='#1f3b73', label='姿势键')
    ax1.scatter([], [], marker='o', color='#e3872d', label='过渡键 (breakdown)')
    ax1.legend(loc='upper right', fontsize=8, ncol=2, bbox_to_anchor=(1, 1.12))
    ax1.set_xlim(F[0] - 2, F[-1] + 10)
    ax1.grid(axis='x', alpha=.3)
    fig.suptitle('m11101 · 分部位姿势键 + 每段1~3个过渡键（离线代理插值，未经Max回采）', fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


if __name__ == '__main__':
    main()
