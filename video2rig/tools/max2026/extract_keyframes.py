"""Candidate keyframes from a fitted clip (system Python + NumPy; no Max, no server).

python extract_keyframes.py <source_dir> [--video ladingwu.mp4] [--out keyframes_auto.json]
                            [--contacts contacts_auto.json] [--max-keys 0] [--no-ladder]

source_dir holds rig.json, motion_temporal.npz and reading.npz (see work/maxkeys/m11101_tex_ladingwu).
Output follows video2rig/docs/关键帧数据与网页接口约定_20261111.md (v2r.keyframes/1).

What is detected (definitions: docs/关键帧优先方案_20261111.md section 2):
  start / end          clip edges
  contact              foot touch / lift, from the observation-based plants in contacts_auto.json
                       (plants shorter than MIN_PLANT frames are treated as shuffling, not contacts)
  hold_start/hold_end  whole-body activity stays below HOLD_SPEED for >= HOLD_MIN frames
  extreme              prominent peaks / valleys of
                         positions: hand reach, hand height, COM height, body yaw sway
                         rotations: head vs chest (turn / nod / tilt), chest vs pelvis (turn / bend / side-bend),
                                    body lean, wrist twist / bend  (a nod or a wrist turn barely moves any joint)
                       found in scale space: an extremum must stand out at the COARSE scale (so reader jitter
                       cannot create it) and its frame is then tracked down to the FINE scale (so smoothing does
                       not shave the peak or move the key off it). After Halit & Capin 2011, multiscale motion saliency.
  impact               a fast hand motion that stops within a few frames (spin frames excluded)
  technical            during a fast spin, one key per ~SPIN_STEP degrees so interpolation turns the right way
NOT detected yet: anticipation, rebound.
breakdown: not a candidate in `keyframes`; see breakdown_ladder.py, embedded as `breakdown_ladder`
           (nested, ranked list added one at a time on top of the structural keys).
All thresholds are starting values to be calibrated against artist-labelled keyframes.
"""
from pathlib import Path
import argparse, hashlib, json, sys
from datetime import datetime
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GENERATOR = 'extract_keyframes.py 0.2'
PRIORITY = {'start': 100, 'end': 100, 'technical': 90, 'contact': 80, 'impact': 75, 'hold_start': 60, 'hold_end': 60,
            'extreme': 50, 'anticipation': 40, 'rebound': 40, 'breakdown': 10, 'custom': 0}
TIER = {'contact': 2, 'extreme': 2, 'hold_start': 2, 'hold_end': 2, 'anticipation': 1, 'rebound': 1}  # pruning order
PROTECTED = {'start', 'end', 'technical', 'impact'}
EXEMPT_GAP = {'start', 'end', 'technical', 'impact', 'contact'}

P = dict(
    min_gap_frames=4,       # between ordinary keyframes
    merge_frames=2,         # candidates this close become one keyframe
    min_plant=6,            # frames; shorter plants are not contacts
    hold_speed=8.0,         # cm/s, whole-body activity
    hold_min=5,             # frames
    hold_bridge=2,          # frames; short activity blips inside a hold are bridged
    reach_prominence_cm=8.0,
    height_prominence_cm=10.0,
    com_prominence_cm=1.2,
    yaw_prominence_deg=25.0,
    impact_peak_cm_s=90.0,
    impact_stop_ratio=0.3,
    impact_window=6,
    spin_rate_deg=12.0,     # deg per frame
    spin_step_deg=90.0,
    # scale space for extrema, Gaussian sigma in frames at 24 fps (scaled with the clip fps), fine -> coarse;
    # a 3-frame median removes single-frame spikes first
    scales_24fps=[1.0, 2.0, 3.0],
    # rotation extrema thresholds (deg, prominence at the coarse scale)
    head_turn_deg=15.0, head_nod_deg=10.0, head_tilt_deg=10.0,
    chest_turn_deg=15.0, chest_bend_deg=10.0, chest_side_deg=10.0,
    body_lean_deg=10.0,
    wrist_deg=25.0,          # wrist readings are noisy (jitter up to ~27 deg on ladingwu)
)


# ----------------------------------------------------------------------------- signal helpers
def gs(x, s):
    r = int(np.ceil(3 * s))
    w = np.exp(-0.5 * (np.arange(-r, r + 1) / s) ** 2)
    w /= w.sum()
    pad = np.concatenate([x[r:0:-1], x, x[-2:-r - 2:-1]], 0)
    return sum(w[i] * pad[i:i + len(x)] for i in range(2 * r + 1))


def med(x, k=5):
    h = k // 2
    pad = np.concatenate([x[:1].repeat(h, 0), x, x[-1:].repeat(h, 0)])
    return np.median(np.stack([pad[i:i + len(x)] for i in range(k)]), 0)


def smooth(x):
    return gs(med(x, 5), 1.2)


def speed(p, fps):
    return np.r_[0.0, np.linalg.norm(np.diff(p, axis=0), axis=1)] * fps


def prominent(x, thr):
    """[(index, prominence, 'max'|'min')] for interior local extrema with prominence >= thr."""
    out = []
    for sign, kind in ((1, 'max'), (-1, 'min')):
        y = sign * x
        for i in range(1, len(y) - 1):
            if not (y[i] >= y[i - 1] and y[i] > y[i + 1]):
                continue
            j, lm = i - 1, y[i]
            while j >= 0 and y[j] <= y[i]:
                lm = min(lm, y[j])
                j -= 1
            j, rm = i + 1, y[i]
            while j < len(y) and y[j] <= y[i]:
                rm = min(rm, y[j])
                j += 1
            prom = y[i] - max(lm, rm)
            if prom >= thr:
                out.append((i, float(prom), kind))
    return sorted(out)


def scale_extrema(x, thr, scales, coarse_frac=0.5):
    """Extrema that persist across scales (fine -> coarse sigmas, frames).

    1. median-3 removes single-frame spikes; Gaussian blur at every sigma in `scales`.
    2. Existence is decided at the COARSEST scale (prominence >= coarse_frac * thr): reader jitter does not
       survive there, so it cannot create an extremum.
    3. The frame is tracked down scale by scale (search +-(ceil(sigma)+1) around the coarser position), so the
       key lands on the real peak and is not moved by the blur.
    4. Size is judged at the FINEST scale (prominence >= thr), so blurring does not shave real peaks away.
    Returns [dict(frame, coarse_frame, kind, prominence, coarse_prominence, value)] sorted by frame."""
    base = med(np.asarray(x, float), 3)
    X = [gs(base, s) for s in scales]
    fine = {(i, k): p for i, p, k in prominent(X[0], 0.0)}
    out = {}
    for i, p, k in prominent(X[-1], coarse_frac * thr):
        pos = i
        for lvl in range(len(scales) - 2, -1, -1):
            w = int(np.ceil(scales[lvl + 1])) + 1
            a, b = max(0, pos - w), min(len(base), pos + w + 1)
            seg = X[lvl][a:b]
            pos = a + int(np.argmax(seg) if k == 'max' else np.argmin(seg))
        pf = fine.get((pos, k), p)  # tracked onto a window edge (no fine extremum there): keep the coarse value
        if pf < thr:
            continue
        if pos not in out or pf > out[pos]['prominence']:
            out[pos] = dict(frame=int(pos), coarse_frame=int(i), kind=k, prominence=float(pf),
                            coarse_prominence=float(p), value=float(X[0][pos]))
    return [out[f] for f in sorted(out)]


def axis_of(spec):
    """'+Z' / '-Y' / [x, y, z] -> unit vector."""
    if isinstance(spec, str):
        v = np.zeros(3)
        v['XYZ'.index(spec[-1].upper())] = -1.0 if spec.startswith('-') else 1.0
        return v
    v = np.asarray(spec, float)
    return v / np.linalg.norm(v)


def rotation_signals(rig, mo):
    """Joint rotations that move little in position (nods, chest bends, wrist turns), in degrees.

    Relative rotation child-vs-parent is expressed in the parent's rest-aligned frame and split along the
    character's up / forward / left axes (turn / tilt / bend). Wrists are split along the forearm's own
    rest axes (twist about the bone, bend about the two others). Body lean is the angle of the pelvis'
    up vector towards the current forward / left direction."""
    from plan_spline_keys import Rig, matq
    from build_spline_edit import logmap
    R = Rig(rig, mo, float(mo['fps']))
    G = mo['G'].astype(float)
    S = R.fk(mo['quat'], matq(G[:, :3, :3]), np.einsum('tij,j->ti', G[:, :3, :3], R.root_rest) + G[:, :3, 3])
    Sr = R.W @ R.M

    def unit_rot(M):
        return M / np.linalg.norm(M[..., :, 0], axis=-1)[..., None, None]

    def delta(n):  # world rotation relative to rest, (T,3,3)
        i = R.names.index(n)
        return unit_rot(S[:, i, :3, :3]) @ unit_rot(Sr[i][:3, :3]).T

    def rel(c, p):  # rotation vector (deg) of c relative to p, in p's rest-aligned frame
        return np.degrees(logmap(np.swapaxes(delta(p), 1, 2) @ delta(c)))

    up = axis_of(rig.get('up', '+Z'))
    fwd = axis_of(rig.get('forward', [0, -1, 0]))
    left = np.cross(up, fwd)
    out = {}
    v = rel('Bip001 Head', 'Bip001 Spine1')
    out['head_turn'], out['head_nod'], out['head_tilt'] = v @ up, v @ left, v @ fwd
    v = rel('Bip001 Spine1', 'Bip001 Pelvis')
    out['chest_turn'], out['chest_bend'], out['chest_side'] = v @ up, v @ left, v @ fwd
    D = delta('Bip001 Pelvis')
    u = D @ up
    f = D @ fwd
    f = f - (f @ up)[:, None] * up
    f /= np.linalg.norm(f, axis=1, keepdims=True)
    l = np.cross(up, f)
    out['lean_fwd'] = np.degrees(np.arcsin(np.clip(np.sum(u * f, 1), -1, 1)))
    out['lean_side'] = np.degrees(np.arcsin(np.clip(np.sum(u * l, 1), -1, 1)))
    for s in 'LR':
        Rf = unit_rot(Sr[R.names.index(f'Bip001 {s} Forearm')][:3, :3])
        v = rel(f'Bip001 {s} Hand', f'Bip001 {s} Forearm')
        out[f'{s}_wrist_twist'], out[f'{s}_wrist_bend1'], out[f'{s}_wrist_bend2'] = v @ Rf[:, 0], v @ Rf[:, 1], v @ Rf[:, 2]
    return out


def runs(mask):
    out, st = [], None
    for i, m in enumerate(mask):
        if m and st is None:
            st = i
        if (not m or i == len(mask) - 1) and st is not None:
            out.append([st, i if m else i - 1])
            st = None
    return out


def cand(frame, typ, parts, reason, conf, **detail):
    return dict(frame=int(frame), type=typ, parts=list(parts), reason=reason, confidence=float(np.clip(conf, 0, 1)), detail=detail)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('--video', default='ladingwu.mp4')
    ap.add_argument('--out', default=None)
    ap.add_argument('--contacts', default=None)
    ap.add_argument('--max-keys', type=int, default=0, help='0 = no budget; otherwise prune lowest-tier, lowest-confidence candidates')
    ap.add_argument('--no-ladder', action='store_true', help='skip the breakdown ladder (needs the Max bind profile)')
    ap.add_argument('--ladder-contacts', default=None, help='contacts for the Biped-legal model (default: contacts_plan.json)')
    a = ap.parse_args()
    src = Path(a.source)
    rig = json.loads((src / 'rig.json').read_text(encoding='utf8'))
    mo = np.load(src / 'motion_temporal.npz')
    rd = np.load(src / 'reading.npz')
    cpath = Path(a.contacts) if a.contacts else src / 'contacts_auto.json'
    ct = json.loads(cpath.read_text(encoding='utf8'))
    names = [k['name'] for k in rig['keypoints']]
    fps = float(mo['fps'])
    kp = mo['kp'].astype(float) * 100.0
    T = len(kp)
    vis_all = rd['conf_fit'].mean(1)  # (T, K)

    def pt(n):
        return smooth(kp[:, names.index(n)])

    def vis(n, f):
        return float(np.clip(vis_all[max(0, f - 1):f + 2, names.index(n)].mean(), 0, 1))

    S1 = pt('Bip001 Spine1')
    com = pt('Bip001')
    hands = {s: pt(f'Bip001 {s} Hand') for s in 'LR'}
    act = gs(np.mean([speed(pt(n), fps) for n in ['Bip001', 'Bip001 Neck', 'Bip001 L Hand', 'Bip001 R Hand',
                                                  'Bip001 L Forearm', 'Bip001 R Forearm']], 0), 1.0)
    G = mo['G']
    yaw = np.degrees(np.unwrap(np.arctan2(G[:, 1, 0], G[:, 0, 0])))
    yaw -= yaw[0]
    yaw_rate = np.abs(np.gradient(yaw))
    spin = yaw_rate > P['spin_rate_deg']
    for _ in range(2):
        spin = spin | np.r_[spin[1:], False] | np.r_[False, spin[:-1]]
    spin_runs = [r for r in runs(spin) if r[1] - r[0] >= 3]
    spin_mask = np.zeros(T, bool)
    for s0, s1 in spin_runs:
        spin_mask[max(0, s0 - 3):s1 + 4] = True

    C = []
    C.append(cand(0, 'start', ['body'], '片段第一帧', 1.0))
    C.append(cand(T - 1, 'end', ['body'], '片段最后一帧', 1.0))

    # ---- contacts
    for side in 'LR':
        for g in ct['feet'][side]:
            n = g['end'] - g['start'] + 1
            if n < P['min_plant']:
                continue
            base = 0.65 if n >= 12 else 0.5
            conf = base * (0.7 + 0.3 * float(g.get('depth_observed_fraction') or 0.0))
            nm = '左' if side == 'L' else '右'
            if g['start'] > 0:
                C.append(cand(g['start'], 'contact', [f'{side}_foot'], f'{nm}脚着地（观测到脚不动，持续 {n} 帧：{g["start"]}–{g["end"]}）',
                              conf, side=side, event='touch', plant=[g['start'], g['end']]))
            if g['end'] < T - 1:
                C.append(cand(g['end'], 'contact', [f'{side}_foot'], f'{nm}脚离地（脚不动的最后一帧，之前持续 {n} 帧：{g["start"]}–{g["end"]}）',
                              conf, side=side, event='lift', plant=[g['start'], g['end']]))

    # ---- holds
    low = act < P['hold_speed']
    r = runs(low)
    merged = []
    for a0, b0 in r:
        if merged and a0 - merged[-1][1] - 1 <= P['hold_bridge']:
            merged[-1][1] = b0
        else:
            merged.append([a0, b0])
    for a0, b0 in merged:
        n = b0 - a0 + 1
        if n < P['hold_min']:
            continue
        conf = 0.9 * min(1.0, n / 10.0)
        C.append(cand(a0, 'hold_start', ['body'], f'全身基本停住，持续 {n} 帧（{a0}–{b0}）', conf, hold=[a0, b0], length=n))
        C.append(cand(b0, 'hold_end', ['body'], f'停顿结束（之前停了 {n} 帧：{a0}–{b0}）', conf, hold=[a0, b0], length=n))

    # ---- extremes (scale space: exists at the coarse scale, located at the fine scale)
    scales = [s * fps / 24.0 for s in P['scales_24fps']]
    n_coarse_only = 0

    def add_extremes(series, n_for_vis, thr, parts, what, unit, digits, allow=None, signal=None, words=('到达峰值', '到达谷值'),
                     absval=False):
        nonlocal n_coarse_only
        for e in scale_extrema(series, thr, scales):
            i = e['frame']
            if i < 2 or i > T - 3 or (allow is not None and not allow[i]):
                continue
            prom = e['prominence']
            conf = 0.6 * min(1.0, prom / (2 * thr)) + 0.4 * (vis(n_for_vis, i) if n_for_vis else 0.8)
            word = words[0] if e['kind'] == 'max' else words[1]
            shift = '' if e['coarse_frame'] == i else f'；粗尺度在第 {e["coarse_frame"]} 帧，已按细尺度定位到峰顶'
            n_coarse_only += e['coarse_frame'] != i
            val = abs(e['value']) if absval else e['value']
            C.append(cand(i, 'extreme', parts,
                          f'{what}{word} {val:.{digits}f}{unit}（与前后相差 {prom:.{digits}f}{unit} 以上{shift}）',
                          conf, signal=signal or what, kind=e['kind'], value=e['value'], prominence=prom,
                          coarse_frame=e['coarse_frame'], coarse_prominence=e['coarse_prominence']))

    raw = {n: kp[:, names.index(n)] for n in ['Bip001', 'Bip001 Spine1', 'Bip001 L Hand', 'Bip001 R Hand']}
    for s in 'LR':
        nm = '左' if s == 'L' else '右'
        reach = np.linalg.norm(raw[f'Bip001 {s} Hand'] - raw['Bip001 Spine1'], axis=1)
        height = raw[f'Bip001 {s} Hand'][:, 2] - raw['Bip001 Spine1'][:, 2]
        add_extremes(reach, f'Bip001 {s} Hand', P['reach_prominence_cm'], [f'{s}_arm'], f'{nm}手离身体的距离', 'cm', 0, ~spin_mask,
                     signal=f'{s}_hand_reach')
        add_extremes(height, f'Bip001 {s} Hand', P['height_prominence_cm'], [f'{s}_arm'], f'{nm}手相对胸口的高度', 'cm', 0, ~spin_mask,
                     signal=f'{s}_hand_height')
    add_extremes(raw['Bip001'][:, 2], 'Bip001', P['com_prominence_cm'], ['body'], '重心高度', 'cm', 1, ~spin_mask, signal='com_height')
    s_first = spin_runs[0][0] if spin_runs else T
    pre_mask = np.zeros(T, bool)
    pre_mask[:max(3, s_first - 2)] = True
    add_extremes(yaw, None, P['yaw_prominence_deg'], ['body', 'head'], '身体朝向（相对第 0 帧）', '°', 0, pre_mask,
                 signal='yaw', words=('摆到端点', '摆到端点'))

    # rotations that barely move a joint position: nods, chest bends, wrist turns.
    # Signs (checked on the rig axes): + about up = turn to the character's left; + about left = forward / down.
    rot = rotation_signals(rig, mo)
    rot_specs = [
        ('head_turn', 'head_turn_deg', ['head'], '头（相对胸口）', ('左转到最大', '右转到最大')),
        ('head_nod', 'head_nod_deg', ['head'], '头（相对胸口）', ('低头到最低', '抬头到最高')),
        ('head_tilt', 'head_tilt_deg', ['head'], '头（相对胸口）', ('歪向一侧到最大', '歪向另一侧到最大')),
        ('chest_turn', 'chest_turn_deg', ['body'], '胸口（相对骨盆）', ('左扭到最大', '右扭到最大')),
        ('chest_bend', 'chest_bend_deg', ['body'], '胸口（相对骨盆）', ('前弯到最大', '后弯到最大')),
        ('chest_side', 'chest_side_deg', ['body'], '胸口（相对骨盆）', ('侧弯到一侧最大', '侧弯到另一侧最大')),
        ('lean_fwd', 'body_lean_deg', ['body'], '骨盆', ('前倾到最大', '后仰到最大')),
        ('lean_side', 'body_lean_deg', ['body'], '骨盆', ('侧倾到一侧最大', '侧倾到另一侧最大')),
    ]
    for s in 'LR':
        nm = '左' if s == 'L' else '右'
        rot_specs += [(f'{s}_wrist_twist', 'wrist_deg', [f'{s}_arm'], f'{nm}手腕（相对前臂）', ('旋转到一侧最大', '旋转到另一侧最大')),
                      (f'{s}_wrist_bend1', 'wrist_deg', [f'{s}_arm'], f'{nm}手腕（相对前臂）', ('弯到一侧最大', '弯到另一侧最大')),
                      (f'{s}_wrist_bend2', 'wrist_deg', [f'{s}_arm'], f'{nm}手腕（相对前臂）', ('摆到一侧最大', '摆到另一侧最大'))]
    for key, thr_key, parts, what, words in rot_specs:
        vis_node = 'Bip001 Neck' if key.startswith('head') else ('Bip001 ' + key[0] + ' Hand' if 'wrist' in key else None)
        add_extremes(rot[key], vis_node, P[thr_key], parts, what, '°', 0, ~spin_mask, signal=key, words=words, absval=True)

    # ---- impacts
    for s in 'LR':
        nm = '左' if s == 'L' else '右'
        v = gs(speed(hands[s], fps), 1.0)
        for i, prom, kind in prominent(v, 60.0):
            if kind != 'max' or v[i] < P['impact_peak_cm_s'] or spin_mask[i]:
                continue
            for j in range(i + 1, min(T - 1, i + P['impact_window']) + 1):
                if spin_mask[j]:
                    break
                if v[j] < P['impact_stop_ratio'] * v[i]:
                    C.append(cand(j, 'impact', [f'{s}_arm'], f'{nm}手以 {v[i]:.0f}cm/s 冲出后在 {j - i} 帧内停住',
                                  0.5 + 0.4 * vis(f'Bip001 {s} Hand', j), peak_speed=float(v[i]), peak_frame=int(i)))
                    break

    # ---- technical: one key per spin_step degrees during a fast spin
    for s0, s1 in spin_runs:
        a0, b0 = max(0, s0 - 1), min(T - 1, s1 + 1)
        base = yaw[a0]
        picks = {a0, b0}
        k = 1
        while True:
            target = base + np.sign(yaw[b0] - base) * P['spin_step_deg'] * k
            if abs(target - base) >= abs(yaw[b0] - base):
                break
            picks.add(int(np.argmin(np.abs(yaw[a0:b0 + 1] - target))) + a0)
            k += 1
        total = float(yaw[b0] - base)
        for f in sorted(picks):
            turned = float(yaw[f] - base)
            if f == a0:
                why = f'快速转身开始（共转 {total:+.0f}°，约每 {P["spin_step_deg"]:.0f}° 一个键，保证插值转向正确）'
            elif f == b0:
                why = f'快速转身结束（共转 {total:+.0f}°）'
            else:
                why = f'快速转身中：已转 {turned:+.0f}°（约每 {P["spin_step_deg"]:.0f}° 一个键，保证插值转向正确）'
            C.append(cand(f, 'technical', ['body', 'head'], why, 0.9, turned_deg=turned, spin=[int(s0), int(s1)], total_deg=total))

    # ---- merge, spacing
    accepted, pruned = [], []
    for c in sorted(C, key=lambda c: (-PRIORITY[c['type']], -c['confidence'], c['frame'])):
        # technical keys are a deliberate series (one per ~90 deg of spin): they never merge with each other
        near = [x for x in accepted if abs(x['frame'] - c['frame']) <= P['merge_frames']
                and not (c['type'] == 'technical' and x['type'] == 'technical')]
        if near:
            x = min(near, key=lambda x: abs(x['frame'] - c['frame']))
            if c['type'] != x['type'] and c['type'] not in x['detail'].setdefault('also_types', []):
                x['detail']['also_types'].append(c['type'])
            x['parts'] = sorted(set(x['parts']) | set(c['parts']))
            if c['reason'] not in x['reason']:
                x['reason'] += '；同时：' + c['reason']
            continue
        if c['type'] not in EXEMPT_GAP and any(abs(x['frame'] - c['frame']) < P['min_gap_frames'] for x in accepted):
            pruned.append({'frame': c['frame'], 'type': c['type'], 'reason': c['reason'], 'why': f'与更重要的关键帧相距不足 {P["min_gap_frames"]} 帧'})
            continue
        accepted.append(c)
    natural = len(accepted)
    if a.max_keys and len(accepted) > a.max_keys:
        removable = sorted([x for x in accepted if x['type'] not in PROTECTED], key=lambda x: (TIER.get(x['type'], 0), x['confidence']))
        for x in removable:
            if len(accepted) <= a.max_keys:
                break
            accepted.remove(x)
            pruned.append({'frame': x['frame'], 'type': x['type'], 'reason': x['reason'], 'why': f'超出预算 {a.max_keys}，置信度较低'})
    accepted.sort(key=lambda x: x['frame'])
    for i, x in enumerate(accepted, 1):
        x['id'] = f'k{i:03d}'
        x['confidence'] = round(x['confidence'], 2)
        x['detail'] = json.loads(json.dumps(x['detail']))
    order = ['id', 'frame', 'type', 'parts', 'reason', 'confidence', 'detail']
    keyframes = [{k: x[k] for k in order} for x in accepted]

    # ---- curves
    def plant_curve(side):
        c = np.zeros(T, int)
        for g in ct['feet'][side]:
            c[g['start']:g['end'] + 1] = 1
        return c.tolist()
    curves = {
        'body_speed': np.round(act, 1).tolist(),
        'R_hand_speed': np.round(gs(speed(hands['R'], fps), 1.0), 1).tolist(),
        'L_hand_speed': np.round(gs(speed(hands['L'], fps), 1.0), 1).tolist(),
        'R_hand_reach': np.round(np.linalg.norm(hands['R'] - S1, axis=1), 1).tolist(),
        'L_hand_reach': np.round(np.linalg.norm(hands['L'] - S1, axis=1), 1).tolist(),
        'com_height': np.round(com[:, 2], 2).tolist(),
        'L_foot_contact': plant_curve('L'),
        'R_foot_contact': plant_curve('R'),
        'spin_yaw_deg': np.round(yaw, 1).tolist(),
        'head_turn_deg': np.round(gs(med(rot['head_turn'], 3), scales[0]), 1).tolist(),
        'head_nod_deg': np.round(gs(med(rot['head_nod'], 3), scales[0]), 1).tolist(),
        'chest_turn_deg': np.round(gs(med(rot['chest_turn'], 3), scales[0]), 1).tolist(),
        'chest_bend_deg': np.round(gs(med(rot['chest_bend'], 3), scales[0]), 1).tolist(),
    }
    units = {'body_speed': 'cm/s', 'R_hand_speed': 'cm/s', 'L_hand_speed': 'cm/s', 'R_hand_reach': 'cm', 'L_hand_reach': 'cm',
             'com_height': 'cm', 'L_foot_contact': '0/1', 'R_foot_contact': '0/1', 'spin_yaw_deg': 'deg',
             'head_turn_deg': 'deg', 'head_nod_deg': 'deg', 'chest_turn_deg': 'deg', 'chest_bend_deg': 'deg'}
    labels = {'body_speed': '全身活动速度', 'R_hand_speed': '右手速度', 'L_hand_speed': '左手速度', 'R_hand_reach': '右手离胸口距离',
              'L_hand_reach': '左手离胸口距离', 'com_height': '重心高度', 'L_foot_contact': '左脚着地', 'R_foot_contact': '右脚着地',
              'spin_yaw_deg': '身体朝向（相对第0帧）', 'head_turn_deg': '头相对胸口左右转', 'head_nod_deg': '头相对胸口点头/抬头',
              'chest_turn_deg': '胸口相对骨盆扭转', 'chest_bend_deg': '胸口相对骨盆前后弯'}

    # ---- breakdown ladder on top of the structural keys (nested ranking, see breakdown_ladder.py)
    ladder = None
    if not a.no_ladder:
        try:
            from breakdown_ladder import compute as ladder_compute
            ladder, _ = ladder_compute(src, [k['frame'] for k in keyframes], a.ladder_contacts)
            ladder['structural_source'] = 'keyframes (this file)'
        except Exception as e:  # the candidate list is still useful without the ladder
            ladder = {'error': f'{type(e).__name__}: {e}'}
            print('breakdown ladder failed:', ladder['error'])

    out = {
        'schema': 'v2r.keyframes/1', 'source_video': a.video, 'fps': fps, 'frames': T,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'generator': GENERATOR,
        'params': dict(P, max_keys=a.max_keys, natural_count=natural, contacts_file=cpath.name,
                       not_detected=['anticipation', 'rebound'], pruned_candidates=pruned,
                       extreme_detection='scale space (median-3, Gaussian sigma = scales_24fps * fps/24): exists at the '
                                         'coarsest scale, frame tracked down to the finest, prominence judged at the finest',
                       extremes_relocated_from_coarse=n_coarse_only),
        'keyframes': keyframes, 'curves': curves, 'curve_units': units, 'curve_labels': labels,
        'contacts_reviewed': bool(ct.get('reviewed', False)),
        'breakdown_ladder': ladder,
    }
    path = Path(a.out) if a.out else src / 'keyframes_auto.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf8')
    from collections import Counter
    print(f'{len(keyframes)} keyframes (natural {natural}, pruned {len(pruned)}) ->', path)
    print(dict(Counter(k['type'] for k in keyframes)))
    for k in keyframes:
        print(f"  {k['id']} f{k['frame']:3d} {k['type']:11s} conf {k['confidence']:.2f} {','.join(k['parts']):14s} {k['reason'][:90]}")
    if ladder and 'error' not in ladder:
        c = ladder['error_curve']
        n = ladder['suggested_count']
        print(f"breakdown ladder: {len(ladder['breakdowns'])} ranked, suggested {n}; non-spin body error "
              f"max {c[0]['max_cm']:.1f} -> {c[n]['max_cm']:.1f}cm, p95 {c[0]['p95_cm']:.1f} -> {c[n]['p95_cm']:.1f}cm")


if __name__ == '__main__':
    main()
