import json
ref = json.load(open("/data/video2rig/projects/m11101/rig.json"))
r = json.load(open("/tmp/v2r_dropprops/projects/noprops/rig.json"))
a0 = {b["name"] for b in ref["bones"] if b["active"]}
a1 = {b["name"] for b in r["bones"] if b["active"]}
print("active only in current:", sorted(a0 - a1), " only in noprops:", sorted(a1 - a0))
for n in sorted(a0 ^ a1):
    i = [b["name"] for b in r["bones"]].index(n)
    for tag, rr in (("current", ref), ("noprops", r)):
        b = rr["bones"][i]
        print(f"  {tag:8s} {n}: active {b['active']} folded {b.get('folded')} helper_of {b.get('helper_of')} "
              f"rigid {b.get('rigid_accessory')} n_moved {b.get('n_moved')} reach {b.get('reach', 0):.3f}")
kp = {k["name"]: k for k in r["keypoints"]}
for n in ("Bip001 Spine", "Bip001", "Bip001 Pelvis"):
    print(n, "in noprops:", n in kp, "in current:", n in {k["name"] for k in ref["keypoints"]},
          kp.get(n, {}).get("rest_world"))
w = [k for k in ref["keypoints"] if k["name"] == "22101_weapon"][0]
print("22101_weapon rest", w["rest_world"])
sp = [b for b in ref["bones"] if b["name"] == "Bip001 Spine"][0]
print("Spine head", sp.get("head_world"))
st0 = json.load(open("/data/video2rig/projects/m11101/studio.json"))
st1 = json.load(open("/tmp/v2r_dropprops/projects/noprops/studio.json"))
print("studio distance", st0["distance"], "->", st1["distance"], " target", st0["target"], "->", st1["target"])
