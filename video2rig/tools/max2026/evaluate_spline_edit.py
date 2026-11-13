"""Evaluate the Max-sampled spline edit against (a) the Biped-legal dense target and (b) the source
keypoints. System Python + NumPy (+ matplotlib for the figure). Reads only; writes eval_report.json and
eval_timeline.png in the run folder.
"""
from pathlib import Path
import json, os, sys
import numpy as np

RUN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / 'work/max_editable_20261111_spline'
PARTS_KP = {
    'COM/Torso': ['Bip001', 'Bip001 Spine1', 'Bip001 Neck', 'feature_Bone_02', 'feature_Bone_02.tip', 'feature_Bone_01',
                  'feature_Bone_01.tip', 'Bone149', 'Bone149.tip', 'Bone147', 'Bone147.tip', 'Bone146', 'Bone146.tip'],
    'L Arm': ['Bip001 L UpperArm', 'Bip001 L Forearm', 'Bip001 L Hand', 'Bip001_L_Finger01_Bone.tip',
              'Bip001_L_UpperArm_Bone.tip', 'Bip001_L_Clavicle_Bone.tip', 'Bip001 L Hand.side_a', 'Bip001 L Hand.side_b'],
    'R Arm': ['Bip001 R UpperArm', 'Bip001 R Forearm', 'Bip001 R Hand', 'Bip001_R_Finger01_Bone.tip',
              'Bip001_R_UpperArm_Bone.tip', 'Bip001_R_Clavicle_Bone.tip', 'Bip001 R Hand.side_a', 'Bip001 R Hand.side_b'],
    'Ears': ['Bone001', 'Bone002', 'Bone142', 'Bone143', 'Bone143.tip', 'Bone0013', 'Bone0021', 'Bone0022', 'Bone0023'],
    'Tail': ['Bone004', 'Bone004.tip'],
    'Feet': ['Bip001 L Foot', 'Bip001 R Foot'],
}


def col(rows):
    m = np.asarray(rows, float)
    a = np.eye(4)
    a[:, :3] = m
    a = a.T
    return a


def main():
    d = np.load(RUN / 'legal_dense.npz')
    nodes = [str(x) for x in d['nodes']]
    legal, F, dz = d['legal'], d['frames'], float(d['dz'])
    dzs = d['dzs'] if 'dzs' in d.files else np.full(len(F), dz)
    kp_node, kp_local, kp_names, kp_src = d['kp_node'], d['kp_local'], [str(x) for x in d['kp_names']], d['kp_src']
    S = json.loads((RUN / 'max_samples.json').read_text(encoding='utf8'))
    times = np.array(S['times'])
    integer = np.isclose(times, np.round(times))
    Max = {n: np.array([col(s[n]) for s in S['samples']]) for n in S['samples'][0]}
    for n in Max:
        Max[n][:, :3, :3] /= np.linalg.norm(Max[n][:, :3, :3], axis=1, keepdims=True)
    fi = np.round(times[integer]).astype(int)

    # --- keypoints riding on Max nodes, at integer frames
    kp_max = np.stack([np.einsum('tij,j->ti', Max[nodes[kp_node[j]]][integer], kp_local[j])[:, :3] for j in range(len(kp_names))], 1)
    kp_leg = np.einsum('tkij,kj->tki', legal[fi][:, kp_node], kp_local)[..., :3]
    # feet keypoints ride on Bip001 L/R Foot in the legal model == anchor; compare source feet via thigh chain instead
    e_legal = np.linalg.norm(kp_max - kp_leg, axis=-1)
    dzv = np.c_[np.zeros((len(fi), 2)), dzs[fi]][:, None, :]
    e_src = np.linalg.norm(kp_max + dzv - kp_src[fi], axis=-1)

    # --- node-level comparison
    node_err = {}
    for i, n in enumerate(nodes):
        if n not in Max or 'Calf' in n or 'Thigh' in n:
            continue
        A = Max[n][integer]
        Lg = legal[fi, i]
        ep = np.linalg.norm(A[:, :3, 3] - Lg[:, :3, 3], axis=1)
        c = np.clip((np.trace(np.swapaxes(A[:, :3, :3], 1, 2) @ Lg[:, :3, :3], axis1=1, axis2=2) - 1) / 2, -1, 1)
        node_err[n] = {'pos_max_cm': float(ep.max()), 'ang_max_deg': float(np.degrees(np.arccos(c)).max())}

    # --- key frames: Max vs legal must match (write fidelity) at the part key frames
    pk = json.loads((RUN / 'edit_packet.json').read_text(encoding='utf8'))
    keyfit = {}
    for p, part in pk['parts'].items():
        K = sorted({int(k) for n in part['nodes'] for k in part['keys'][n]})
        worst = 0.0
        for n in part['nodes']:
            i = nodes.index(n)
            for k in K:
                t = int(np.where(np.isclose(times, k))[0][0])
                worst = max(worst, float(np.linalg.norm(Max[n][t, :3, 3] - legal[k, i, :3, 3])))
        keyfit[p] = worst

    # --- feet: anchor drift over all samples (incl. half frames), knee flex
    feet = {}
    for s in 'LR':
        ft = pk['feet'][s]
        if 'keys' in ft:
            plants = [(k['frame'], col(k['rows'])) for k in ft['keys'] if k['type'] == 'plant_start']
            plants = [(a, b, A) for (a, A), (b_, _) in zip(plants, [(k['frame'], None) for k in ft['keys'] if k['type'] == 'plant_end']) for b in [b_]]
        else:
            plants = [(ft['frames'][0], ft['frames'][1], col(ft['anchor_rows']))]
        P = Max[f'Bip001 {s} Foot']
        hip, knee = Max[f'Bip001 {s} Thigh'][:, :3, 3], Max[f'Bip001 {s} Calf'][:, :3, 3]
        u, v = knee - hip, P[:, :3, 3] - knee
        flex = np.degrees(np.arccos(np.clip(np.sum(u * v, 1) / np.linalg.norm(u, axis=1) / np.linalg.norm(v, axis=1), -1, 1)))
        toe = Max[f'Bip001 {s} Toe0'][:, :3, 3]
        segs, planted = [], np.zeros(len(times), bool)
        for a, b, A in plants:
            m = (times >= a) & (times <= b)
            planted |= m
            drift = np.linalg.norm(P[m, :3, 3] - A[:3, 3], axis=1)
            c = np.clip((np.trace(np.swapaxes(P[m, :3, :3], 1, 2) @ A[:3, :3], axis1=1, axis2=2) - 1) / 2, -1, 1)
            segs.append({'frames': [int(a), int(b)], 'anchor_drift_max_cm': float(drift.max()),
                         'anchor_rot_max_deg': float(np.degrees(np.arccos(c)).max()),
                         'knee_flex_deg': [float(flex[m].min()), float(flex[m].max())]})
        feet[s] = {'plants': segs,
                   'anchor_drift_max_cm': max(g['anchor_drift_max_cm'] for g in segs),
                   'anchor_rot_max_deg': max(g['anchor_rot_max_deg'] for g in segs),
                   'knee_flex_deg': [float(flex.min()), float(flex.max())],
                   'knee_flex_planted_deg': [float(flex[planted].min()), float(flex[planted].max())],
                   'toe_height_range_cm': [float(toe[:, 2].min()), float(toe[:, 2].max())],
                   'free_toe_min_height_cm': float(toe[~planted, 2].min()) if (~planted).any() else None}

    rep = {'evaluated_times': len(times), 'integer_frames': int(integer.sum()),
           'max_vs_legal_keypoint_cm': {}, 'max_vs_source_keypoint_cm': {},
           'node_vs_legal': node_err, 'key_frame_write_error_cm': keyfit, 'feet': feet, 'dz_cm': dz}
    for p, names in PARTS_KP.items():
        idx = [kp_names.index(n) for n in names if n in kp_names]
        rep['max_vs_legal_keypoint_cm'][p] = {'max': float(e_legal[:, idx].max()), 'p95': float(np.percentile(e_legal[:, idx], 95))}
        rep['max_vs_source_keypoint_cm'][p] = {'max': float(e_src[:, idx].max()), 'p95': float(np.percentile(e_src[:, idx], 95))}
    rep['max_vs_legal_keypoint_cm']['ALL'] = {'max': float(e_legal.max()), 'p95': float(np.percentile(e_legal, 95))}
    rep['max_vs_source_keypoint_cm']['ALL'] = {'max': float(e_src.max()), 'p95': float(np.percentile(e_src, 95))}
    (RUN / 'eval_report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf8')
    print(json.dumps({k: rep[k] for k in ('max_vs_legal_keypoint_cm', 'max_vs_source_keypoint_cm', 'key_frame_write_error_cm', 'feet')},
                     indent=1, ensure_ascii=False))
    worst = sorted(node_err.items(), key=lambda x: -x[1]['pos_max_cm'])[:6]
    print('worst nodes vs legal:', [(n, round(v['pos_max_cm'], 2), round(v['ang_max_deg'], 1)) for n, v in worst])
    figure(F, fi, kp_names, kp_src, kp_max, dzs, pk)


def figure(F, fi, names, kp_src, kp_max, dzs, pk):
    dz = dzs[fi]
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True, height_ratios=[1.3, 1, 1.2])
    ax = axes[0]
    for n, c in [('Bip001 R Hand', 'tab:red'), ('Bip001 L Hand', 'tab:blue')]:
        j = names.index(n)
        ax.plot(F, kp_src[:, j, 2] - kp_src[0, j, 2], color=c, lw=2.4, alpha=.3, label=f'{n} 高度 · 原拟合')
        ax.plot(fi, kp_max[:, j, 2] + dz - kp_src[0, j, 2], color=c, lw=1.1, ls='--', label=f'{n} 高度 · Max 稀疏键回采')
    ax.set_ylabel('相对第0帧 (cm)')
    ax.legend(fontsize=8, ncol=2, loc='upper left')
    ax.grid(alpha=.3)
    ax = axes[1]
    j = names.index('Bip001')
    ax.plot(F, kp_src[:, j, 2], color='k', lw=2.4, alpha=.3, label='COM 高度 · 原拟合')
    ax.plot(fi, kp_max[:, j, 2], color='k', lw=1.1, ls='--', label=f'COM 高度 · Max（为保持屈膝下沉 0–{dz.max():.2f}cm）')
    ax.set_ylabel('cm')
    ax.legend(fontsize=8, loc='upper left')
    ax.grid(alpha=.3)
    ax = axes[2]
    parts = list(pk['parts']) + ['L Foot IK', 'R Foot IK']
    for r, p in enumerate(parts):
        y = len(parts) - 1 - r
        ax.hlines(y, F[0], F[-1], color='#ddd', lw=1)
        if p.endswith('Foot IK'):
            ft = pk['feet'][p[0]]
            if 'keys' in ft:
                for k in ft['keys']:
                    pass
                pl = [(a, b) for a, b in ft['plants']]
                for a, b in pl:
                    ax.hlines(y, a, b, color='#2a7f3f', lw=6, alpha=.5)
                ks = ft['keys']
                ax.scatter([k['frame'] for k in ks if k['type'] in ('plant_start', 'plant_end')],
                           [y] * sum(k['type'] in ('plant_start', 'plant_end') for k in ks), marker='s', s=40, color='#2a7f3f', zorder=3)
                ax.scatter([k['frame'] for k in ks if k['type'] == 'plant_hold'], [y] * sum(k['type'] == 'plant_hold' for k in ks),
                           marker='s', s=12, color='#2a7f3f', zorder=3)
                ax.scatter([k['frame'] for k in ks if k['type'] == 'free'], [y] * sum(k['type'] == 'free' for k in ks),
                           marker='^', s=22, color='#7a7a7a', zorder=3)
                ax.text(F[-1] + 2, y, f"{len(ks)} ({len(pl)} 段着地)", va='center', fontsize=8)
            else:
                ax.scatter([F[0], F[-1]], [y, y], marker='s', s=50, color='#2a7f3f', zorder=3)
                ax.text(F[-1] + 2, y, '2 (planted)', va='center', fontsize=8)
            continue
        d = pk['parts'][p]
        ax.scatter(d['poses'], [y] * len(d['poses']), marker='D', s=40, color='#1f3b73', zorder=3)
        ax.scatter(d['breakdowns'], [y] * len(d['breakdowns']), marker='o', s=14, color='#e3872d', zorder=3)
        ax.text(F[-1] + 2, y, f"{len(d['poses'])}+{len(d['breakdowns'])}", va='center', fontsize=8)
    ax.set_yticks(range(len(parts)), parts[::-1])
    ax.set_xlabel('帧 (30fps)')
    ax.set_xlim(F[0] - 2, F[-1] + 22)
    ax.grid(axis='x', alpha=.3)
    fig.suptitle(f'{RUN.name} · Max 2026 实写回采：分部位姿势键(◆) + 过渡键(●) + 脚 IK（绿=着地段 ■=IK键 ▲=离地键）', fontsize=11)
    fig.tight_layout()
    fig.savefig(RUN / 'eval_timeline.png', dpi=130)


if __name__ == '__main__':
    main()
