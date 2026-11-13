"""3dsmaxbatch probe: import a file, report how many keys Max sees per animated node.
3dsmaxbatch.exe probe_import.py  (env PROBE_FILE, PROBE_OUT)"""
import json
import os

from pymxs import runtime as rt

src = os.environ['PROBE_FILE']
out = os.environ['PROBE_OUT']
rep = {'file': src}
rt.resetMaxFile(rt.name('noPrompt'))
if os.environ.get('PROBE_FPS'):
    rt.frameRate = int(os.environ['PROBE_FPS'])
try:
    if os.environ.get('PROBE_FBXIMP'):
        # FBX SDK importer also reads BVH; this avoids the interactive Biped mocap path.
        ok = rt.importFile(src, rt.name('noPrompt'), using=rt.FBXIMP)
    else:
        ok = rt.importFile(src, rt.name('noPrompt'))
    rep['import_ok'] = bool(ok)
except Exception as e:  # noqa: BLE001
    rep['import_error'] = str(e)
rep['frameRate'] = rt.frameRate
rep['range'] = [float(rt.animationRange.start.frame), float(rt.animationRange.end.frame)]
nodes = []


def key_times(ctrl):
    times = set()
    if ctrl is None:
        return times
    try:
        for k in ctrl.keys:
            times.add(round(float(k.time.frame), 3))
    except Exception:  # noqa: BLE001
        pass
    try:
        n = ctrl.numSubs
        for i in range(1, n + 1):
            sub = rt.getSubAnim(ctrl, i)
            if sub is not None and sub.controller is not None:
                times |= key_times(sub.controller)
    except Exception:  # noqa: BLE001
        pass
    return times


for n in rt.objects:
    t = key_times(n.controller)
    if t:
        nodes.append({'name': n.name, 'class': str(rt.classOf(n)), 'ctrl': str(rt.classOf(n.controller)), 'keys': len(t),
                      'times': sorted(t)[:60]})
rep['n_objects'] = len(list(rt.objects))
# Interpolation type of the first rotation key on a few bones (Bezier / Linear / TCB ...).
try:
    rep['sample_rot'] = str(rt.execute(
        "(local c = (getNodeByName \"Bip001 R Forearm\").rotation.controller; "
        "local s = c[1].controller; local k = s.keys[2]; "
        "(classof c) as string + \" / \" + (classof s) as string + \" key2@\" + (k.time as string) + \" in=\" + "
        "(k.inTangentType as string) + \" out=\" + (k.outTangentType as string))"))
    # rotation sampled at every frame between two keys: smooth (varies each frame) vs stepped (constant)
    rep['sample_curve'] = str(rt.execute(
        "(local n = getNodeByName \"Bip001 R Forearm\"; local o = #(); "
        "for f = 0 to 16 do append o ((at time f (n.rotation.controller[1].value)) as string); o as string)"))
except Exception as e:  # noqa: BLE001
    rep['sample_error'] = str(e)
rep['animated'] = len(nodes)
nodes.sort(key=lambda x: -x['keys'])
rep['nodes'] = nodes[:12]
rep['key_count_hist'] = {}
for x in nodes:
    rep['key_count_hist'][x['keys']] = rep['key_count_hist'].get(x['keys'], 0) + 1
with open(out, 'w', encoding='utf-8') as f:
    json.dump(rep, f, ensure_ascii=False, indent=1)
