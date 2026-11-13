"""Read-only probe on an existing spline_edit.max (run in 3dsmaxbatch -sceneFile <file>):
for one planted segment, try intermediate planted-key cadences (scene reloaded between trials) and
measure ankle drift, contact-pivot drift and foot rotation. Nothing is saved."""
import json, os, math, traceback
import pymxs
rt = pymxs.runtime
RUN = os.environ['V2R_RUN']
SIDE, A, B = os.environ.get('V2R_SIDE', 'R'), int(os.environ.get('V2R_A', '134')), int(os.environ.get('V2R_B', '158'))
COM_KEYS = [int(x) for x in os.environ.get('V2R_COMKEYS', '').split(',') if x]
SCENE = os.path.join(RUN, 'spline_edit.max')
out = {'side': SIDE, 'frames': [A, B], 'trials': []}


def xyz(p):
    return [float(p.x), float(p.y), float(p.z)]


def measure(foot, ref_tm):
    worst_p, worst_r = 0.0, 0.0
    for i in range(int(2 * (B - A)) + 1):
        with pymxs.attime(A + 0.5 * i):
            tm = foot.transform
        p = xyz(tm.row4)
        worst_p = max(worst_p, math.sqrt(sum((p[k] - xyz(ref_tm.row4)[k]) ** 2 for k in range(3))))
        d = rt.inverse(ref_tm.rotationpart) * tm.rotationpart
        worst_r = max(worst_r, abs(float(rt.quatToAngleAxis(d).angle)) if hasattr(rt, 'quatToAngleAxis') else 0.0)
    return worst_p, worst_r


def angle_between(q1, q2):
    d = rt.inverse(q1) * q2
    w = min(1.0, abs(float(d.w)))
    return math.degrees(2 * math.acos(w))


def trial(name, frames):
    rt.loadMaxFile(SCENE, quiet=True)
    foot = rt.getNodeByName('Bip001 %s Foot' % SIDE)
    with pymxs.attime(A):
        ref_tm = foot.transform
    for f in frames:
        with pymxs.attime(float(f)):
            rt.biped.setTransform(foot, rt.Name('pos'), ref_tm.row4, True)
            rt.biped.setTransform(foot, rt.Name('rotation'), ref_tm.rotationpart, True)
            rt.biped.setPlantedKey(foot)
    wp, wr = 0.0, 0.0
    for i in range(int(2 * (B - A)) + 1):
        with pymxs.attime(A + 0.5 * i):
            tm = foot.transform
        p = xyz(tm.row4)
        r0 = xyz(ref_tm.row4)
        wp = max(wp, math.sqrt(sum((p[k] - r0[k]) ** 2 for k in range(3))))
        wr = max(wr, angle_between(ref_tm.rotationpart, tm.rotationpart))
    out['trials'].append({'name': name, 'added_keys': len(frames), 'ankle_drift_cm': wp, 'foot_rot_deg': wr})


try:
    trial('baseline', [])
    trial('every_6f', list(range(A + 6, B, 6)))
    trial('every_3f', list(range(A + 3, B, 3)))
    trial('every_2f', list(range(A + 2, B, 2)))
    if COM_KEYS:
        trial('at_com_keys', [f for f in COM_KEYS if A < f < B])
except Exception:
    out['error'] = traceback.format_exc()
with open(os.path.join(RUN, 'plant_drift_probe.json'), 'w', encoding='utf8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
