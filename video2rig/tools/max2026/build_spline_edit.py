"""Build Biped-legal 30fps targets and a per-part spline key plan for m11101.

System Python + NumPy. No Max calls. Output (new run folder, never overwrites old evidence):
  edit_packet.json  - key frames + Max matrix3 rows per node, foot anchors, offsets
  legal_dense.npz   - dense legal / source targets and keypoint riding data for evaluation

Biped-legal model (verified on the 2026-11-09 Max samples, see build report):
  * COM absorbs the source pelvis delta; Pelvis position == COM position.
  * Clavicle head is fixed in Spine1; clavicle has swing only (no twist about X vs Spine1).
  * UpperArm/Forearm/Hand heads lie on +X at fixed lengths; Forearm = UpperArm * Rz(theta), theta <= 0.
  * Arm solved as a two-bone IK: wrist and elbow follow the source; hand keeps source world rotation.
  * Feet: planted world anchors per contact segment (contacts json; default = whole clip planted).
    Anchor = closest point to the observed foot that the leg can reach with <= DROP_BUDGET extra body
    lowering. Body is lowered per frame (smoothed) so planted knees keep >= MIN_KNEE_DEG flex.
    Swing / spin intervals get Biped free keys. Legs themselves are left to Biped IK.

Usage: build_spline_edit.py --source <dir with rig.json, motion_temporal.npz[, reading.npz]>
                            --out <new run dir> [--contacts <contacts_plan.json>]
"""
from pathlib import Path
import argparse, hashlib, json, os, sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from plan_spline_keys import Rig, cr_eval, matq, qmat, plan_part  # noqa: E402

BASE = HERE.parents[2]
SOURCE = BASE / 'work/maxkeys/m11101'
PROFILE = BASE / 'work/max_probe/bind_inspection.json'
if not PROFILE.is_file() and (HERE / 'profiles/m11101_bind_inspection.json').is_file():
    PROFILE = HERE / 'profiles/m11101_bind_inspection.json'   # deployed copy (server)
if os.environ.get('V2R_BIND_PROFILE'):
    PROFILE = Path(os.environ['V2R_BIND_PROFILE'])
OUT = BASE / 'work/max_editable_20261111_spline'
C = np.diag([100., 100., 100., 1.])  # source world -> Max cm (verified 2026-11-09)
MIN_KNEE_DEG = 15.0  # v1 used 10; Max showed 5.3 deg once COM interpolation was included
KNEE_MARGIN_CM = 0.6  # extra body lowering on top of the 15-deg limit: absorbs COM spline overshoot
                      # (Max ladingwu draft3: hip up to 0.48cm above target between COM keys -> 0.2 deg knee)
POLE_TWIST_CM = 2.0  # weight of the source upper-arm twist when the elbow offset is small
DROP_BUDGET = 1.0    # cm of extra body lowering allowed per plant when choosing its anchor
MAX_PLANT_DROP = 3.0  # cm: frames of a plant needing more body lowering than this become free (trimmed)
EDGE_TRIM = 4        # frames: max trimmed from each end of a plant for MAX_PLANT_DROP
SWING_LIFT = (1.0, 2.0, 5.0)  # cm: min, default, max foot lift at a swing key
FREE_STEP = 6        # target frames between free keys in long free / spin intervals

PARENT = {
    'Bip001': None, 'Bip001 Pelvis': 'Bip001', 'Bip001 Spine': 'Bip001 Pelvis', 'Bip001 Spine1': 'Bip001 Spine',
    'Bip001 Neck': 'Bip001 Spine1', 'Bip001 Head': 'Bip001 Neck',
    'Bip001 L Thigh': 'Bip001 Pelvis', 'Bip001 L Calf': 'Bip001 L Thigh', 'Bip001 L Foot': 'Bip001 L Calf',
    'Bip001 R Thigh': 'Bip001 Pelvis', 'Bip001 R Calf': 'Bip001 R Thigh', 'Bip001 R Foot': 'Bip001 R Calf',
    'Bone001': 'Bip001 Head', 'Bone002': 'Bone001', 'Bone142': 'Bone002',
    'Bone0013': 'Bip001 Head', 'Bone0021': 'Bone0013', 'Bone0022': 'Bone0021',
    'Bone004': 'Bip001 Pelvis',
}
for s in 'LR':
    PARENT.update({f'Bip001 {s} Clavicle': 'Bip001 Spine1', f'Bip001 {s} UpperArm': f'Bip001 {s} Clavicle',
                   f'Bip001 {s} Forearm': f'Bip001 {s} UpperArm', f'Bip001 {s} Hand': f'Bip001 {s} Forearm'})
NODES = list(PARENT)  # parent-first order
PARTS = {
    'COM': ['Bip001', 'Bip001 Pelvis'],
    'Torso': ['Bip001 Spine', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 Head'],
    'L Arm': ['Bip001 L Clavicle', 'Bip001 L UpperArm', 'Bip001 L Forearm', 'Bip001 L Hand'],
    'R Arm': ['Bip001 R Clavicle', 'Bip001 R UpperArm', 'Bip001 R Forearm', 'Bip001 R Hand'],
    'L Ear': ['Bone001', 'Bone002', 'Bone142'],
    'R Ear': ['Bone0013', 'Bone0021', 'Bone0022'],
    'Tail': ['Bone004'],
}
LEGS = {'L': ['Bip001 L Thigh', 'Bip001 L Calf', 'Bip001 L Foot'], 'R': ['Bip001 R Thigh', 'Bip001 R Calf', 'Bip001 R Foot']}


# ----------------------------------------------------------------------------- helpers
def col(rows):
    m = np.asarray(rows, float)
    a = np.eye(4)
    a[:, :3] = m
    return a.T


def to_rows(M):
    return [M[:3, 0].tolist(), M[:3, 1].tolist(), M[:3, 2].tolist(), M[:3, 3].tolist()]


def rigid(M):
    u, s, vt = np.linalg.svd(M[..., :3, :3])
    out = M.copy()
    out[..., :3, :3] = u @ vt
    if np.any(np.linalg.det(out[..., :3, :3]) < 0):
        raise ValueError('reflection')
    return out


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def min_rot(a, b):
    """Rotation matrices (T,3,3) taking unit a (3,) or (T,3) to unit b (T,3)."""
    a = np.broadcast_to(a, b.shape)
    k = np.cross(a, b)
    c = np.sum(a * b, -1)
    K = np.zeros(b.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2], K[..., 1, 2] = -k[..., 2], k[..., 1], -k[..., 0]
    K[..., 1, 0], K[..., 2, 0], K[..., 2, 1] = k[..., 2], -k[..., 1], k[..., 0]
    return np.eye(3) + K + K @ K / (1.0 + c)[..., None, None]


def knee_flex(d, l1, l2):
    return np.degrees(np.arccos(np.clip((d * d - l1 * l1 - l2 * l2) / (2 * l1 * l2), -1, 1)))


def skew(v):
    K = np.zeros(v.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2], K[..., 1, 2] = -v[..., 2], v[..., 1], -v[..., 0]
    K[..., 1, 0], K[..., 2, 0], K[..., 2, 1] = v[..., 2], -v[..., 1], v[..., 0]
    return K


def expmap(v):
    th = np.linalg.norm(v, axis=-1)[..., None, None]
    K = skew(v)
    small = th < 1e-8
    ths = np.where(small, 1.0, th)
    A = np.where(small, 1.0, np.sin(ths) / ths)
    Bc = np.where(small, 0.5, (1 - np.cos(ths)) / ths ** 2)
    return np.eye(3) + A * K + Bc * K @ K


def logmap(R):
    c = np.clip((np.trace(R, axis1=-2, axis2=-1) - 1) / 2, -1, 1)
    th = np.arccos(c)[..., None]
    w = np.stack([R[..., 2, 1] - R[..., 1, 2], R[..., 0, 2] - R[..., 2, 0], R[..., 1, 0] - R[..., 0, 1]], -1)
    s = np.sin(th)
    return np.where(th < 1e-6, 0.5 * w, w * th / (2 * np.where(s < 1e-9, 1.0, s)))


def rotz(t):
    c, s = np.cos(t), np.sin(t)
    R = np.zeros(t.shape + (3, 3))
    R[..., 0, 0], R[..., 0, 1], R[..., 1, 0], R[..., 1, 1], R[..., 2, 2] = c, -s, s, c, 1
    return R


ELBOW_MIN = np.radians(-145.0)  # Biped forearm hinge: Rz(theta), theta in [ELBOW_MIN, 0]


def fit_arm(Rs1, s1pos, ref, off, cl, ua, fa, hd, Rhand, markers, target, init, iters=40):
    """Least-squares Biped-legal arm per frame.
    Params (T,6): clavicle swing (y,z rotation vector, no twist), upper-arm rotation vector, elbow theta.
    markers: list of (node, local xyz) riding the arm nodes; target (T,M,3) observed positions."""
    L0 = ref['Bip001 Spine1'][:3, :3].T @ ref[cl][:3, :3]
    Lu = ref[cl][:3, :3].T @ ref[ua][:3, :3]
    Lf = ref[ua][:3, :3].T @ ref[fa][:3, :3]
    o_cl, o_ua, o_fa, o_hd = off('Bip001 Spine1', cl), off(cl, ua), off(ua, fa), off(fa, hd)
    cpos = s1pos + np.einsum('tij,j->ti', Rs1, o_cl)

    # Biped clavicle (Max 2026 measurement on 2 clips): its local Y axis always stays perpendicular to
    # Spine1's local X axis, i.e. Y lies in Spine1's Y-Z plane. Parameterise: aim X freely (2 dof),
    # then choose the roll that puts Y in that plane (no free twist).
    def clav_rot(x):
        Rsw = L0 @ expmap(np.concatenate([np.zeros(x.shape[:-1] + (1,)), x[..., 0:2]], -1))
        X = Rsw[..., :, 0]
        e = np.array([1.0, 0.0, 0.0])
        Y = np.cross(e, X)  # perpendicular to both Spine1 X and clavicle X
        Y = Y / np.linalg.norm(Y, axis=-1, keepdims=True)
        Y = np.where((np.sum(Y * Rsw[..., :, 1], -1) < 0)[..., None], -Y, Y)
        Z = np.cross(X, Y)
        return np.stack([X, Y, Z], -1)

    def pose(x):
        Rc = Rs1 @ clav_rot(x)
        Ru = Rc @ Lu @ expmap(x[..., 2:5])
        Rf = Ru @ Lf @ rotz(x[..., 5])
        pu = cpos + np.einsum('tij,j->ti', Rc, o_ua)
        pf = pu + np.einsum('tij,j->ti', Ru, o_fa)
        ph = pf + np.einsum('tij,j->ti', Rf, o_hd)
        return {cl: (Rc, cpos), ua: (Ru, pu), fa: (Rf, pf), hd: (Rhand, ph)}

    def resid(x):
        P = pose(x)
        out = [P[n][1] + np.einsum('tij,j->ti', P[n][0], v) for n, v in markers]
        return (np.stack(out, 1) - target).reshape(len(x), -1)

    x = init.copy()
    lam = np.full(len(x), 1e-2)
    r = resid(x)
    cost = np.sum(r * r, 1)
    eps = 1e-5
    for _ in range(iters):
        J = np.stack([(resid(x + eps * np.eye(6)[k]) - r) / eps for k in range(6)], -1)
        JtJ = np.einsum('tmi,tmj->tij', J, J)
        g = np.einsum('tmi,tm->ti', J, r)
        A = JtJ + lam[:, None, None] * (np.eye(6) + np.einsum('tii->ti', JtJ)[:, :, None] * np.eye(6))
        dx = -np.linalg.solve(A, g[..., None])[..., 0]
        xn = x + dx
        xn[:, 5] = np.clip(xn[:, 5], ELBOW_MIN, 0.0)
        rn = resid(xn)
        cn = np.sum(rn * rn, 1)
        ok = cn < cost
        x[ok], r[ok], cost[ok] = xn[ok], rn[ok], cn[ok]
        lam = np.where(ok, lam / 3, lam * 5).clip(1e-7, 1e7)
    P = pose(x)
    return P, x, np.sqrt(np.max(np.sum(r.reshape(len(x), -1, 3) ** 2, -1), 1))


# ----------------------------------------------------------------------------- build
def smooth_max_envelope(x, radius):
    """Upper envelope: running max over +-radius, then a triangular blur (keeps x <= result)."""
    T = len(x)
    m = np.array([x[max(0, i - radius):i + radius + 1].max() for i in range(T)])
    w = np.r_[np.arange(1, radius + 2), np.arange(radius, 0, -1)].astype(float)
    w /= w.sum()
    p = np.r_[np.full(radius, m[0]), m, np.full(radius, m[-1])]
    s = np.convolve(p, w, mode='valid')
    return np.maximum(s, x)


def load_contacts(path, fps, src_fps, T):
    """Contacts json (source frames) -> per foot list of [a, b] target frames (inclusive)."""
    if path is None:
        return {s: [[0, T - 1]] for s in 'LR'}, 'default: both feet planted for the whole clip'
    c = json.loads(Path(path).read_text(encoding='utf8'))
    k = fps / src_fps
    out = {}
    for s in 'LR':
        segs = []
        for g in c['feet'][s]:
            a = int(np.ceil(g['start'] * k - 1e-9))
            b = int(np.floor(g['end'] * k + 1e-9))
            if g['start'] == 0:
                a = 0
            if g['end'] == c['frames'] - 1:
                b = T - 1
            if b - a >= 1:
                segs.append([a, b])
        out[s] = segs
    return out, f'{Path(path).name} (reviewed={c.get("reviewed", False)})'


def build(fps, contacts_path=None):
    rig = json.loads((SOURCE / 'rig.json').read_text(encoding='utf8'))
    mo = np.load(SOURCE / 'motion_temporal.npz', allow_pickle=False)
    prof = json.loads(PROFILE.read_text(encoding='utf8'))
    R = Rig(rig, mo, fps)
    F = R.frames
    T = len(F)
    idx = {n: i for i, n in enumerate(R.names)}
    S = R.fk(R.q_ref, R.gq_ref, R.gp_ref)               # source world (Blender, W-scaled) at integer frames
    Sr = R.W @ R.M                                      # source rest
    pnodes = {n['name']: n for n in prof['nodes']}
    ref = {n: col(pnodes[n]['frame0']) for n in NODES}
    # rest heads must agree with the Max frame0 (same check as prepare_motion)
    for n in NODES:
        e = np.linalg.norm((C @ Sr[idx[n]])[:3, 3] - ref[n][:3, 3])
        if e > 0.02:
            raise ValueError(f'rest/frame0 mismatch {n}: {e:.4f}cm')
    tgt = {n: rigid(C @ S[:, idx[n]] @ np.linalg.inv(Sr[idx[n]]) @ np.linalg.inv(C) @ ref[n]) for n in NODES}

    def off(p, c):  # fixed child offset in parent frame, from Max frame0
        return (np.linalg.inv(ref[p]) @ ref[c])[:3, 3]

    # ---- contacts, observed feet, leg geometry
    plants, contact_source = load_contacts(contacts_path, fps, R.src_fps, T)
    l1 = float(np.linalg.norm(off('Bip001 L Thigh', 'Bip001 L Calf')))
    l2 = float(np.linalg.norm(off('Bip001 L Calf', 'Bip001 L Foot')))
    dmax = np.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * np.cos(np.radians(MIN_KNEE_DEG)))
    pel = tgt['Bip001 Pelvis']
    hips = {s: (pel @ np.r_[off('Bip001 Pelvis', f'Bip001 {s} Thigh'), 1.0])[:, :3] for s in 'LR'}
    obs_foot = {}
    rd_path = SOURCE / 'reading.npz'
    if rd_path.is_file():
        rd = np.load(rd_path)
        kpn = [k['name'] for k in R.kps]
        src_frames = np.arange(len(rd['X_fit'])) * fps / R.src_fps
        for s in 'LR':
            j = kpn.index(f'Bip001 {s} Foot')
            Xs = rd['X_fit'][:, j] * 100.0
            cf = rd['conf_fit'][:, :, j]
            Xs[cf[:, 0] <= 0.3] = np.nan
            Xs[cf[:, 1] <= 0.3, 1] = np.nan
            obs_foot[s] = np.stack([np.interp(F, src_frames[~np.isnan(Xs[:, c])], Xs[~np.isnan(Xs[:, c]), c])
                                    if np.any(~np.isnan(Xs[:, c])) else np.full(T, np.nan) for c in range(3)], 1)
            # mark frames far from any observed sample as unknown
            okf = np.interp(F, src_frames, (~np.isnan(Xs[:, 0])).astype(float)) > 0.5
            obs_foot[s][~okf] = np.nan
            oky = np.interp(F, src_frames, (~np.isnan(Xs[:, 1])).astype(float)) > 0.5
            obs_foot[s][~oky, 1] = np.nan

    def need_drop(h, anc):
        """Per-frame body lowering so |hip - anchor| <= dmax (inf if horizontally out of reach)."""
        dxy2 = np.sum((h[:, :2] - anc[:2]) ** 2, 1)
        out = np.full(len(h), np.inf)
        ok = dxy2 < dmax ** 2
        out[ok] = np.maximum(0.0, (h[ok, 2] - anc[2]) - np.sqrt(dmax ** 2 - dxy2[ok]))
        return out

    # ---- trim plants where the source body moves out of leg reach (those frames become free)
    trims = []
    for s in 'LR':
        n = f'Bip001 {s} Foot'
        kept = []
        for a, b in plants[s]:
            for _ in range(50):
                anc = np.median(tgt[n][a:b + 1, :2, 3], 0)
                dxy = np.linalg.norm(hips[s][a:b + 1, :2] - anc, axis=1)
                ok = dxy < 0.92 * dmax
                if ok.all():
                    break
                rr, st = [], None
                for i, v in enumerate(ok):
                    if v and st is None:
                        st = i
                    if (not v or i == len(ok) - 1) and st is not None:
                        rr.append((st, i if v else i - 1))
                        st = None
                if not rr:
                    a = b + 1
                    break
                u, v = max(rr, key=lambda r: r[1] - r[0])
                trims.append({'foot': s, 'plant': [a, b], 'kept': [a + u, a + v]})
                a, b = a + u, a + v
            if b - a >= 1:
                kept.append([a, b])
        plants[s] = kept

    # ---- per-plant anchors: closest to the observed foot within the reach / drop budget
    anchors, anchor_info = {}, {}
    drop = np.zeros(T)
    for s in 'LR':
        n = f'Bip001 {s} Foot'
        Rr = ref[n][:3, :3]
        anchors[s], anchor_info[s] = [], []
        for a, b in [list(x) for x in plants[s]]:
            seg = slice(a, b + 1)
            D = tgt[n][seg, :3, :3] @ Rr.T
            yaw = float(np.median(np.arctan2(D[:, 1, 0], D[:, 0, 0])))
            cz, sz = np.cos(yaw), np.sin(yaw)
            solved = np.median(tgt[n][seg, :2, 3], 0)
            want = solved.copy()
            src_note = 'solved foot'
            if s in obs_foot:
                o = obs_foot[s][seg]
                if np.any(~np.isnan(o[:, 0])):
                    want[0] = np.nanmedian(o[:, 0])
                    src_note = 'observed x'
                if np.mean(~np.isnan(o[:, 1])) > 0.5:
                    want[1] = np.nanmedian(o[:, 1])
                    src_note = 'observed x,y'
            h = hips[s][seg]
            base = need_drop(h, np.r_[solved, ref[n][2, 3]])
            budget = (base.max() if np.isfinite(base.max()) else 0.0) + DROP_BUDGET
            best, t_used = solved, 0.0
            for t in np.linspace(1.0, 0.0, 41):
                cand = solved + t * (want - solved)
                d = need_drop(h, np.r_[cand, ref[n][2, 3]])
                if np.isfinite(d.max()) and d.max() <= budget:
                    best, t_used = cand, float(t)
                    break
            A = np.eye(4)
            A[:3, :3] = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]) @ Rr
            A[:3, 3] = [*best, ref[n][2, 3]]
            d = need_drop(h, A[:3, 3])
            if not np.all(np.isfinite(d)):
                raise ValueError(f'{s} plant {a}-{b}: anchor horizontally out of reach')
            # leg reaches its limit at the plant edges -> those frames are lift-off / touch-down: make them free
            a0, b0 = a, b
            while a < b - 1 and a - a0 < EDGE_TRIM and d[a - a0] > MAX_PLANT_DROP:
                a += 1
            while b > a + 1 and b0 - b < EDGE_TRIM and d[b - a0] > MAX_PLANT_DROP:
                b -= 1
            if (a, b) != (a0, b0):
                trims.append({'foot': s, 'plant': [a0, b0], 'kept': [a, b], 'reason': f'edge frames need > {MAX_PLANT_DROP}cm body drop'})
                plants[s][plants[s].index([a0, b0])] = [a, b]
                seg = slice(a, b + 1)
                h = hips[s][seg]
                d = d[a - a0:b - a0 + 1]
            if b - a < 2:  # a 1-2 frame plant is not an editable support; leave the foot free
                trims.append({'foot': s, 'plant': [a0, b0], 'kept': None, 'reason': 'shorter than 3 frames after trimming'})
                plants[s].remove([a, b])
                continue
            drop[seg] = np.maximum(drop[seg], d)
            anchors[s].append(A)
            drift = np.linalg.norm(tgt[n][seg, :3, 3] - A[:3, 3], axis=1)
            anchor_info[s].append({'frames': [a, b], 'yaw_deg': np.degrees(yaw), 'anchor_xy_cm': best.tolist(),
                                   'target': src_note, 'moved_toward_observed_fraction': t_used,
                                   'observed_xy_cm': want.tolist(), 'solved_xy_cm': solved.tolist(),
                                   'solved_foot_to_anchor_max_cm': float(drift.max()),
                                   'body_drop_needed_max_cm': float(d.max())})

    # ---- body lowering: smooth upper envelope of what the planted legs need
    radius = max(2, int(round(0.2 * fps)))
    planted_any = np.zeros(T, bool)
    for s in 'LR':
        for a, b in plants[s]:
            planted_any[a:b + 1] = True
    margin = np.where(planted_any & (drop > 0), KNEE_MARGIN_CM, 0.0)
    dzs = smooth_max_envelope(np.ceil((drop + margin) * 100) / 100, radius)
    shift = np.broadcast_to(np.eye(4), (T, 4, 4)).copy()
    shift[:, 2, 3] = -dzs
    knees = {}
    for s in 'LR':
        fl = []
        for (a, b), A in zip(plants[s], anchors[s]):
            h = hips[s][a:b + 1] - np.c_[np.zeros((b - a + 1, 2)), dzs[a:b + 1]]
            fl.append(knee_flex(np.linalg.norm(h - A[:3, 3], axis=1), l1, l2))
        fl = np.concatenate(fl) if fl else np.array([np.nan])
        knees[s] = [float(np.nanmin(fl)), float(np.nanmax(fl))]
    dz = float(dzs.max())

    # ---- keypoints in Max space riding on nodes (rigid in the source by construction)
    kp_src = R.kp(S) * 100.0
    kp_node, kp_local, kp_names, kp_dev = [], [], [], []
    for k, kp in enumerate(R.kps):
        b = kp['bone']
        while b >= 0 and R.names[b] not in PARENT:
            b = R.parent[b]
        if b < 0 or (R.names[b] == 'Bip001' and kp['name'] != 'Bip001'):
            continue  # props / unattached
        n = R.names[b]
        loc = np.einsum('tij,tj->ti', np.linalg.inv(tgt[n]), np.c_[kp_src[:, k], np.ones(T)])
        kp_node.append(NODES.index(n))
        kp_local.append(loc.mean(0))
        kp_dev.append(float(np.linalg.norm(loc - loc.mean(0), axis=1).max()))
        kp_names.append(kp['name'])
    kp_node = np.array(kp_node)
    kp_local = np.array(kp_local)
    if max(kp_dev) > 1e-3:
        raise ValueError(f'keypoint riding not rigid: {max(kp_dev)}')
    kp_obs = np.stack([kp_src[:, [k['name'] for k in R.kps].index(n)] for n in kp_names], 1)

    # ---- legal dense poses
    L = {}
    Ts = {n: shift @ tgt[n] for n in NODES}
    L['Bip001'] = Ts['Bip001 Pelvis'] @ np.linalg.inv(ref['Bip001 Pelvis']) @ ref['Bip001']

    def place(n, Rw):
        p = PARENT[n]
        M = np.broadcast_to(np.eye(4), (T, 4, 4)).copy()
        M[:, :3, :3] = Rw
        M[:, :3, 3] = (L[p] @ np.r_[off(p, n), 1.0])[:, :3]
        L[n] = M

    for n in ['Bip001 Pelvis', 'Bip001 Spine', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 Head']:
        place(n, Ts[n][:, :3, :3])
    arm_info = {}
    for s in 'LR':
        cl, ua, fa, hd = (f'Bip001 {s} {k}' for k in ('Clavicle', 'UpperArm', 'Forearm', 'Hand'))
        Rs1 = L['Bip001 Spine1'][:, :3, :3]
        L0 = ref['Bip001 Spine1'][:3, :3].T @ ref[cl][:3, :3]
        cpos = (L['Bip001 Spine1'] @ np.r_[off('Bip001 Spine1', cl), 1.0])[:, :3]
        x1 = unit(np.einsum('tji,tj->ti', Rs1, Ts[ua][:, :3, 3] - cpos))
        place(cl, Rs1 @ min_rot(L0[:, 0], x1) @ L0)
        Sh = (L[cl] @ np.r_[off(cl, ua), 1.0])[:, :3]
        a = float(np.linalg.norm(off(ua, fa)))
        b = float(np.linalg.norm(off(fa, hd)))
        Wr = Ts[hd][:, :3, 3]
        Es = Ts[fa][:, :3, 3]
        v = Wr - Sh
        Ln = np.linalg.norm(v, axis=1)
        w = v / Ln[:, None]
        Lc = np.clip(Ln, abs(a - b) + 0.05, a + b - 0.05)
        d = (Es - Sh) - np.sum((Es - Sh) * w, 1, keepdims=True) * w
        y = Ts[ua][:, :3, 1]
        y = unit(y - np.sum(y * w, 1, keepdims=True) * w)
        p = unit(d + POLE_TWIST_CM * y)
        cosa = np.clip((a * a + Lc * Lc - b * b) / (2 * a * Lc), -1, 1)
        sina = np.sqrt(1 - cosa ** 2)
        X = cosa[:, None] * w + sina[:, None] * p
        Z = unit(np.cross(w, p))
        Y = np.cross(Z, X)
        Ru0 = np.stack([X, Y, Z], -1)
        E = Sh + a * X
        f = unit(Sh + Lc[:, None] * w - E)
        theta0 = -np.arctan2(np.linalg.norm(np.cross(X, f), axis=1), np.sum(X * f, 1))
        # analytic two-bone solution -> initial guess; then least squares on the observed arm keypoints
        Lu = ref[cl][:3, :3].T @ ref[ua][:3, :3]
        Rc0 = L[cl][:, :3, :3]
        x0 = np.zeros((T, 6))
        x0[:, 0:2] = logmap(L0.T @ np.swapaxes(Rs1, 1, 2) @ Rc0)[:, 1:3]
        x0[:, 2:5] = logmap(Lu.T @ np.swapaxes(Rc0, 1, 2) @ Ru0)
        x0[:, 5] = theta0
        sel = [j for j, nk in enumerate(kp_node) if NODES[nk] in (cl, ua, fa, hd)]
        mk = [(NODES[kp_node[j]], kp_local[j][:3]) for j in sel]
        target = kp_obs[:, sel] - np.c_[np.zeros((T, 2)), dzs][:, None, :]
        Pz, xs, res = fit_arm(Rs1, L['Bip001 Spine1'][:, :3, 3], ref, off, cl, ua, fa, hd, Ts[hd][:, :3, :3], mk, target, x0)
        for n in (cl, ua, fa, hd):
            place(n, Pz[n][0])
            gap = float(np.linalg.norm(L[n][:, :3, 3] - Pz[n][1], axis=1).max())
            if gap > 1e-4:
                raise ValueError(f'arm chain mismatch {n}: {gap}')
        theta = np.degrees(xs[:, 5])
        src_d = np.sum(d * y, 1)
        arm_info[s] = {'upper_cm': a, 'fore_cm': b, 'markers': [kp_names[j] for j in sel],
                       'fit_residual_max_cm': float(res.max()), 'fit_residual_p95_cm': float(np.percentile(res, 95)),
                       'elbow_hinge_deg': [float(theta.min()), float(theta.max())],
                       'elbow_at_limit_frames': int(np.sum(xs[:, 5] >= -1e-6)),
                       'source_elbow_offset_against_hinge_frames': int(np.sum(src_d < -0.05)),
                       'wrist_error_cm': float(np.linalg.norm(L[hd][:, :3, 3] - Wr, axis=1).max()),
                       'elbow_error_cm': float(np.linalg.norm(L[fa][:, :3, 3] - Es, axis=1).max()),
                       'shoulder_shift_cm': float(np.linalg.norm(L[ua][:, :3, 3] - Ts[ua][:, :3, 3], axis=1).max())}
    for n in ['Bone001', 'Bone002', 'Bone142', 'Bone0013', 'Bone0021', 'Bone0022', 'Bone004']:
        place(n, Ts[n][:, :3, :3])
    # legs: planted feet at anchors; free feet follow the (shifted) source foot, lifted above the floor
    feet_plan = {}
    for s in 'LR':
        n = f'Bip001 {s} Foot'
        L[f'Bip001 {s} Thigh'] = Ts[f'Bip001 {s} Thigh'].copy()
        L[f'Bip001 {s} Thigh'][:, :3, 3] = (L['Bip001 Pelvis'] @ np.r_[off('Bip001 Pelvis', f'Bip001 {s} Thigh'), 1.0])[:, :3]
        L[f'Bip001 {s} Calf'] = Ts[f'Bip001 {s} Calf']
        Fw = Ts[n].copy()
        planted = np.zeros(T, bool)
        for (a, b), A in zip(plants[s], anchors[s]):
            Fw[a:b + 1] = A
            planted[a:b + 1] = True
        L[n] = Fw
        # free intervals and their keys
        free_runs, st = [], None
        for i in range(T):
            if not planted[i] and st is None:
                st = i
            if (planted[i] or i == T - 1) and st is not None:
                free_runs.append([st, i - 1 if planted[i] else i])
                st = None
        keys = []
        for (a, b), A in zip(plants[s], anchors[s]):
            keys.append({'frame': a, 'type': 'plant_start', 'rows': to_rows(A)})
            keys.append({'frame': b, 'type': 'plant_end', 'rows': to_rows(A)})
        hip_free = hips[s] - np.c_[np.zeros((T, 2)), dzs]
        for a, b in free_runs:
            inner = list(range(a, b + 1))
            nk = max(1, int(round(len(inner) / FREE_STEP)))
            picks = [inner[int(round((k + 0.5) * len(inner) / nk - 0.5))] for k in range(nk)]
            for f in sorted(set(picks)):
                M = Ts[n][f].copy()
                lo = ref[n][2, 3] + SWING_LIFT[0]
                M[2, 3] = float(np.clip(M[2, 3], lo, ref[n][2, 3] + SWING_LIFT[2]))
                if M[2, 3] < ref[n][2, 3] + SWING_LIFT[1] and (b - a + 1) >= 3:
                    M[2, 3] = ref[n][2, 3] + SWING_LIFT[1]
                v = M[:3, 3] - hip_free[f]
                dist = np.linalg.norm(v)
                if dist > dmax:  # keep the free foot reachable with a bent knee
                    M[:3, 3] = hip_free[f] + v / dist * dmax
                keys.append({'frame': int(f), 'type': 'free', 'rows': to_rows(M)})
                L[n][f] = M
        keys.sort(key=lambda k: (k['frame'], k['type'] != 'plant_end'))
        feet_plan[s] = {'plants': plants[s], 'free_runs': free_runs, 'keys': keys}

    Ld = np.stack([L[n] for n in NODES], 1)
    Td = np.stack([tgt[n] for n in NODES], 1)
    info = dict(dz_cm=dz, dz_profile_cm=dzs.tolist(), min_knee_deg=MIN_KNEE_DEG, leg_lengths_cm=[l1, l2],
                knee_flex_range_deg=knees, anchors=anchor_info, arms=arm_info, contacts=contact_source,
                plant_trims_out_of_reach=trims)
    return dict(R=R, F=F, ref=ref, L=L, Ld=Ld, Td=Td, anchors=anchors, info=info, off=off, dzs=dzs,
                feet_plan=feet_plan, kp_src=kp_src, kp_obs=kp_obs, kp_node=kp_node, kp_local=kp_local, kp_names=kp_names)


def add_plant_holds(feet_plan, com_keys, min_gap=3, max_gap=8):
    """Biped interpolates the planted foot's ROTATION between IK keys while the body turns, so the
    foot rolls about its contact pivot (Max test: 8.6 deg / 1.3 cm ankle drift over a 24-frame plant).
    A planted (join) hold key at each COM key inside the plant removes most of it (2.2 deg / 0.3 cm);
    long stretches without a COM key get evenly spaced holds (<= max_gap frames apart)."""
    for s, fp in feet_plan.items():
        extra = []
        starts = [k for k in fp['keys'] if k['type'] == 'plant_start']
        ends = [k for k in fp['keys'] if k['type'] == 'plant_end']
        for k0, k1 in zip(starts, ends):
            a, b = k0['frame'], k1['frame']
            hold = [f for f in sorted(com_keys) if a + min_gap <= f <= b - min_gap]
            pts = [a] + hold + [b]
            filled = []
            for u, v in zip(pts[:-1], pts[1:]):
                n_add = int(np.ceil((v - u) / max_gap)) - 1
                filled += [int(round(u + (v - u) * (i + 1) / (n_add + 1))) for i in range(max(0, n_add))]
            last = a
            for f in sorted(set(hold + filled)):
                if a + min_gap <= f <= b - min_gap and f - last >= min_gap:
                    extra.append({'frame': int(f), 'type': 'plant_hold', 'rows': k0['rows']})
                    last = f
        fp['keys'] = sorted(fp['keys'] + extra, key=lambda k: (k['frame'], k['type'] != 'plant_end'))
    return feet_plan


def ride(poses, kp_node, kp_local):
    """poses (T,N,4,4) -> keypoints (T,K,3)."""
    return np.einsum('tkij,kj->tki', poses[:, kp_node], kp_local)[..., :3]


# ----------------------------------------------------------------------------- proxy interpolation
class Proxy:
    """Max-like FK over NODES: per-node local rotation interpolated between keys (TCB 0/0/0 proxy),
    fixed local offsets; COM world position/rotation interpolated."""

    def __init__(self, B):
        self.B = B
        Ld = B['Ld']
        self.T = len(B['F'])
        self.loc_q, self.off, self.dev = {}, {}, {}
        for i, n in enumerate(NODES):
            p = PARENT[n]
            if p is None:
                self.loc_q[n] = matq(Ld[:, i, :3, :3])
                continue
            loc = np.linalg.inv(Ld[:, NODES.index(p)]) @ Ld[:, i]
            self.loc_q[n] = matq(loc[:, :3, :3])
            self.off[n] = loc[:, :3, 3].mean(0)
            self.dev[n] = float(np.linalg.norm(loc[:, :3, 3] - self.off[n], axis=1).max())
        self.com_p = Ld[:, 0, :3, 3]

    def pose(self, keys_by_node, parent_override=None):
        F = self.B['F']
        Ld = self.B['Ld']
        out = Ld.copy() if parent_override is None else parent_override.copy()
        for i, n in enumerate(NODES):
            K = keys_by_node.get(n)
            if K is None:
                continue
            Rl = qmat(cr_eval(K, self.loc_q[n][K], F, quat=True))
            M = np.broadcast_to(np.eye(4), (self.T, 4, 4)).copy()
            M[:, :3, :3] = Rl
            if PARENT[n] is None:
                M[:, :3, 3] = cr_eval(K, self.com_p[K], F)
                out[:, i] = M
            else:
                M[:, :3, 3] = self.off[n]
                out[:, i] = out[:, NODES.index(PARENT[n])] @ M
        # children of interpolated nodes that are not keyed in this call still follow their parents
        for i, n in enumerate(NODES):
            if n in keys_by_node or PARENT[n] is None:
                continue
            j = NODES.index(PARENT[n])
            loc = np.linalg.inv(Ld[:, j]) @ Ld[:, i]
            out[:, i] = out[:, j] @ loc
        return out


LEG_NODES = {f'Bip001 {s} {k}' for s in 'LR' for k in ('Thigh', 'Calf', 'Foot')}


def gauss_smooth(x, sigma):
    """Gaussian smoothing along axis 0 with edge reflection; x (T, ...)."""
    if sigma <= 0:
        return x.copy()
    r = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.concatenate([x[r:0:-1], x, x[-2:-r - 2:-1]], 0)
    out = np.zeros_like(x)
    for i, w in enumerate(k):
        out += w * pad[i:i + len(x)]
    return out


def smooth_legal(Ld, sigma):
    """Remove frame-to-frame reader jitter from the legal poses: smooth every keyed node's LOCAL
    rotation (and COM world position/rotation), keep the fixed bone offsets, rebuild by FK.
    Leg nodes are left untouched (Biped IK owns them)."""
    if sigma <= 0:
        return Ld.copy()
    out = Ld.copy()
    for i, n in enumerate(NODES):
        if n in LEG_NODES:
            continue
        p = PARENT[n]
        loc = Ld[:, i] if p is None else np.linalg.inv(Ld[:, NODES.index(p)]) @ Ld[:, i]
        q = matq(loc[:, :3, :3])
        for t in range(1, len(q)):
            if q[t] @ q[t - 1] < 0:
                q[t] = -q[t]
        q = gauss_smooth(q, sigma)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        M = loc.copy()
        M[:, :3, :3] = qmat(q)
        if p is None:
            M[:, :3, 3] = gauss_smooth(loc[:, :3, 3], sigma)
            out[:, i] = M
        else:
            M[:, :3, 3] = loc[:, :3, 3].mean(0) if n not in LEG_NODES else loc[:, :3, 3]
            out[:, i] = out[:, NODES.index(p)] @ M
    return out


def markers(poses, names, B, lever=10.0):
    """Per-node head, +X, +Y markers and riding keypoints, in cm."""
    pts = []
    for n in names:
        M = poses[:, NODES.index(n)]
        pts += [M[:, :3, 3], M[:, :3, 3] + lever * M[:, :3, 0], M[:, :3, 3] + lever * M[:, :3, 1]]
    sel = [k for k, i in enumerate(B['kp_node']) if NODES[i] in names]
    if sel:
        kp = ride(poses, B['kp_node'][sel], B['kp_local'][sel])
        pts += [kp[:, j] for j in range(len(sel))]
    return np.stack(pts, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--pose-floor', type=float, default=1.0)
    ap.add_argument('--bd-floor', type=float, default=0.8)
    ap.add_argument('--bd-min', type=int, default=1)
    ap.add_argument('--bd-max', type=int, default=3)
    ap.add_argument('--min-gap', type=int, default=2)
    ap.add_argument('--bd-gap', type=int, default=1, help='breakdown spacing allowed inside over-tolerance (fast) intervals')
    ap.add_argument('--tag', default='v2')
    ap.add_argument('--source', default=None, help='dir with rig.json + motion_temporal.npz (+ reading.npz)')
    ap.add_argument('--out', default=None, help='new run dir (must not contain edit_packet.json)')
    ap.add_argument('--contacts', default=None, help='contacts json in source frames (default: all planted)')
    ap.add_argument('--smooth-sigma', type=float, default=0.0, help='Gaussian jitter removal on legal poses, target frames')
    ap.add_argument('--plan-only', action='store_true', help='print key counts, write nothing')
    a = ap.parse_args()
    global SOURCE, OUT
    if a.source:
        SOURCE = Path(a.source).resolve()
    if a.out:
        OUT = Path(a.out).resolve()
    if (OUT / 'edit_packet.json').exists() and not a.plan_only:
        raise SystemExit(f'{OUT} already has edit_packet.json; choose a new --out')
    B = build(a.fps, a.contacts)
    F = B['F']
    Ld_raw = B['Ld']
    Ld = smooth_legal(Ld_raw, a.smooth_sigma)
    B['Ld'] = Ld
    kp_raw = ride(Ld_raw, B['kp_node'], B['kp_local'])
    smooth_err = np.linalg.norm(ride(Ld, B['kp_node'], B['kp_local']) - kp_raw, axis=-1)
    print('smoothing sigma %.2f frames: keypoint change max %.2fcm p95 %.2fcm' % (a.smooth_sigma, smooth_err.max(), np.percentile(smooth_err, 95)))
    P = Proxy(B)
    bad = {n: d for n, d in P.dev.items() if d > 0.01 and not n.startswith(('Bip001 L Calf', 'Bip001 R Calf', 'Bip001 L Foot', 'Bip001 R Foot', 'Bip001 L Thigh', 'Bip001 R Thigh'))}
    if bad:
        raise ValueError(f'non-constant local offsets in legal poses: {bad}')

    # shape change caused by the legal model (dz compensated) and absolute (with dz)
    kp_legal = ride(Ld, B['kp_node'], B['kp_local'])
    kp_src = B['kp_obs']
    dzv = np.c_[np.zeros((len(F), 2)), B['dzs']][:, None, :]
    legal_err = np.linalg.norm(kp_legal + dzv - kp_src, axis=-1)
    foot_kp = [j for j, n in enumerate(B['kp_names']) if n in ('Bip001 L Foot', 'Bip001 R Foot')]
    body_kp = [j for j in range(len(B['kp_names'])) if j not in foot_kp]

    plan = {}
    for p, names in PARTS.items():
        root_parent = PARENT[names[0]]

        def local_markers(poses):
            M = markers(poses, names, B)
            if root_parent is None:
                return M
            Fp = poses[:, NODES.index(root_parent)]
            return np.einsum('tij,tpj->tpi', np.linalg.inv(Fp), np.concatenate([M, np.ones(M.shape[:2] + (1,))], -1))[..., :3]
        X = local_markers(Ld)
        ref_m = markers(Ld, names, B)
        rng = float(np.linalg.norm(X - X.mean(0), axis=-1).max() * 2)
        pose_tol = float(np.clip(0.12 * rng, a.pose_floor, 3.0))
        bd_tol = float(np.clip(0.03 * rng, a.bd_floor, max(1.0, a.bd_floor)))

        def err_fn(keys, names=names, ref_m=ref_m):
            K = np.array(sorted(keys))
            poses = P.pose({n: K for n in names})
            return np.linalg.norm(markers(poses, names, B) - ref_m, axis=-1).max(-1)
        res = plan_part(err_fn, X, len(F), pose_tol, bd_tol, a.bd_min, a.bd_max, a.min_gap, a.bd_gap)
        res.update(nodes=names, motion_range_cm=rng, pose_tol_cm=pose_tol, breakdown_tol_cm=bd_tol)
        plan[p] = res
        print(f"{p:6s} range {rng:6.2f}cm poses {len(res['poses']):2d} bds {len(res['breakdowns']):2d} "
              f"per-interval {res['breakdowns_per_interval']} max {res['max_err_cm']:.2f}cm tol {bd_tol:.2f} over {res['over_tolerance_intervals']}")

    if a.plan_only:
        tot = sum(len(r['poses']) + len(r['breakdowns']) for r in plan.values())
        print('PLAN ONLY: part keys', tot, {p: len(r['poses']) + len(r['breakdowns']) for p, r in plan.items()})
        return
    com_keys = sorted(set(plan['COM']['poses']) | set(plan['COM']['breakdowns']))
    add_plant_holds(B['feet_plan'], com_keys)
    keys_by_node = {}
    for p, r in plan.items():
        K = np.array(sorted(set(r['poses']) | set(r['breakdowns'])))
        for n in r['nodes']:
            keys_by_node[n] = K
    whole = P.pose(keys_by_node)
    kp_whole = ride(whole, B['kp_node'], B['kp_local'])
    proxy_err = np.linalg.norm(kp_whole - kp_legal, axis=-1)

    OUT.mkdir(parents=True, exist_ok=True)
    packet = {'schema': 'v2r.max.spline_edit/1', 'target_scene': str(BASE / 'work/max_editable_20261109/base_scene.max'),
              'source_motion': str(SOURCE / 'motion_temporal.npz'),
              'source_sha256': hashlib.sha256((SOURCE / 'motion_temporal.npz').read_bytes()).hexdigest(),
              'fps': a.fps, 'frames': [int(F[0]), int(F[-1])], 'unit': 'cm', 'matrix_convention': 'Max matrix3 rows (X,Y,Z,translation)',
              'legal_model': __doc__.strip().splitlines()[0], 'info': B['info'],
              'feet': {s: {'node': f'Bip001 {s} Foot', 'plants': B['feet_plan'][s]['plants'],
                           'free_runs': B['feet_plan'][s]['free_runs'], 'keys': B['feet_plan'][s]['keys'],
                           'key_semantics': 'plant_start: sliding (new independent world IK reference); plant_end: planted (join); '
                                            'free: free key (IK blend 0, body space)'} for s in 'LR'},
              'parts': {}}
    def local(n):
        i = NODES.index(n)
        if PARENT[n] is None:
            return Ld[:, i]
        return np.linalg.inv(Ld[:, NODES.index(PARENT[n])]) @ Ld[:, i]

    def const_local(n):
        if PARENT[n] is None:
            return False
        Lr = local(n)[:, :3, :3]
        c = np.clip((np.trace(np.swapaxes(Lr[:1], 1, 2) @ Lr, axis1=1, axis2=2) - 1) / 2, -1, 1)
        return bool(np.degrees(np.arccos(c)).max() < 0.01)

    for p, r in plan.items():
        K = sorted(set(r['poses']) | set(r['breakdowns']))
        packet['parts'][p] = {'nodes': r['nodes'], 'poses': r['poses'], 'breakdowns': r['breakdowns'],
                              'breakdowns_per_interval': r['breakdowns_per_interval'],
                              'proxy_max_err_cm': r['max_err_cm'], 'over_tolerance_intervals': r['over_tolerance_intervals'],
                              'tolerances_cm': {'pose': r['pose_tol_cm'], 'breakdown': r['breakdown_tol_cm']},
                              'constant_local': {n: const_local(n) for n in r['nodes']},
                              'keys': {n: {str(k): {'world': to_rows(Ld[k, NODES.index(n)]), 'local': to_rows(local(n)[k])}
                                           for k in K} for n in r['nodes']}}
    packet['parent'] = PARENT
    packet['offline'] = {
        'legal_vs_source_keypoint_cm_dz_compensated': {'max': float(legal_err.max()), 'p95': float(np.percentile(legal_err, 95))},
        'legal_vs_source_body_keypoint_cm_dz_compensated': {'max': float(legal_err[:, body_kp].max()),
                                                            'p95': float(np.percentile(legal_err[:, body_kp], 95))},
        'proxy_interp_vs_legal_keypoint_cm': {'max': float(proxy_err.max()), 'p95': float(np.percentile(proxy_err, 95))},
        'per_keypoint_legal_max_cm': {n: float(legal_err[:, j].max()) for j, n in enumerate(B['kp_names'])},
        'per_keypoint_proxy_max_cm': {n: float(proxy_err[:, j].max()) for j, n in enumerate(B['kp_names'])},
        'part_key_count': {p: len(set(r['poses']) | set(r['breakdowns'])) for p, r in plan.items()},
        'feet_key_count': {s: len(B['feet_plan'][s]['keys']) for s in 'LR'},
        'total_part_keys': int(sum(len(set(r['poses']) | set(r['breakdowns'])) for r in plan.values())
                               + sum(len(B['feet_plan'][s]['keys']) for s in 'LR'))}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'edit_packet.json').write_text(json.dumps(packet, ensure_ascii=False), encoding='utf8')
    print('constant-local nodes (no keys written):',
          [n for pp in packet['parts'].values() for n, c in pp['constant_local'].items() if c])
    np.savez(OUT / 'legal_dense.npz', nodes=np.array(NODES), legal=Ld, target=B['Td'], kp_src=kp_src,
             kp_node=B['kp_node'], kp_local=B['kp_local'], kp_names=np.array(B['kp_names']), dz=B['info']['dz_cm'],
             dzs=B['dzs'], frames=F)
    print(json.dumps({k: v for k, v in B['info'].items() if k != 'dz_profile_cm'}, indent=1, ensure_ascii=False))
    print('legal vs source BODY keypoints (feet excluded) max %.2fcm p95 %.2fcm' % (legal_err[:, body_kp].max(), np.percentile(legal_err[:, body_kp], 95)))
    print('feet keys:', {s: [(k['frame'], k['type']) for k in B['feet_plan'][s]['keys']] for s in 'LR'})
    print('legal vs source (dz compensated) keypoint max %.2fcm p95 %.2fcm' % (legal_err.max(), np.percentile(legal_err, 95)))
    print('proxy interpolation vs legal keypoint max %.2fcm p95 %.2fcm' % (proxy_err.max(), np.percentile(proxy_err, 95)))
    worst = sorted(packet['offline']['per_keypoint_legal_max_cm'].items(), key=lambda x: -x[1])[:6]
    print('largest legal-model changes:', [(n, round(v, 2)) for n, v in worst])
    print('wrote', OUT / 'edit_packet.json')


if __name__ == '__main__':
    main()
