"""Read-only 3ds Max scene/profile export. Run inside Max 2026, not system Python.
Does not change keys, selection, modes, hierarchy, current frame, or save the scene.
"""
import json
import math
import os
import traceback
from datetime import datetime

import pymxs
rt = pymxs.runtime
OUT = r"D:\Project\ai-animation\work\max_probe"
os.makedirs(OUT, exist_ok=True)


def safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:
        return default if default is not None else {"error": str(exc)}


def cls(x):
    return str(rt.classOf(x))


def vec(p):
    return [float(p.x), float(p.y), float(p.z)]


def matrix(m):
    # Max row-vector matrix3, fourth row is translation.
    return [vec(m.row1), vec(m.row2), vec(m.row3), vec(m.row4)]


def props(x):
    result = {}
    for p in safe(lambda: list(rt.getPropNames(x)), []):
        name = str(p)
        value = safe(lambda: rt.getProperty(x, p))
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[name] = value
        else:
            result[name] = str(value)[:300]
    return result


def tracks(x, depth=0, seen=None):
    seen = set() if seen is None else seen
    if x is None or depth > 4:
        return None
    handle = safe(lambda: int(rt.getHandleByAnim(x)), -1)
    if handle != -1 and handle in seen:
        return {"class": cls(x), "ref": handle}
    if handle != -1:
        seen.add(handle)
    result = {"class": cls(x), "handle": handle}
    n = safe(lambda: int(rt.numKeys(x)), -1)
    result["num_keys"] = n
    if n > 0:
        result["key_times_ticks"] = [safe(lambda j=j: int(rt.getKeyTime(x, j).ticks), -1)
                                       for j in range(1, min(n, 5000) + 1)]
        result["first_key"] = safe(lambda: props(rt.getKey(x, 1)))
    children = []
    count = safe(lambda: int(rt.numSubs(x)), 0)
    for i in range(1, min(count, 60) + 1):
        sub = safe(lambda i=i: rt.getSubAnim(x, i), None)
        if isinstance(sub, dict) or sub is None:
            continue
        ctrl = safe(lambda: rt.getProperty(sub, rt.Name("controller")), None)
        if ctrl is None or isinstance(ctrl, dict):
            continue
        children.append({"index": i, "name": safe(lambda i=i: str(rt.getSubAnimName(x, i)), "?"),
                         "track": tracks(ctrl, depth + 1, seen)})
    if children:
        result["children"] = children
    return result


def main():
    data = {"schema": "v2r.max.scene-inspection/1", "read_only": True,
            "created": datetime.now().isoformat(),
            "file": str(rt.maxFilePath) + str(rt.maxFileName),
            "max_version": safe(lambda: str(rt.maxVersion())),
            "fps": int(rt.frameRate), "ticks_per_frame": int(rt.ticksPerFrame),
            "range_ticks": [int(rt.animationRange.start.ticks), int(rt.animationRange.end.ticks)],
            "current_ticks": int(rt.sliderTime.ticks),
            "units": {"system_type": str(rt.units.SystemType),
                      "system_scale": float(rt.units.SystemScale),
                      "display_type": str(rt.units.DisplayType),
                      "one_meter_in_system_units": safe(lambda: float(rt.units.decodeValue("1m")))},
            "nodes": [], "biped_roots": []}
    for n in list(rt.objects):
        c = cls(n)
        ctrl = safe(lambda: n.controller, None)
        parent = safe(lambda: n.parent, None)
        item = {"name": str(n.name), "handle": int(n.handle), "class": c,
                "parent": str(parent.name) if parent else None,
                "controller_class": cls(ctrl) if ctrl is not None else None,
                "hidden": safe(lambda: bool(n.isHidden)),
                "frozen": safe(lambda: bool(n.isFrozen))}
        if c in ("Biped_Object", "BoneGeometry", "Dummy", "Point", "IK_Chain") or "Bip" in str(n.name) or "Bone" in str(n.name):
            item["controller"] = tracks(ctrl)
            item["transform_current"] = matrix(n.transform)
            with pymxs.attime(0):
                item["transform_frame0"] = matrix(n.transform)
            if c == "Biped_Object":
                item["biped_controller_properties"] = props(ctrl)
                if ctrl is not None and "Vertical_Horizontal_Turn" in cls(ctrl):
                    data["biped_roots"].append({"name": str(n.name), "properties": props(ctrl),
                        "num_layers": safe(lambda: int(rt.biped.numLayers(ctrl))),
                        "current_layer": safe(lambda: int(rt.biped.getCurrentLayer(ctrl)))})
        item["modifiers"] = []
        for m in safe(lambda: list(n.modifiers), []):
            rec = {"name": str(m.name), "class": cls(m)}
            if cls(m) == "Skin":
                nb = safe(lambda: int(rt.skinOps.GetNumberBones(m)), 0)
                rec["bones"] = [safe(lambda i=i: str(rt.skinOps.GetBoneName(m, i, 0)), "?")
                                for i in range(1, nb + 1)]
            item["modifiers"].append(rec)
        data["nodes"].append(item)
    data["class_counts"] = {}
    for n in data["nodes"]:
        data["class_counts"][n["class"]] = data["class_counts"].get(n["class"], 0) + 1
    path = os.path.join(OUT, "scene_inspection.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "inspection.done"), "w", encoding="utf-8") as f:
        f.write(path)
    print("V2R read-only inspection complete:", path)


try:
    main()
except Exception:
    with open(os.path.join(OUT, "inspection.error.txt"), "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    raise
