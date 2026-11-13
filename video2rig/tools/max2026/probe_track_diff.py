"""Read-only: dump key times/values/props of one track path in base_scene.max and spline_edit.max."""
import json, traceback
import pymxs
rt = pymxs.runtime
RUN = r'D:\Project\ai-animation\work\max_editable_20261111_spline'
FILES = [r'D:\Project\ai-animation\work\max_editable_20261109\base_scene.max', RUN + r'\spline_edit.max']
NODE = 'Dumm_input'


def dump():
    n = rt.getNodeByName(NODE)
    out = {'node_controller': str(rt.classOf(n.controller)), 'parent': str(n.parent.name) if n.parent else None, 'tracks': []}

    def scan(c, path, depth=0):
        if depth > 6:
            return
        rec = {'path': path, 'class': str(rt.classOf(c))}
        try:
            k = int(rt.numKeys(c))
        except Exception:
            k = -1
        rec['num_keys'] = k
        if k > 0:
            rec['keys'] = []
            for i in range(1, k + 1):
                t = rt.getKeyTime(c, i)
                item = {'ticks': int(t.ticks)}
                try:
                    key = rt.getKey(c, i)
                    item['props'] = {str(p): str(rt.getProperty(key, p)) for p in rt.getPropNames(key)}
                except Exception as e:
                    item['props_error'] = repr(e)
                with pymxs.attime(float(t.frame)):
                    try:
                        item['value_at_time'] = str(c.value)
                    except Exception as e:
                        item['value_at_time'] = repr(e)
                rec['keys'].append(item)
        out['tracks'].append(rec)
        for i in range(1, int(c.numSubs) + 1):
            try:
                ch = rt.getSubAnim(c, i).controller
            except Exception:
                continue
            if ch is not None:
                scan(ch, path + '/' + str(rt.getSubAnimName(c, i)), depth + 1)
    scan(n.controller, NODE)
    # link constraint targets
    try:
        lc = n.controller
        out['link_targets'] = [[str(lc.getNode(i).name), int(lc.getFrameNo(i))] for i in range(1, int(lc.getNumTargets()) + 1)]
    except Exception as e:
        out['link_targets_error'] = repr(e)
    return out


res = {}
try:
    for f in FILES:
        rt.loadMaxFile(f, quiet=True)
        res[f] = dump()
except Exception:
    res['error'] = traceback.format_exc()
with open(RUN + r'\track_diff_probe.json', 'w', encoding='utf8') as fh:
    json.dump(res, fh, ensure_ascii=False, indent=1)
print('done')
