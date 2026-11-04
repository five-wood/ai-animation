import json, sys, os
p = sys.argv[1]
r = json.load(open(os.path.join(p, "exam", "exam_report.json")))
rig = json.load(open(os.path.join(p, "rig.json")))
L = {b["name"]: b["length"] for b in rig["bones"]}
for n, v in r["per_bone"].items():
    print("%-8s len %.3f mean %5.1f p90 %5.1f" % (n, L[n], v["mean_deg"], v["p90_deg"]))
print({k: round(v, 2) for k, v in r["per_keypoint_pct"].items()})
