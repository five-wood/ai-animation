import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
for k, v in d.items():
    print(k, {x: y for x, y in v.items() if x != "bones"})
for ex in ("exam", "exam_strike"):
    a, b = d[f"m11101/{ex}"]["bones"], d[f"m11101_nogun/{ex}"]["bones"]
    print(f"{ex} per bone (m11101 -> nogun):")
    for n in sorted(set(a) | set(b), key=lambda n: -max(a.get(n, 0), b.get(n, 0))):
        print(f"  {n:24s} {str(a.get(n, '-')):>6} -> {str(b.get(n, '-')):>6}")
