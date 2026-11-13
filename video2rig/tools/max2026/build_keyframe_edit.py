"""Keyframe-first edit packet: whole-body keys on the artist-reviewed keyframes (docs/关键帧优先方案_20261111.md).

python build_keyframe_edit.py --source work/maxkeys/m11101_tex_ladingwu --out <new run dir>
                              [--artist keyframes_artist.json] [--breakdowns 0|N] [--fps 30]

System Python + NumPy. No Max calls. Writes the same packet format as build_spline_edit.py, so
import_spline_edit.py / evaluate_spline_edit.py / post_checks_spline.py / overlay_video.py work unchanged:
  edit_packet.json, legal_dense.npz, key_poses.json (per-key report with problem flags)

What is different from build_spline_edit.py (the 11-11 "spline" version the artist rejected):
  * timing: ONE set of whole-body key frames = the artist's keyframes (video frame f -> Max frame floor(f*30/24+0.5)),
    optionally + the first N suggested breakdowns of the ladder; no per-part key planning
  * key poses: Biped-legal fit as before; slow keys (start/end/contact/hold) use a lightly denoised pose
    (local-rotation Gaussian, ~1 video frame), extremes / impacts / spin keys keep the raw pose (peaks not shaved)
  * feet: plants = the contact segments the artist approved (touch/lift keys), not the merged contacts_plan
  * stance prior: planted knees in a natural band [--min-knee, --max-knee] (20-35 deg). The body height on each
    COM key moves only as needed for that band (up or down, <= --max-raise up); the anchor is the floor point
    closest to the observed foot that needs <= --height-cap cm of body height change. The old version pinned
    anchors at the observed foot and lowered the body up to 5 cm (deep squat): this rig's legs are 14.5 cm and
    the observed footholds are 8-22 cm from the hip horizontally.
  * interpolated frames: a planted knee never gets straighter than --min-knee - 6 deg (Biped slides the planted
    ankle ~0.4 cm between keys; 0.2 cm of hip distance is ~10 deg of knee on these short legs)
  * free feet: keyed on the shared key frames, lifted 1-6 cm and within leg reach, plus one key 2 frames before
    each touchdown / after each lift-off hovering 2 cm above the anchor (free keys interpolate in body space, and
    without it the toe cut 2.3 cm into the floor in Max)
"""
from pathlib import Path
import argparse, hashlib, json, sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_spline_edit as bse  # noqa: E402
from plan_spline_keys import cr_eval  # noqa: E402

MIN_KNEE_DEG = 20.0     # planted knees bend at least this much. The legs are short (14.5 cm), so 0.2 cm of
                        # hip distance is ~10 deg of knee; between keys Biped lets the planted ankle slide
                        # up to ~0.4 cm, which straightened 12-deg knees in Max (v2) -> keep a 20-deg margin
MAX_KNEE_DEG = 35.0     # ... and at most this much when the body height allows it (natural stance, no squat)
MAX_RAISE_CM = 3.0      # body may sit this much higher than the (poorly observed) video COM height
DROP_MARGIN_CM = 0.25   # proxy (Catmull-Rom) vs Max TCB on COM
HEIGHT_CAP_CM = 1.5     # planted foot goes as close to the observed spot as the legs allow with <= this body height change
APPROACH = 2            # frames: extra free key this far before touchdown / after lift-off, above the anchor
FREE_LIFT = (1.0, 2.0, 6.0)  # cm: min, default, max ankle lift above its rest height on free-foot keys
# (free keys interpolate in body space; while the body turns between sparse keys the foot dips ~2 cm,
#  so a stepping foot must be keyed lifted, as in build_spline_edit.SWING_LIFT)
SMOOTH_SIGMA_24 = 1.0   # video frames
SMOOTH_TYPES = {'start', 'end', 'contact', 'hold_start', 'hold_end', 'breakdown'}
KEEP_RAW = {'extreme', 'impact', 'technical'}
LEG = {s: ['Bip001 %s Thigh' % s, 'Bip001 %s Calf' % s, 'Bip001 %s Foot' % s] for s in 'LR'}


def vf2mf(f, sfps, tfps):
    return int(np.floor(f * tfps / sfps + 0.5))


def rotz(t):
    c, s = np.cos(t), np.sin(t)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--artist', default='keyframes_artist.json')
    ap.add_argument('--auto', default='keyframes_auto.json')
    ap.add_argument('--breakdowns', type=int, default=0, help='also key the first N suggested ladder breakdowns')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--height-cap', type=float, default=HEIGHT_CAP_CM, help='cm the body may move off the video height so a planted foot can stay at the observed spot')
    ap.add_argument('--min-knee', type=float, default=MIN_KNEE_DEG)
    ap.add_argument('--max-knee', type=float, default=MAX_KNEE_DEG, help='natural stance: planted knees bend at most this much when possible')
    ap.add_argument('--max-raise', type=float, default=MAX_RAISE_CM, help='cm the body may be raised above the video height')
    a = ap.parse_args()
    src = Path(a.source).resolve()
    out = Path(a.out).resolve()
    if (out / 'edit_packet.json').exists():
        raise SystemExit(f'{out} already has edit_packet.json; choose a new --out')
    art = json.loads((src / a.artist).read_text(encoding='utf8'))
    auto = json.loads((src / a.auto).read_text(encoding='utf8'))
    auto_bytes = (src / a.auto).read_bytes()
    if art.get('base_auto_sha256') and art['base_auto_sha256'] != hashlib.sha256(auto_bytes).hexdigest():
        raise SystemExit('artist annotation was made on a different candidate file; open it in the web page and save again')
    ct_name = auto.get('params', {}).get('contacts_file', 'contacts_auto.json')
    ct = json.loads((src / ct_name).read_text(encoding='utf8'))
    sfps, tfps = float(auto['fps']), float(a.fps)
    vframes = int(auto['frames'])

    # ---- Biped-legal dense motion (build() lowers the body for its own plants: undo that)
    bse.SOURCE = src
    B = bse.build(a.fps, str(src / 'contacts_plan.json') if (src / 'contacts_plan.json').is_file() else None)
    F = B['F']
    T = len(F)
    NODES = bse.NODES
    ix = {n: i for i, n in enumerate(NODES)}
    feet_idx = [ix['Bip001 L Foot'], ix['Bip001 R Foot']]
    Lr = B['Ld'].copy()
    for i, n in enumerate(NODES):
        if i not in feet_idx:
            Lr[:, i, 2, 3] += B['dzs']
    err = np.abs(Lr[:, ix['Bip001 Pelvis'], :3, 3] - B['Td'][:, ix['Bip001 Pelvis'], :3, 3]).max()
    assert err < 1e-6, f'un-lowering failed ({err})'
    ref, off = B['ref'], B['off']
    l1, l2 = B['info']['leg_lengths_cm']
    dm = float(np.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * np.cos(np.radians(a.min_knee))))
    gz = float(ref['Bip001 L Foot'][2, 3])

    # spin frames (fast COM yaw) and the denoised reference
    R0 = Lr[:, 0, :3, :3]
    yaw = np.degrees(np.unwrap(np.arctan2(R0[:, 1, 0], R0[:, 0, 0])))
    fast = np.abs(np.gradient(yaw)) > 12.0 * sfps / tfps
    spin = fast.copy()
    pad = int(round(2 * tfps / sfps))
    for i in np.flatnonzero(fast):
        spin[max(0, i - pad):i + pad + 1] = True
    Ls = bse.smooth_legal(Lr, SMOOTH_SIGMA_24 * tfps / sfps)
    Ls[spin] = Lr[spin]

    # ---- key frames
    autok = {k['id']: k for k in auto['keyframes']}
    keys = []
    for k in art['keyframes']:
        also = set((autok.get(k.get('auto_id') or '', {}).get('detail') or {}).get('also_types', []))
        keys.append({'id': k['id'], 'type': k['type'], 'origin': k['origin'], 'note': k.get('note', ''),
                     'video_frame': int(k['frame']), 'frame': vf2mf(k['frame'], sfps, tfps), 'also': sorted(also),
                     'reason': autok.get(k.get('auto_id') or '', {}).get('reason', '')})
    struct_frames = sorted({k['frame'] for k in keys})
    if len(struct_frames) != len(keys):
        raise SystemExit('two artist keyframes map to the same Max frame; move one of them in the web page')
    bd_frames = []
    if a.breakdowns:
        lad = auto.get('breakdown_ladder') or {}
        for b in (lad.get('breakdowns') or [])[:a.breakdowns]:
            mf = vf2mf(b['frame'], sfps, tfps)
            if mf in struct_frames or mf in bd_frames or b['frame'] in [k['video_frame'] for k in keys]:
                continue
            bd_frames.append(mf)
            keys.append({'id': f"b{b['rank']:03d}", 'type': 'breakdown', 'origin': 'ladder_suggested', 'note': '',
                         'video_frame': int(b['frame']), 'frame': mf, 'also': [], 'reason': b.get('reason', '')})
    keys.sort(key=lambda k: k['frame'])
    K = np.array([k['frame'] for k in keys])
    assert len(set(K.tolist())) == len(K)
    assert K[0] == 0 and K[-1] == T - 1, 'first/last keyframe must be the clip start/end'

    # key poses: denoised for slow keys, raw for peaks and the spin
    KP = Lr.copy()
    for k in keys:
        smooth = k['type'] in SMOOTH_TYPES and not (set(k['also']) & KEEP_RAW) and not spin[k['frame']]
        k['pose'] = 'denoised' if smooth else 'raw'
        if smooth:
            KP[k['frame']] = Ls[k['frame']]

    # ---- plants = contact segments the artist approved (snapped to the artist's key frames)
    min_plant = int(auto.get('params', {}).get('min_plant', 6))
    merge = int(auto.get('params', {}).get('merge_frames', 2))
    art_vf = sorted(k['video_frame'] for k in keys if k['origin'] != 'ladder_suggested')
    snap_log = []

    def snap(v):
        near = [x for x in art_vf if abs(x - v) <= merge]
        if not near:
            snap_log.append(f'contact frame {v} has no artist keyframe within ±{merge}: kept as detected')
            return v
        return min(near, key=lambda x: abs(x - v))

    plants = {s: [] for s in 'LR'}
    for s in 'LR':
        for g in ct['feet'][s]:
            if g['end'] - g['start'] + 1 < min_plant:
                continue
            av, bv = snap(g['start']), snap(g['end'])
            pa = 0 if g['start'] == 0 else vf2mf(av, sfps, tfps)
            pb = T - 1 if g['end'] == vframes - 1 else vf2mf(bv, sfps, tfps)
            if pb - pa < 2:
                continue
            want = np.array([g['obs_x_cm'], g['obs_y_cm'] if g.get('obs_y_cm') is not None else np.nan], float)
            plants[s].append({'frames': [int(pa), int(pb)], 'video_frames': [int(av), int(bv)], 'want': want})

    # ---- interpolated (unlowered) motion for these keys
    B2 = dict(B)
    B2['Ld'] = KP
    P = bse.Proxy(B2)
    keyed = [n for p in bse.PARTS for n in bse.PARTS[p]]
    pose_u = P.pose({n: K for n in keyed})
    pel = ix['Bip001 Pelvis']

    def hips_of(pose, s):
        return (pose[:, pel] @ np.r_[off('Bip001 Pelvis', f'Bip001 {s} Thigh'), 1.0])[:, :3]
    hips_u = {s: hips_of(pose_u, s) for s in 'LR'}

    # Hip-to-ankle distance for a knee bend of k degrees. The legs are short (7.7 + 6.7 cm), so the
    # natural-stance band is narrow: 12 deg -> 14.38 cm, 35 deg -> 13.79 cm, straight 14.46 cm.
    def dist_at(k):
        return float(np.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * np.cos(np.radians(k))))
    d_soft_hi = dm                      # knee >= MIN_KNEE
    d_soft_lo = dist_at(a.max_knee)     # knee <= MAX_KNEE
    d_hard = dist_at(max(a.min_knee - 6.0, 3.0))   # interpolated frames: planted knee never straighter than this

    def raise_band(h, anc_xy):
        """Per frame [lo, hi] body raise (cm, + = up) that keeps the knee in [MIN_KNEE, MAX_KNEE]; hi_hard = reach limit.
        lo/hi are nan when the anchor is horizontally out of reach."""
        dxy2 = np.sum((h[:, :2] - anc_xy) ** 2, 1)
        v = h[:, 2] - gz
        vlo = np.sqrt(np.maximum(d_soft_lo ** 2 - dxy2, 0.0))
        vhi = np.where(dxy2 < d_soft_hi ** 2, np.sqrt(np.maximum(d_soft_hi ** 2 - dxy2, 0.0)), np.nan)
        vhard = np.where(dxy2 < d_hard ** 2, np.sqrt(np.maximum(d_hard ** 2 - dxy2, 0.0)), np.nan)
        return vlo - v, vhi - v, vhard - v

    def height_cost(h, anc_xy):
        """How far (cm) the body must move off the video height to give this anchor a natural knee bend."""
        lo, hi, _ = raise_band(h, anc_xy)
        if np.isnan(hi).any():
            return np.inf
        z = np.clip(0.0, lo, hi)          # stay at the video height when it is already natural
        z = np.where(lo > hi, hi, z)
        return float(np.max(np.abs(z)))

    # ---- anchors: natural knee with the least change of body height, pulled towards the observed foot
    Rr = {s: ref[f'Bip001 {s} Foot'][:3, :3] for s in 'LR'}
    for s in 'LR':
        n = f'Bip001 {s} Foot'
        for pl in plants[s]:
            a0, b0 = pl['frames']
            h = hips_u[s][a0:b0 + 1]
            solved = np.median(B['Td'][a0:b0 + 1, ix[n], :2, 3], 0)
            want = np.where(np.isnan(pl['want']), solved, pl['want'])
            xs = np.arange(h[:, 0].min() - dm, h[:, 0].max() + dm, 0.25)
            ys = np.arange(h[:, 1].min() - dm, h[:, 1].max() + dm, 0.25)
            best = None
            for x in xs:
                for y in ys:
                    hc = height_cost(h, np.array([x, y]))
                    if not np.isfinite(hc):
                        continue
                    dist = float(np.hypot(x - want[0], y - want[1]))
                    # closest to the observed foot among anchors that keep the body within --height-cap of the video;
                    # if none does, the least height change (tie: closest)
                    c = dist if hc <= a.height_cap else 1e3 + hc + 1e-3 * dist
                    if best is None or c < best[0]:
                        best = (c, hc, x, y)
            if best is None:
                raise SystemExit(f'{s} plant {a0}-{b0}: no floor point is within leg reach for every frame')
            D = B['Td'][a0:b0 + 1, ix[n], :3, :3] @ Rr[s].T
            yw = float(np.median(np.arctan2(D[:, 1, 0], D[:, 0, 0])))
            A = np.eye(4)
            A[:3, :3] = rotz(yw) @ Rr[s]
            A[:3, 3] = [best[2], best[3], gz]
            pl.update(anchor=A, anchor_xy=[best[2], best[3]], height_change_dense=best[1],
                      from_observed_cm=float(np.hypot(best[2] - want[0], best[3] - want[1])),
                      from_solved_cm=float(np.hypot(best[2] - solved[0], best[3] - solved[1])), yaw_deg=float(np.degrees(yw)))

    # ---- body height on the COM keys (stance prior): with a foot planted, the body sits where the
    # planted legs have a natural bend; keys without planted feet blend between their neighbours.
    z = np.full(len(K), np.nan)
    for i, f in enumerate(K):
        los, his, hards = [], [], []
        for s in 'LR':
            for pl in plants[s]:
                if pl['frames'][0] <= f <= pl['frames'][1]:
                    lo, hi, hd = (x[0] for x in raise_band(hips_u[s][f:f + 1], np.array(pl['anchor_xy'])))
                    los.append(lo), his.append(hi), hards.append(hd)
        if not his:
            continue
        L_, H_ = max(los), min(his)
        zi = float(np.clip(0.0, L_, H_)) if L_ <= H_ else H_    # conflict: keep every planted knee >= MIN_KNEE
        zi = min(zi, a.max_raise)
        z[i] = zi
    known = np.flatnonzero(~np.isnan(z))
    z = np.interp(np.arange(len(K)), known, z[known]) if len(known) else np.zeros(len(K))
    # interpolated frames inside plants must never over-extend: push the neighbouring keys down
    for it in range(60):
        Dz = cr_eval(K, z, F)
        excess = np.zeros(len(K))
        worst = 0.0
        for s in 'LR':
            for pl in plants[s]:
                a0, b0 = pl['frames']
                h = hips_u[s][a0:b0 + 1].copy()
                h[:, 2] += Dz[a0:b0 + 1]
                _, _, hd = raise_band(h, np.array(pl['anchor_xy']))
                over = np.where(np.isnan(hd), 5.0, np.maximum(-hd, 0.0))   # cm above the reach limit
                for j in np.flatnonzero(over > 0.01):
                    f = a0 + j
                    i = int(np.searchsorted(K, f, side='right') - 1)
                    for ii in ([i] if K[i] == f else [i, min(i + 1, len(K) - 1)]):
                        excess[ii] = max(excess[ii], over[j] * 1.05)
                worst = max(worst, float(over.max()) if len(over) else 0.0)
        if worst <= 0.01:
            break
        z -= excess
    Dd = -cr_eval(K, z, F)          # + = body lowered (as in build_spline_edit), - = raised
    drop_iters = it

    # ---- final poses: lowered key poses, reference (denoised, lowered by the interpolated drop)
    shift = np.zeros_like(Lr)
    KPL = KP.copy()
    Lref = Ls.copy()
    for i in range(len(NODES)):
        if i in feet_idx:
            continue
        KPL[:, i, 2, 3] -= Dd     # only key frames are used from KPL
        Lref[:, i, 2, 3] -= Dd
    del shift
    for i in range(len(NODES)):
        if i not in feet_idx:
            Lref[K, i] = KPL[K, i]
    hips_l = {s: hips_u[s] - np.c_[np.zeros((T, 2)), Dd] for s in 'LR'}

    def free_foot(s, f):
        n = f'Bip001 {s} Foot'
        M = np.eye(4)
        D = B['Td'][f, ix[n], :3, :3] @ Rr[s].T
        M[:3, :3] = rotz(np.arctan2(D[1, 0], D[0, 0])) @ Rr[s]
        p = B['Td'][f, ix[n], :3, 3].copy()
        p[2] = float(np.clip(p[2], gz + FREE_LIFT[0], gz + FREE_LIFT[2]))
        if p[2] < gz + FREE_LIFT[1]:
            p[2] = gz + FREE_LIFT[1]
        hip = hips_l[s][f]
        v = p - hip
        if np.linalg.norm(v) > dm:
            p = hip + v / np.linalg.norm(v) * dm
            if p[2] < gz + FREE_LIFT[0]:
                p[2] = gz + FREE_LIFT[0]
                r = np.sqrt(max(dm * dm - (hip[2] - gz) ** 2, 0.0))
                xy = p[:2] - hip[:2]
                if np.linalg.norm(xy) > r:
                    p[:2] = hip[:2] + xy / max(np.linalg.norm(xy), 1e-9) * r
        M[:3, 3] = p
        return M, float(np.linalg.norm(p - B['Td'][f, ix[n], :3, 3]))

    feet_plan, knees = {}, {}
    for s in 'LR':
        n = f'Bip001 {s} Foot'
        planted = np.zeros(T, bool)
        fk = []
        for pl in plants[s]:
            a0, b0 = pl['frames']
            planted[a0:b0 + 1] = True
            Lref[a0:b0 + 1, ix[n]] = pl['anchor']
            fk.append({'frame': a0, 'type': 'plant_start', 'rows': bse.to_rows(pl['anchor'])})
            fk.append({'frame': b0, 'type': 'plant_end', 'rows': bse.to_rows(pl['anchor'])})
        for f in range(T):
            if not planted[f]:
                Lref[f, ix[n]] = free_foot(s, f)[0]
        for k in keys:
            f = k['frame']
            if not planted[f]:
                M, moved = free_foot(s, f)
                fk.append({'frame': f, 'type': 'free', 'rows': bse.to_rows(M)})
                k.setdefault('free_foot_moved_cm', {})[s] = round(moved, 2)
        # approach / lift-off keys: 2 frames before touchdown and after lift-off the foot is above its anchor
        # (a free key interpolates in body space; without this the swing foot cuts through the floor on the way in)
        taken = {r['frame'] for r in fk}
        for pl in plants[s]:
            a0, b0 = pl['frames']
            for f, side in ((a0 - APPROACH, 'in'), (b0 + APPROACH, 'out')):
                if f < 0 or f >= T or planted[f] or any(abs(f - x) < 2 for x in taken):
                    continue
                M = pl['anchor'].copy()
                M[2, 3] = gz + FREE_LIFT[1]
                hip = hips_l[s][f]
                v = M[:3, 3] - hip
                if np.linalg.norm(v) > dm:
                    M[:3, 3] = hip + v / np.linalg.norm(v) * dm
                    M[2, 3] = max(M[2, 3], gz + FREE_LIFT[0])
                fk.append({'frame': int(f), 'type': 'free', 'rows': bse.to_rows(M), 'role': 'approach' if side == 'in' else 'lift'})
                taken.add(f)
        fk.sort(key=lambda r: (r['frame'], r['type'] != 'plant_end'))
        runs, st = [], None
        for f in range(T):
            if not planted[f] and st is None:
                st = f
            if (planted[f] or f == T - 1) and st is not None:
                runs.append([st, f - 1 if planted[f] else f])
                st = None
        feet_plan[s] = {'plants': [pl['frames'] for pl in plants[s]], 'free_runs': runs, 'keys': fk}
        flex = np.full(T, np.nan)
        for pl in plants[s]:
            a0, b0 = pl['frames']
            flex[a0:b0 + 1] = bse.knee_flex(np.linalg.norm(hips_l[s][a0:b0 + 1] - pl['anchor'][:3, 3], axis=1), l1, l2)
        knees[s] = flex
    bse.add_plant_holds(feet_plan, K.tolist())

    # ---- packet (same schema as build_spline_edit.py)
    def local(Lx, n):
        i = ix[n]
        return Lx[:, i] if bse.PARENT[n] is None else np.linalg.inv(Lx[:, ix[bse.PARENT[n]]]) @ Lx[:, i]

    def const_local(n):
        if bse.PARENT[n] is None:
            return False
        Lq = local(Ls, n)[:, :3, :3]
        c = np.clip((np.trace(np.swapaxes(Lq[:1], 1, 2) @ Lq, axis1=1, axis2=2) - 1) / 2, -1, 1)
        return bool(np.degrees(np.arccos(c)).max() < 0.01)

    sf = sorted(set(struct_frames))
    out.mkdir(parents=True, exist_ok=True)
    packet = {'schema': 'v2r.max.spline_edit/1', 'variant': 'keyframe-first (shared whole-body keys)',
              'target_scene': str(bse.BASE / 'work/max_editable_20261109/base_scene.max'),
              'source_motion': str(src / 'motion_temporal.npz'),
              'source_sha256': hashlib.sha256((src / 'motion_temporal.npz').read_bytes()).hexdigest(),
              'artist_file': a.artist, 'artist_revision': art.get('revision'), 'artist_author': art.get('author'),
              'fps': a.fps, 'frames': [int(F[0]), int(F[-1])], 'unit': 'cm',
              'matrix_convention': 'Max matrix3 rows (X,Y,Z,translation)', 'parent': bse.PARENT, 'parts': {},
              'feet': {s: {'node': f'Bip001 {s} Foot', **feet_plan[s],
                           'key_semantics': 'plant_start: sliding (new world IK reference); plant_end/plant_hold: planted (join); free: free key'}
                       for s in 'LR'}}
    for p, names in bse.PARTS.items():
        locs = {n: local(KPL, n) for n in names}
        packet['parts'][p] = {'nodes': names, 'poses': sf, 'breakdowns': sorted(bd_frames), 'breakdowns_per_interval': [],
                              'constant_local': {n: const_local(n) for n in names},
                              'keys': {n: {str(int(k)): {'world': bse.to_rows(KPL[k, ix[n]]), 'local': bse.to_rows(locs[n][k])}
                                           for k in K} for n in names}}

    # ---- offline checks: proxy interpolation of the lowered keys vs the lowered reference
    B3 = dict(B)
    B3['Ld'] = KPL
    pose_l = bse.Proxy(B3).pose({n: K for n in keyed})
    ranked = ['COM', 'Torso', 'L Arm', 'R Arm']
    E = np.stack([np.linalg.norm(bse.markers(pose_l, bse.PARTS[p], B) - bse.markers(Lref, bse.PARTS[p], B), axis=-1).max(1)
                  for p in ranked], 1).max(1)
    ns = ~spin
    kp_ref = bse.ride(Lref, B['kp_node'], B['kp_local'])
    kp_key = bse.ride(KPL, B['kp_node'], B['kp_local'])
    obs = B['kp_obs'] - np.c_[np.zeros((T, 2)), Dd][:, None, :]
    names_kp = B['kp_names']
    body_kp = [j for j, n in enumerate(names_kp) if 'Foot' not in n]

    # ---- per-key report and problem flags
    rd = np.load(src / 'reading.npz')
    kpn = [k['name'] for k in json.loads((src / 'rig.json').read_text(encoding='utf8'))['keypoints']]
    conf = rd['conf_fit']
    arm_kp = {s: [j for j, n in enumerate(names_kp) if n.startswith(f'Bip001 {s} ') and ('Hand' in n or 'Forearm' in n)] for s in 'LR'}
    flags_total = {}
    for k in keys:
        f, vf = k['frame'], k['video_frame']
        flags = []
        e_arm = {s: float(np.linalg.norm(kp_key[f, arm_kp[s]] - obs[f, arm_kp[s]], axis=-1).max()) for s in 'LR'}
        for s in 'LR':
            nm = '左' if s == 'L' else '右'
            if e_arm[s] > 4.0:
                flags.append(f'{nm}臂姿势与视频读数相差 {e_arm[s]:.1f}cm（Biped 肘部只能单向弯，或读数有误）')
            loc = np.linalg.inv(KPL[f, ix[f'Bip001 {s} UpperArm']]) @ KPL[f, ix[f'Bip001 {s} Forearm']]
            ref_l = np.linalg.inv(ref[f'Bip001 {s} UpperArm']) @ ref[f'Bip001 {s} Forearm']
            rel = ref_l[:3, :3].T @ loc[:3, :3]
            theta = np.degrees(np.arctan2(rel[1, 0], rel[0, 0]))
            if theta > -3.0:
                flags.append(f'{nm}肘伸直到极限（视频里肘部弯向 Biped 转不过去的方向）')
            js = [kpn.index(f'Bip001 {s} Hand'), kpn.index(f'Bip001 {s} Forearm')]
            c = float(conf[max(0, vf - 1):vf + 2][:, :, js].mean())
            if c < 0.5:
                flags.append(f'{nm}手读数置信度低（{c:.2f}），姿势可能不准')
        if spin[f]:
            flags.append('快速转圈段：每约 90° 一个键，手臂姿势变化快，建议人工检查')
        for s, mv in (k.get('free_foot_moved_cm') or {}).items():
            if mv > 3.0:
                flags.append(f'{"左" if s == "L" else "右"}脚离地键为了贴地或够得着，比视频读数挪了 {mv:.1f}cm')
        for s in 'LR':
            for pl in plants[s]:
                if pl['frames'][0] <= f <= pl['frames'][1] and pl['from_observed_cm'] > 5.0 and f in (pl['frames'][0], min(K[(K >= pl['frames'][0]) & (K <= pl['frames'][1])])):
                    flags.append(f'{"左" if s == "L" else "右"}脚着地点比视频读数偏 {pl["from_observed_cm"]:.0f}cm（腿长只有 {l1 + l2:.1f}cm，视频里的落脚点在保持身体高度时够不着；读数也可能落在脚尖上）')
        if Dd[f] > 2.0:
            flags.append(f'身体比视频低了 {Dd[f]:.1f}cm（为了让着地的腿够得着）')
        elif Dd[f] < -2.0:
            flags.append(f'身体比视频高了 {-Dd[f]:.1f}cm（视频里的重心高度会让膝盖蹲太深）')
        planted_here = {s: bool(any(pl['frames'][0] <= f <= pl['frames'][1] for pl in plants[s])) for s in 'LR'}
        k.update(body_lowered_cm=round(float(Dd[f]), 2), arm_error_cm={s: round(v, 2) for s, v in e_arm.items()},
                 planted=planted_here, knee_flex_deg={s: (round(float(knees[s][f]), 1) if planted_here[s] else None) for s in 'LR'},
                 flags=flags)
        for fl in flags:
            flags_total[fl.split('（')[0]] = flags_total.get(fl.split('（')[0], 0) + 1
    planted_flex = np.concatenate([knees[s][~np.isnan(knees[s])] for s in 'LR'])
    info = {
        'keys': {'artist': len(sf), 'breakdowns': len(bd_frames), 'total_whole_body': int(len(K)),
                 'feet': {s: len(feet_plan[s]['keys']) for s in 'LR'}},
        'mapping': f'Max frame = floor(video frame * {tfps:g}/{sfps:g} + 0.5)',
        'knee_band_deg': [a.min_knee, a.max_knee], 'height_cap_cm': a.height_cap, 'max_raise_cm': a.max_raise,
        'body_height_change_cm': {'lowered_max': float(max(Dd.max(), 0.0)), 'raised_max': float(max(-Dd.min(), 0.0)), 'mean_abs': float(np.abs(Dd).mean()), 'iterations': drop_iters},
        'knee_flex_planted_deg': {'min': float(planted_flex.min()), 'median': float(np.median(planted_flex)), 'max': float(planted_flex.max())},
        'plants': {s: [{'frames': pl['frames'], 'video_frames': pl['video_frames'], 'anchor_xy_cm': [round(v, 2) for v in pl['anchor_xy']],
                        'anchor_from_observed_cm': round(pl['from_observed_cm'], 2), 'anchor_from_solved_cm': round(pl['from_solved_cm'], 2),
                        'yaw_deg': round(pl['yaw_deg'], 1)} for pl in plants[s]] for s in 'LR'},
        'contact_snap_log': snap_log, 'spin_frames': [int(x) for x in np.flatnonzero(spin)[[0, -1]]] if spin.any() else None,
        'proxy_interp_vs_reference_cm': {'non_spin_max': float(E[ns].max()), 'non_spin_p95': float(np.percentile(E[ns], 95)),
                                         'spin_max': float(E[~ns].max()) if (~ns).any() else 0.0},
        'key_pose_vs_video_body_kp_cm': {'max': float(np.linalg.norm(kp_key[K][:, body_kp] - obs[K][:, body_kp], axis=-1).max()),
                                         'p95': float(np.percentile(np.linalg.norm(kp_key[K][:, body_kp] - obs[K][:, body_kp], axis=-1), 95))},
        'flag_counts': flags_total,
        'leg_lengths_cm': [l1, l2],
    }
    packet['info'] = info
    (out / 'edit_packet.json').write_text(json.dumps(packet, ensure_ascii=False), encoding='utf8')
    np.savez(out / 'legal_dense.npz', nodes=np.array(NODES), legal=Lref, target=B['Td'], kp_src=B['kp_obs'],
             kp_node=B['kp_node'], kp_local=B['kp_local'], kp_names=np.array(B['kp_names']), dz=float(Dd.max()),
             dzs=Dd, frames=F)
    report = {'source': str(src), 'artist': {'file': a.artist, 'revision': art.get('revision'), 'author': art.get('author'),
                                             'reviewed': art.get('reviewed')}, 'info': info,
              'keys': [{k2: v for k2, v in k.items() if k2 not in ('also',)} for k in keys]}
    (out / 'key_poses.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf8')
    print(json.dumps(info, ensure_ascii=False, indent=1))
    print('flagged keys:', [(k['frame'], k['type'], k['flags']) for k in keys if k['flags']][:40])
    print('wrote', out / 'edit_packet.json')


if __name__ == '__main__':
    main()
