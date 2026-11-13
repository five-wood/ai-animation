"""Read-only: for every leaf track with >=2 keys, report whether the keyed values differ."""
import json, traceback
import pymxs
rt = pymxs.runtime
OUT = r'D:\Project\ai-animation\work\max_editable_20261111_spline\multikey_probe.json'
res = {'file': str(rt.maxFilePath) + str(rt.maxFileName), 'range': [float(rt.animationRange.start.frame), float(rt.animationRange.end.frame)], 'tracks': []}
try:
    seen = set()

    def scan(c, path):
        try:
            h = int(rt.getHandleByAnim(c))
        except Exception:
            return
        if h in seen:
            return
        seen.add(h)
        try:
            n = int(rt.numKeys(c))
        except Exception:
            n = -1
        subs = []
        for i in range(1, int(c.numSubs) + 1):
            try:
                ch = rt.getSubAnim(c, i).controller
            except Exception:
                continue
            if ch is not None:
                subs.append((ch, path + '/' + str(rt.getSubAnimName(c, i))))
        if n >= 2 and not subs:
            vals = []
            for i in range(1, n + 1):
                t = rt.getKeyTime(c, i)
                with pymxs.attime(float(t.frame)):
                    try:
                        vals.append([float(t.frame), str(c.value)])
                    except Exception as e:
                        vals.append([float(t.frame), repr(e)])
            res['tracks'].append({'path': path, 'class': str(rt.classOf(c)), 'keys': vals,
                                  'values_differ': len({v for _, v in vals}) > 1})
        for ch, p in subs:
            scan(ch, p)
    for nd in list(rt.objects):
        scan(nd.controller, str(nd.name))
except Exception:
    res['error'] = traceback.format_exc()
with open(OUT, 'w', encoding='utf8') as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
print('done')
