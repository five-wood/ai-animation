"""Generic candidate keyframes for any skeleton (used when the Biped extractor does not apply).

Input: motion.npz (kp: T x K x 3 world metres, fps) and rig.json (size).
Output: keyframes_auto.json in the v2r.keyframes/1 schema (docs/关键帧数据与网页接口约定_20261111.md),
including a breakdown_ladder computed with linear interpolation of keypoint positions.

python -m studio.auto_keys <result_dir> <rig.json>
"""
import hashlib
import json
import os
import sys
import time

import numpy as np

GENERATOR = 'studio.auto_keys 0.1'


def smooth(x, radius=2):
    if radius <= 0 or len(x) < 3:
        return x
    k = np.ones(2 * radius + 1) / (2 * radius + 1)
    pad = np.pad(x, radius, mode='edge')
    return np.convolve(pad, k, mode='valid')


def interp_error(kp, keys):
    """Per-frame max keypoint error (metres) of linear interpolation between sorted key frames."""
    T = kp.shape[0]
    err = np.zeros(T)
    for a, b in zip(keys[:-1], keys[1:]):
        if b - a < 2:
            continue
        t = (np.arange(a + 1, b) - a) / (b - a)
        lin = kp[a][None] + (kp[b] - kp[a])[None] * t[:, None, None]
        err[a + 1:b] = np.linalg.norm(kp[a + 1:b] - lin, axis=2).max(axis=1)
    return err


def ladder(kp, structural, size, limit=30):
    keys = sorted(set(structural))
    T = kp.shape[0]
    cm = 100.0
    max_ok, p95_ok = 0.027 * size * cm, 0.013 * size * cm
    curve, out = [], []
    err = interp_error(kp, keys)

    def stats(e, n):
        return {'n': n, 'max_cm': round(float(e.max() * cm), 2), 'p95_cm': round(float(np.percentile(e, 95) * cm), 2),
                'mean_cm': round(float(e.mean() * cm), 2)}

    curve.append(stats(err, 0))
    for rank in range(1, limit + 1):
        f = int(np.argmax(err))
        if err[f] * cm < 0.3 or f in keys:
            break
        i = np.searchsorted(keys, f)
        a, b = keys[i - 1], keys[i]
        before = err[a + 1:b].max()
        keys.insert(i, f)
        err = interp_error(kp, keys)
        after = err[a + 1:b].max() if b - a > 1 else 0.0
        out.append({'rank': rank, 'frame': f, 'between': [int(a), int(b)], 'parts': ['body'],
                    'error_here_cm': round(float(before * cm), 2),
                    'interval_max_before_cm': round(float(before * cm), 2),
                    'interval_max_after_cm': round(float(after * cm), 2),
                    'clip_after': {k: v for k, v in stats(err, rank).items() if k != 'n'},
                    'reason': f'第 {a}–{b} 帧之间插值偏差最大处；补上后这段最大偏差降到 {after * cm:.1f}cm'})
        curve.append(stats(err, rank))
    suggested = len(out)
    for c in curve:
        if c['max_cm'] <= max_ok and c['p95_cm'] <= p95_ok:
            suggested = c['n']
            break
    return {'generator': GENERATOR, 'frame_rate': None, 'structural_frames': sorted(set(structural)),
            'suggested_count': suggested,
            'suggestion_rule': '最少的过渡键数，使全身最大偏差和 p95 偏差都足够小',
            'breakdowns': out, 'error_curve': curve, 'error_curve_units': 'cm', 'spin_frames': [],
            'params': {'limit': limit}}


def extract(kp, fps, size):
    T = kp.shape[0]
    size = float(size) if size and size > 0 else float(np.ptp(kp.reshape(-1, 3), axis=0).max() or 1.0)
    vel = np.zeros((T, kp.shape[1]))
    vel[1:] = np.linalg.norm(np.diff(kp, axis=0), axis=2) * fps
    speed = smooth(vel.mean(axis=1), 2)
    rel = speed / max(size, 1e-6)          # body sizes per second
    peak = float(np.percentile(rel, 95)) if T > 2 else 0.0
    low = max(0.08, 0.2 * peak)
    min_gap = max(3, int(round(fps * 0.2)))
    keys = [{'frame': 0, 'type': 'start', 'reason': '片段第一帧', 'confidence': 1.0}]
    # holds: at least ~0.25 s nearly still
    still = rel < low * 0.6
    hold_len = max(4, int(round(fps * 0.25)))
    holds = []
    t = 0
    while t < T:
        if still[t]:
            s = t
            while t < T and still[t]:
                t += 1
            if t - s >= hold_len:
                holds.append((s, t - 1))
        else:
            t += 1
    for s, e in holds:
        keys.append({'frame': int(s), 'type': 'hold_start', 'reason': f'动作停住（第 {s}–{e} 帧）', 'confidence': 0.7})
        keys.append({'frame': int(e), 'type': 'hold_end', 'reason': f'停顿结束，重新开始动（第 {s}–{e} 帧）', 'confidence': 0.6})
    # extremes: local speed minima between two faster phases (the motion reverses or arrives)
    for t in range(2, T - 2):
        if rel[t] <= rel[t - 1] and rel[t] < rel[t + 1] and rel[t] < low:
            left = rel[max(0, t - 3 * min_gap):t].max()
            right = rel[t + 1:t + 1 + 3 * min_gap].max()
            if min(left, right) > max(rel[t] * 1.8, low * 0.8):
                conf = float(np.clip(1 - rel[t] / max(min(left, right), 1e-6), 0.3, 0.95))
                keys.append({'frame': t, 'type': 'extreme', 'reason': '动作到位、即将反向（全身速度的低谷）', 'confidence': round(conf, 2)})
    # impacts: fast motion that stops within a few frames
    acc = np.zeros(T)
    acc[1:] = np.diff(speed)
    for t in range(2, T - 2):
        drop = rel[max(0, t - min_gap):t].max() - rel[t]
        if drop > 0.6 * peak and acc[t] < 0 and acc[t + 1] >= acc[t] and rel[t] < 0.5 * peak:
            keys.append({'frame': t, 'type': 'impact', 'reason': '快速动作在几帧内急停', 'confidence': 0.55})
    keys.append({'frame': T - 1, 'type': 'end', 'reason': '片段最后一帧', 'confidence': 1.0})
    # keep one key per neighbourhood, priority start/end > hold > impact > extreme
    prio = {'start': 0, 'end': 0, 'hold_start': 1, 'hold_end': 1, 'impact': 2, 'extreme': 3}
    keys.sort(key=lambda k: (prio[k['type']], -k['confidence']))
    chosen = []
    for k in keys:
        gap = 1 if k['type'] in ('start', 'end') else min_gap
        if all(abs(k['frame'] - c['frame']) >= gap for c in chosen):
            chosen.append(k)
    chosen.sort(key=lambda k: k['frame'])
    out = []
    for i, k in enumerate(chosen):
        out.append({'id': f'k{i + 1:03d}', 'frame': k['frame'], 'type': k['type'], 'parts': ['body'],
                    'reason': k['reason'], 'confidence': k['confidence'], 'detail': {}})
    lad = ladder(kp, [k['frame'] for k in out], size)
    lad['frame_rate'] = fps
    curves = {'body_speed': [round(float(v * 100), 2) for v in speed]}
    return out, curves, lad


def run(result_dir, rig_path):
    motion = next((os.path.join(result_dir, f) for f in ('motion_temporal.npz', 'motion.npz')
                   if os.path.isfile(os.path.join(result_dir, f))), None)
    if motion is None:
        raise SystemExit('KEYFRAMES_FAILED: 结果缺少 motion.npz')
    m = np.load(motion)
    kp = np.asarray(m['kp'], dtype=np.float64)
    fps = float(m['fps']) if 'fps' in m.files else 24.0
    size = None
    try:
        size = json.load(open(rig_path, encoding='utf-8')).get('size')
    except (OSError, ValueError):
        pass
    keys, curves, lad = extract(kp, fps, size)
    summary = {}
    try:
        summary = json.load(open(os.path.join(result_dir, 'summary.json'), encoding='utf-8'))
    except (OSError, ValueError):
        pass
    video = (summary.get('source_video') or '').replace('\\', '/').split('/')[-1] or None
    doc = {'schema': 'v2r.keyframes/1', 'source_video': video, 'fps': fps, 'frames': int(kp.shape[0]),
           'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'generator': GENERATOR,
           'params': {}, 'keyframes': keys, 'curves': curves, 'curve_units': {'body_speed': 'cm/s'},
           'curve_labels': {'body_speed': '全身活动速度'}, 'breakdown_ladder': lad,
           'generated_from': {'motion_file': os.path.basename(motion),
                              'motion_sha256': hashlib.sha256(open(motion, 'rb').read()).hexdigest()}}
    data = (json.dumps(doc, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
    target = os.path.join(result_dir, 'keyframes_auto.json')
    if os.path.exists(target):
        hist = os.path.join(result_dir, 'keyframes_auto_history')
        os.makedirs(hist, exist_ok=True)
        old = open(target, 'rb').read()
        dest = os.path.join(hist, time.strftime('%Y%m%d-%H%M%S') + '_' + hashlib.sha256(old).hexdigest()[:12] + '.json')
        if not os.path.exists(dest):
            with open(dest, 'xb') as f:
                f.write(old)
    tmp = os.path.join(result_dir, '.keyframes_auto.json.tmp')
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, target)
    return doc


if __name__ == '__main__':
    d = run(sys.argv[1], sys.argv[2])
    print(f"[v2r] keyframes done: {len(d['keyframes'])} candidates, ladder {len(d['breakdown_ladder']['breakdowns'])}")
