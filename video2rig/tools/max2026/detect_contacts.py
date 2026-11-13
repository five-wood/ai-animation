"""Foot-contact candidates from the reader's triangulated foot keypoints (not from the solved legs,
which are a straight 'stick' on this rig because calf/foot are folded).

python detect_contacts.py <source_dir>   (needs rig.json, motion_temporal.npz, reading.npz)
Writes <source_dir>/contacts_auto.json and contacts_auto.png. Frames are SOURCE frames.
Rules (all reported in the json):
  * position axes: x,z come from the front view; y (depth) only when the side view is also confident
  * stationary: smoothed speed < SPEED cm/frame; runs >= MIN_RUN frames; short gaps bridged
  * fast body spin (|yaw rate| > SPIN deg/frame) forces both feet free
  * a run touching an unobserved clip start/end is extended to the edge
"""
from pathlib import Path
import json, sys
import numpy as np

SPEED = 0.9      # cm per source frame (~22 cm/s at 24fps)
MIN_RUN = 4
BRIDGE = 2
BRIDGE_DIST = 2.5
CONF = 0.3
SPIN = 12.0      # deg per source frame


def runs(mask):
    out, st = [], None
    for i, m in enumerate(mask):
        if m and st is None:
            st = i
        if (not m or i == len(mask) - 1) and st is not None:
            out.append([st, i if m else i - 1])
            st = None
    return out


def main(src):
    src = Path(src)
    rig = json.loads((src / 'rig.json').read_text(encoding='utf8'))
    mo = np.load(src / 'motion_temporal.npz')
    rd = np.load(src / 'reading.npz')
    names = [k['name'] for k in rig['keypoints']]
    X = rd['X_fit'] * 100.0
    conf = rd['conf_fit']
    T = len(X)
    G = mo['G']
    yaw = np.degrees(np.unwrap(np.arctan2(G[:, 1, 0], G[:, 0, 0])))
    yaw_rate = np.abs(np.gradient(yaw))
    spin = yaw_rate > SPIN
    for _ in range(2):
        spin = spin | np.r_[spin[1:], False] | np.r_[False, spin[:-1]]
    out = {'source_fps': float(mo['fps']), 'frames': T, 'method': __doc__.strip().splitlines()[0],
           'params': dict(speed_cm_per_frame=SPEED, min_run=MIN_RUN, bridge=BRIDGE, bridge_dist_cm=BRIDGE_DIST,
                          conf=CONF, spin_deg_per_frame=SPIN),
           'spin_frames': [int(i) for i in np.where(spin)[0]], 'feet': {}, 'reviewed': False}
    for s in 'LR':
        j = names.index(f'Bip001 {s} Foot')
        front = conf[:, 0, j] > CONF
        side = conf[:, 1, j] > CONF
        P = X[:, j].copy()
        P[~front] = np.nan
        P[front & ~side, 1] = np.nan  # depth unknown from one view
        d = np.diff(P, axis=0)
        sp = np.sqrt(np.nansum(np.where(np.isnan(d), 0, d) ** 2, 1))
        sp[np.all(np.isnan(d), 1)] = np.nan
        sp = np.r_[sp[:1], sp]
        sm = np.array([np.nanmedian(sp[max(0, i - 1):i + 2]) if np.any(~np.isnan(sp[max(0, i - 1):i + 2])) else np.nan
                       for i in range(T)])
        still = (sm < SPEED) & ~spin
        R = [r for r in runs(still) if r[1] - r[0] + 1 >= MIN_RUN]
        merged = []
        for r in R:
            if merged and r[0] - merged[-1][1] - 1 <= BRIDGE:
                a = np.nanmedian(P[merged[-1][0]:merged[-1][1] + 1, [0, 2]], 0)
                b = np.nanmedian(P[r[0]:r[1] + 1, [0, 2]], 0)
                if np.linalg.norm(a - b) < BRIDGE_DIST and not spin[merged[-1][1]:r[0]].any():
                    merged[-1][1] = r[1]
                    continue
            merged.append(list(r))
        # extend to clip edges over unobserved frames
        if merged and merged[0][0] > 0 and not front[:merged[0][0]].any():
            merged[0][0] = 0
        if merged and merged[-1][1] < T - 1 and not front[merged[-1][1] + 1:].any():
            merged[-1][1] = T - 1
        segs = []
        for a, b in merged:
            seg = P[a:b + 1]
            segs.append({'start': int(a), 'end': int(b), 'kind': 'planted',
                         'obs_x_cm': float(np.nanmedian(seg[:, 0])),
                         'obs_y_cm': None if np.all(np.isnan(seg[:, 1])) else float(np.nanmedian(seg[:, 1])),
                         'obs_z_cm': float(np.nanmedian(seg[:, 2])),
                         'depth_observed_fraction': float(np.mean(~np.isnan(seg[:, 1]))),
                         'source': 'auto'})
        out['feet'][s] = segs
        out.setdefault('evidence', {})[s] = {'speed_cm_per_frame': [None if np.isnan(v) else round(float(v), 3) for v in sm],
                                             'front_conf_ok': front.tolist(), 'side_conf_ok': side.tolist()}
        out.setdefault('_P', {})[s] = P
        print(s, [(g['start'], g['end']) for g in segs])
    print('spin frames', out['spin_frames'][:3], '...', out['spin_frames'][-3:] if out['spin_frames'] else [])
    P_all = out.pop('_P')
    (src / 'contacts_auto.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf8')
    figure(src, out, X, names, conf, 'contacts_auto.png')
    plan = make_plan(out, P_all, spin)
    (src / 'contacts_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf8')
    figure(src, plan, X, names, conf, 'contacts_plan.png')


MERGE_GAP = 12      # source frames
MERGE_X = 3.0       # cm, lateral (front view) difference
MERGE_Y = 6.0       # cm, depth difference when both observed
MIN_SWING = 3       # source frames between two plants of one foot


def seg_obs(P, a, b):
    seg = P[a:b + 1]
    x = np.nanmedian(seg[:, 0]) if np.any(~np.isnan(seg[:, 0])) else np.nan
    y = np.nanmedian(seg[:, 1]) if np.any(~np.isnan(seg[:, 1])) else np.nan
    z = np.nanmedian(seg[:, 2]) if np.any(~np.isnan(seg[:, 2])) else np.nan
    return x, y, z, float(np.mean(~np.isnan(seg[:, 1])))


def make_plan(auto, P, spin):
    """Editing-oriented contact plan (still a proposal for human review):
    1. merge same-foot plants separated by a short lift when the foot stays in place (shuffle noise)
    2. outside fast spins, at least one foot supports the body: an uncovered run is given to the
       foot with the lower observed speed, by extending its neighbouring plant
    3. two plants of one foot need >= MIN_SWING free frames between them"""
    T = auto['frames']
    log = []
    feet = {s: [[g['start'], g['end'], g.get('source', 'auto')] for g in auto['feet'][s]] for s in 'LR'}
    speed = {s: np.array([np.nan if v is None else v for v in auto['evidence'][s]['speed_cm_per_frame']]) for s in 'LR'}

    def merge(s):
        out = []
        for a, b, src in feet[s]:
            if out:
                pa, pb, psrc = out[-1]
                gap = a - pb - 1
                if gap <= 0:
                    out[-1] = [pa, max(pb, b), psrc]
                    continue
                if gap <= MERGE_GAP and not spin[pb:a].any():
                    x0, y0, _, _ = seg_obs(P[s], pa, pb)
                    x1, y1, _, _ = seg_obs(P[s], a, b)
                    same_x = not np.isnan(x0) and not np.isnan(x1) and abs(x0 - x1) < MERGE_X
                    same_y = np.isnan(y0) or np.isnan(y1) or abs(y0 - y1) < MERGE_Y
                    if same_x and same_y:
                        out[-1] = [pa, b, psrc + '+merged']
                        log.append(f'{s}: merged {pa}-{pb} with {a}-{b} (gap {gap}, dx {abs(x0 - x1):.1f}cm)')
                        continue
            out.append([a, b, src])
        feet[s] = out

    for s in 'LR':
        merge(s)
    for _ in range(T):
        cov = np.zeros(T, bool)
        for s in 'LR':
            for a, b, _ in feet[s]:
                cov[a:b + 1] = True
        un = [r for r in runs(~cov & ~spin)]
        if not un:
            break
        u, v = un[0]
        score = {s: (np.nanmedian(speed[s][u:v + 1]) if np.any(~np.isnan(speed[s][u:v + 1])) else np.inf) for s in 'LR'}
        done = False
        for s in sorted('LR', key=lambda k: score[k]):
            prev = [g for g in feet[s] if g[1] == u - 1]
            nxt = [g for g in feet[s] if g[0] == v + 1]
            if prev:
                prev[0][1] = v
                prev[0][2] += '+support'
            elif nxt:
                nxt[0][0] = u
                nxt[0][2] += '+support'
            else:
                continue
            log.append(f'{s}: support extension over {u}-{v} (median speed L {score["L"]:.2f}, R {score["R"]:.2f} cm/frame)')
            done = True
            break
        if not done:
            s = min('LR', key=lambda k: score[k])
            feet[s].append([u, v, 'support'])
            feet[s].sort()
            log.append(f'{s}: new support plant {u}-{v}')
        for k in 'LR':
            merge(k)
    # minimum swing between two plants of one foot
    for s in 'LR':
        g = feet[s]
        for i in range(1, len(g)):
            gap = g[i][0] - g[i - 1][1] - 1
            if gap < MIN_SWING:
                cut = MIN_SWING - gap
                g[i][0] += cut
                log.append(f'{s}: shortened plant start {g[i][0] - cut}->{g[i][0]} to leave a {MIN_SWING}-frame swing')
        feet[s] = [x for x in g if x[1] - x[0] >= 1]
    plan = {k: v for k, v in auto.items() if k not in ('feet', 'evidence')}
    plan['evidence'] = auto['evidence']
    plan['method'] = make_plan.__doc__.strip().splitlines()[0]
    plan['rules'] = [l.strip() for l in make_plan.__doc__.strip().splitlines()[1:]]
    plan['params'] = dict(auto['params'], merge_gap=MERGE_GAP, merge_x_cm=MERGE_X, merge_y_cm=MERGE_Y, min_swing=MIN_SWING)
    plan['log'] = log
    plan['reviewed'] = False
    plan['feet'] = {}
    for s in 'LR':
        plan['feet'][s] = []
        for a, b, src in feet[s]:
            x, y, z, fr = seg_obs(P[s], a, b)
            plan['feet'][s].append({'start': int(a), 'end': int(b), 'kind': 'planted', 'source': src,
                                    'obs_x_cm': None if np.isnan(x) else float(x), 'obs_y_cm': None if np.isnan(y) else float(y),
                                    'obs_z_cm': None if np.isnan(z) else float(z), 'depth_observed_fraction': fr})
        print('plan', s, [(g['start'], g['end'], g['source']) for g in plan['feet'][s]])
    for l in log:
        print('  ', l)
    return plan


def figure(src, out, X, names, conf, fname):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'DejaVu Sans']
    T = out['frames']
    t = np.arange(T)
    fig, ax = plt.subplots(3, 1, figsize=(18, 9), sharex=True)
    for r, (s, c) in enumerate([('L', 'tab:blue'), ('R', 'tab:red')]):
        j = names.index(f'Bip001 {s} Foot')
        ok = conf[:, 0, j] > CONF
        ax[r].plot(t, np.where(ok, X[:, j, 0], np.nan), '.-', color=c, label=f'{s} x')
        ax[r].plot(t, np.where(ok & (conf[:, 1, j] > CONF), X[:, j, 1], np.nan), 'x-', color=c, alpha=.5, label=f'{s} y (depth)')
        ax[r].plot(t, np.where(ok, X[:, j, 2] * 3, np.nan), '-', color='k', alpha=.5, label=f'{s} z×3')
        for g in out['feet'][s]:
            ax[r].axvspan(g['start'] - .5, g['end'] + .5, color=c, alpha=.15)
        ax[r].legend(fontsize=8, loc='upper left')
        ax[r].set_ylabel(f'{s} foot (cm)')
        ax[2].plot(t, out['evidence'][s]['speed_cm_per_frame'], color=c, label=f'{s} speed')
    for f in out['spin_frames']:
        ax[2].axvspan(f - .5, f + .5, color='green', alpha=.1)
    ax[2].axhline(SPEED, color='gray', ls='--')
    ax[2].set_ylim(0, 6)
    ax[2].legend(fontsize=8)
    for a in ax:
        a.grid(alpha=.3)
        a.set_xticks(range(0, T, 8))
    ax[2].set_xlabel('源帧 (24fps)；色块=判定为着地（planted），绿色=快速转身（强制离地）')
    fig.tight_layout()
    fig.savefig(src / fname, dpi=90)
    plt.close(fig)


if __name__ == '__main__':
    main(sys.argv[1])
