"""Breakdown ladder: structural keys stay fixed, breakdowns are added ONE AT A TIME, worst place first.

python breakdown_ladder.py <source_dir> [--keys keyframes_auto.json | keyframes_artist.json]
                           [--contacts contacts_plan.json] [--max-breakdowns 60] [--out ladder.json] [--png ladder.png]

Why a ladder (docs/关键帧优先方案_20261111.md; Roberts et al. 2019 "Salient Poses" and its critique
"Saliency Diagrams", SIGGRAPH Asia 2019 TB):
  * structural keys (start/end, contact, extreme, hold, impact, technical) are decided by the motion and never move;
  * breakdowns are ranked: the first k of the list is the k-breakdown answer for EVERY k, so a key-count slider
    only adds or removes keys at the end and never reshuffles the ones the artist is looking at.

What the error is measured on (Cheynel et al. 2024: evaluate on the controls the animator edits):
  * the Biped-legal dense motion from build_spline_edit.build() (COM absorbs pelvis, hinge elbows, clavicle rule,
    no-twist, legs left to IK, per-frame body lowering for planted feet), evaluated at the VIDEO frame rate so the
    frame numbers match keyframes_*.json;
  * interpolation = build_spline_edit.Proxy: COM world position + per-node LOCAL rotations, quaternion
    Catmull-Rom (= TCB 0/0/0). Every keyed node shares the same key frames (whole-body blocking);
  * error = world-space distance of node heads, +X/+Y axis markers (10 cm lever, so pure rotations count) and the
    reader keypoints riding on COM / torso / head / arms; legs, ears and tail are not ranked (IK / follow-through);
  * reference = the legal motion with light jitter removal (local-rotation Gaussian, sigma 1 frame at 24 fps),
    left raw during fast spins (smoothing a 26 deg/frame spin would itself move the body by ~20 cm).
Key VALUES come from the same legal dense motion; final key poses will come from the per-pose fine fit, so the
numbers here rank timing, they are not an acceptance measurement (Max TCB must still be checked in Max).
"""
from pathlib import Path
import argparse, json, sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_spline_edit as bse  # noqa: E402

LADDER_VERSION = 'breakdown_ladder.py 0.1'
RANKED_PARTS = ['COM', 'Torso', 'L Arm', 'R Arm']
PART_LABEL = {'COM': 'body', 'Torso': 'body', 'L Arm': 'L_arm', 'R Arm': 'R_arm'}
KEYED_PARTS = ['COM', 'Torso', 'L Arm', 'R Arm', 'L Ear', 'R Ear', 'Tail']
DEFAULTS = dict(ref_sigma_24fps=1.0, spin_rate_deg=12.0, min_gap=2, max_breakdowns=60, floor_cm=1.0,
                suggest_max_cm=4.0, suggest_p95_cm=2.0)


def spin_mask_from_yaw(Ld, rate_deg, pad):
    """Frames where the COM turns faster than rate_deg per frame, padded by `pad` frames."""
    R = Ld[:, 0, :3, :3]
    yaw = np.degrees(np.unwrap(np.arctan2(R[:, 1, 0], R[:, 0, 0])))
    fast = np.abs(np.gradient(yaw)) > rate_deg
    m = fast.copy()
    for i in np.flatnonzero(fast):
        m[max(0, i - pad):i + pad + 1] = True
    return m, yaw


def prepare(source, contacts=None, fps=None, ref_sigma_24fps=DEFAULTS['ref_sigma_24fps'], spin_rate_deg=DEFAULTS['spin_rate_deg']):
    """Legal dense motion at the video frame rate + reference + proxy. Returns a context dict."""
    source = Path(source).resolve()
    mo = np.load(source / 'motion_temporal.npz')
    vfps = float(mo['fps'])
    fps = fps or vfps
    if contacts is None:
        for name in ('contacts_plan.json', 'contacts_auto.json'):
            if (source / name).is_file():
                contacts = source / name
                break
    bse.SOURCE = source
    B = bse.build(fps, str(contacts) if contacts else None)
    raw = B['Ld']
    spin, yaw = spin_mask_from_yaw(raw, spin_rate_deg * 24.0 / fps, pad=int(round(2 * fps / 24.0)))
    ref = bse.smooth_legal(raw, ref_sigma_24fps * fps / 24.0)
    ref[spin] = raw[spin]
    B['Ld'] = ref
    P = bse.Proxy(B)
    keyed = [n for p in KEYED_PARTS for n in bse.PARTS[p]]
    ref_m = {p: bse.markers(ref, bse.PARTS[p], B) for p in RANKED_PARTS}
    smooth_change = np.linalg.norm(bse.ride(ref, B['kp_node'], B['kp_local']) - bse.ride(raw, B['kp_node'], B['kp_local']), axis=-1)
    return dict(B=B, P=P, keyed=keyed, ref_m=ref_m, raw=raw, T=len(B['F']), fps=fps, video_fps=vfps, spin=spin, yaw=yaw,
                contacts=str(contacts) if contacts else None, ref_sigma=ref_sigma_24fps * fps / 24.0,
                ref_change_cm={'p95': float(np.percentile(smooth_change[~spin], 95)) if (~spin).any() else 0.0,
                               'max': float(smooth_change[~spin].max()) if (~spin).any() else 0.0})


def frame_error(ctx, keys):
    """(T, parts) max marker error in cm for whole-body keys at `keys`."""
    K = np.array(sorted(set(int(k) for k in keys)))
    poses = ctx['P'].pose({n: K for n in ctx['keyed']})
    return np.stack([np.linalg.norm(bse.markers(poses, bse.PARTS[p], ctx['B']) - ctx['ref_m'][p], axis=-1).max(1)
                     for p in RANKED_PARTS], 1)


def stats(E, mask=None):
    e = E.max(1)
    if mask is not None:
        e = e[mask]
    if len(e) == 0:
        return dict(max_cm=0.0, p95_cm=0.0, mean_cm=0.0)
    return dict(max_cm=round(float(e.max()), 2), p95_cm=round(float(np.percentile(e, 95)), 2),
                mean_cm=round(float(e.mean()), 2))


def ladder(ctx, structural, max_breakdowns=DEFAULTS['max_breakdowns'], min_gap=DEFAULTS['min_gap'],
           floor_cm=DEFAULTS['floor_cm']):
    """Greedy nested ranking. Each step: the interval (between consecutive keys) with the largest error gets
    a breakdown at its worst frame that is >= min_gap frames from every existing key.
    Fast-spin frames are not ranked: technical keys already sit every 2-3 frames there, the arms travel ~25 cm per
    frame, so breakdowns cannot fix it; the spin is reported separately (`spin` in the curve)."""
    T = ctx['T']
    ns = ~ctx['spin']
    keys = sorted(set(int(k) for k in structural) | {0, T - 1})
    E = frame_error(ctx, keys)
    curve = [dict(n=0, **stats(E, ns), spin=stats(E, ~ns))]
    steps = []
    for rank in range(1, max_breakdowns + 1):
        e = np.where(ns, E.max(1), 0.0)
        if e.max() <= floor_cm:
            break
        best = None
        for a, b in zip(keys[:-1], keys[1:]):
            cand = np.arange(a + min_gap, b - min_gap + 1)
            cand = cand[ns[cand]] if len(cand) else cand
            if len(cand) == 0:
                continue
            f = int(cand[np.argmax(e[cand])])
            if best is None or e[f] > best[0]:
                best = (float(e[f]), f, a, b, float(e[a:b + 1].max()))
        if best is None or best[0] <= floor_cm:
            break
        err_at, f, a, b, int_before = best
        part = RANKED_PARTS[int(np.argmax(E[f]))]
        keys = sorted(keys + [f])
        E = frame_error(ctx, keys)
        after = stats(E, ns)
        e_after = float(np.where(ns, E.max(1), 0.0)[a:b + 1].max())
        steps.append(dict(rank=rank, frame=f, between=[a, b], parts=[PART_LABEL[part]], worst_part=part,
                          error_here_cm=round(err_at, 2), interval_max_before_cm=round(int_before, 2),
                          interval_max_after_cm=round(e_after, 2),
                          clip_after=after,
                          reason=f'第 {a}–{b} 帧之间插值偏差最大处（{part}，{err_at:.1f}cm）；补上后这段最大偏差降到 '
                                 f'{e_after:.1f}cm'))
        curve.append(dict(n=rank, **after, spin=stats(E, ~ns)))
    return steps, curve, E


def suggest(curve, max_cm=DEFAULTS['suggest_max_cm'], p95_cm=DEFAULTS['suggest_p95_cm']):
    for c in curve:
        if c['max_cm'] <= max_cm and c['p95_cm'] <= p95_cm:
            return c['n']
    return curve[-1]['n']


def interval_report(ctx, structural, E0, top=8):
    keys = sorted(set(int(k) for k in structural) | {0, ctx['T'] - 1})
    e = E0.max(1)
    rows = []
    for a, b in zip(keys[:-1], keys[1:]):
        if b - a < 2:
            continue
        f = a + int(np.argmax(e[a:b + 1]))
        rows.append(dict(between=[a, b], frames=b - a, max_cm=round(float(e[f]), 2), at=f,
                         part=RANKED_PARTS[int(np.argmax(E0[f]))], spin=bool(ctx['spin'][a:b + 1].any())))
    return sorted(rows, key=lambda r: -r['max_cm'])[:top]


def compute(source, structural, contacts=None, **kw):
    """Everything the keyframe JSON embeds as `breakdown_ladder`."""
    p = {k: kw.get(k, v) for k, v in DEFAULTS.items()}
    ctx = prepare(source, contacts, ref_sigma_24fps=p['ref_sigma_24fps'], spin_rate_deg=p['spin_rate_deg'])
    keys0 = sorted(set(int(k) for k in structural) | {0, ctx['T'] - 1})
    E0 = frame_error(ctx, keys0)
    steps, curve, E = ladder(ctx, keys0, p['max_breakdowns'], p['min_gap'], p['floor_cm'])
    n_sug = suggest(curve, p['suggest_max_cm'], p['suggest_p95_cm'])
    ns = ~ctx['spin']
    return dict(
        generator=LADDER_VERSION,
        frame_rate=ctx['fps'],
        structural_frames=keys0,
        suggested_count=n_sug,
        suggestion_rule=f'最少的过渡键数，使身体最大偏差 ≤ {p["suggest_max_cm"]}cm 且 p95 ≤ {p["suggest_p95_cm"]}cm；达不到时取全部',
        breakdowns=steps,
        error_curve=curve,
        worst_intervals_before=interval_report(ctx, keys0, E0),
        structural_only_non_spin=stats(E0, ns),
        structural_only_spin=stats(E0, ~ns),
        spin_frames=[int(x) for x in np.flatnonzero(ctx['spin'])[[0, -1]]] if ctx['spin'].any() else None,
        error_curve_units='cm, max over COM/torso/head/arm markers per frame, world space; max/p95/mean exclude fast-spin '
                          'frames (not ranked), `spin` gives the same numbers inside the spin',
        params=dict(p, contacts=Path(ctx['contacts']).name if ctx['contacts'] else None, ref_sigma_frames=ctx['ref_sigma'],
                    reference_change_by_jitter_removal_cm=ctx['ref_change_cm'],
                    spin_frames=int(ctx['spin'].sum()), ranked_parts=RANKED_PARTS,
                    interpolation='proxy: quaternion Catmull-Rom on Biped local rotations + COM position (TCB 0/0/0); NOT Max-evaluated',
                    key_values='legal dense motion at the key frames (to be replaced by per-pose fine fits)'),
    ), dict(ctx=ctx, E0=E0, E=E)


def figure(lad, extra, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for fam in ('Microsoft YaHei', 'SimHei'):
        matplotlib.rcParams['font.sans-serif'] = [fam] + matplotlib.rcParams['font.sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    ctx, E0 = extra['ctx'], extra['E0']
    curve = lad['error_curve']
    n_sug = lad['suggested_count']
    fig, ax = plt.subplots(3, 1, figsize=(13, 9.5), gridspec_kw=dict(height_ratios=[1.1, 1.1, 0.9]))
    T = ctx['T']
    t = np.arange(T)
    keys0 = lad['structural_frames']
    sug = sorted(keys0 + [s['frame'] for s in lad['breakdowns'][:n_sug]])
    Es = frame_error(ctx, sug)
    ax[0].fill_between(t, 0, ctx['spin'] * 1.0, step='mid', color='0.92', transform=ax[0].get_xaxis_transform(), label='快速转圈')
    ax[0].plot(t, E0.max(1), color='tab:red', lw=1.2, label=f'只有结构键（{len(keys0)} 个）')
    ax[0].plot(t, Es.max(1), color='tab:blue', lw=1.2, label=f'+ 前 {n_sug} 个过渡键（建议值）')
    for k in keys0:
        ax[0].axvline(k, color='tab:red', lw=0.6, alpha=0.5)
    for s in lad['breakdowns'][:n_sug]:
        ax[0].axvline(s['frame'], color='tab:blue', lw=0.8, ls='--', alpha=0.8)
        ax[0].text(s['frame'], 0.97, str(s['rank']), color='tab:blue', fontsize=7, ha='center', va='top',
                   transform=ax[0].get_xaxis_transform())
    ax[0].set_ylabel('身体偏差 cm（逐帧最大）')
    ax[0].set_title('结构键（红竖线）+ 按排名补的过渡键（蓝虚线，数字=排名）')
    ax[0].legend(loc='upper left', fontsize=8)
    ax[0].set_xlim(0, T - 1)
    for j, p in enumerate(RANKED_PARTS):
        ax[1].plot(t, Es[:, j], lw=1.0, label=p)
    ax[1].set_xlim(0, T - 1)
    ax[1].set_ylabel('cm')
    ax[1].set_title(f'建议键数下各部位偏差（视频帧，{ctx["fps"]:.0f}fps）')
    ax[1].legend(loc='upper left', fontsize=8, ncol=4)
    n = [c['n'] for c in curve]
    ax[2].plot(n, [c['max_cm'] for c in curve], 'o-', ms=3, label='最大')
    ax[2].plot(n, [c['p95_cm'] for c in curve], 'o-', ms=3, label='p95')
    ax[2].axvline(n_sug, color='k', ls=':', lw=1)
    ax[2].set_xlabel('过渡键个数（键数滑杆）')
    ax[2].set_ylabel('cm')
    ax[2].set_title('键数-偏差曲线：每多一个过渡键，只在末尾追加，前面的键不动')
    ax[2].legend(fontsize=8)
    ax[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('--keys', default=None, help='keyframes_auto.json or keyframes_artist.json (default: <source>/keyframes_auto.json)')
    ap.add_argument('--contacts', default=None, help='contacts used by the legal model (default: contacts_plan.json, else contacts_auto.json)')
    ap.add_argument('--max-breakdowns', type=int, default=DEFAULTS['max_breakdowns'])
    ap.add_argument('--min-gap', type=int, default=DEFAULTS['min_gap'])
    ap.add_argument('--out', default=None, help='write the ladder json here (does not touch keyframes_*.json)')
    ap.add_argument('--png', default=None)
    a = ap.parse_args()
    src = Path(a.source)
    kpath = Path(a.keys) if a.keys else src / 'keyframes_auto.json'
    kj = json.loads(kpath.read_text(encoding='utf8'))
    structural = [k['frame'] for k in kj['keyframes'] if k.get('type') != 'breakdown']
    lad, extra = compute(src, structural, a.contacts, max_breakdowns=a.max_breakdowns, min_gap=a.min_gap)
    lad['structural_source'] = kpath.name
    print(f"structural keys {len(lad['structural_frames'])} ({kpath.name}); suggested breakdowns {lad['suggested_count']}")
    print('structural only, non-spin:', lad['structural_only_non_spin'], ' spin', lad['spin_frames'], lad['structural_only_spin'])
    print('worst intervals before:', [(r['between'], r['max_cm'], r['part'], 'spin' if r['spin'] else '') for r in lad['worst_intervals_before'][:6]])
    for c in lad['error_curve']:
        if c['n'] in (0, 5, 10, 15, 20, 25, 30, 40, 50, 60) or c['n'] == lad['suggested_count']:
            print(f"  +{c['n']:2d} breakdowns: max {c['max_cm']:5.2f}  p95 {c['p95_cm']:5.2f}  mean {c['mean_cm']:5.2f}   (spin max {c['spin']['max_cm']:.1f})")
    for s in lad['breakdowns'][:lad['suggested_count']]:
        print(f"  #{s['rank']:2d} f{s['frame']:3d} in {s['between']} {s['worst_part']:5s} {s['error_here_cm']:5.1f}cm -> interval {s['interval_max_after_cm']:.1f}cm")
    if a.out:
        Path(a.out).write_text(json.dumps(lad, ensure_ascii=False, indent=1), encoding='utf8')
        print('wrote', a.out)
    if a.png:
        figure(lad, extra, a.png)
        print('wrote', a.png)


if __name__ == '__main__':
    main()
