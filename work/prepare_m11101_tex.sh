#!/bin/bash
# New textured, gun-free m11101 model: prepare only (reference picture).  New project; nothing else is touched.
set -e
P=/data/video2rig/projects/m11101_tex
M=/data/video2rig/inputs/11101_nogun_exam_textured.fbx
if [ -e $P ]; then echo "$P exists, abort"; exit 1; fi
if pgrep -f "python -m v2r\.cli (gen|train|exam|animate|prepare) " >/dev/null; then echo "BUSY"; pgrep -af "python -m v2r\.cli"; exit 1; fi
cd /data/video2rig/code
mkdir -p $P
set +e
/data/video2rig/.venv/bin/python -m v2r.cli prepare $M $P --facing=-Y --up=+Z --fist-points --offaxis-points \
  > /tmp/m11101_tex_prepare.log 2>&1
rc=$?
set -e
cp /tmp/m11101_tex_prepare.log $P/prepare.log
echo "rc=$rc"
grep -E "\[v2r\] (props|rebind|mesh .*procedural|rigid|bones=|reference picture|WARNING)|Error|Traceback" $P/prepare.log || true
[ $rc -eq 0 ] || exit $rc
/data/video2rig/.venv/bin/python - <<EOF
import json
a = json.load(open("$P/rig.json")); b = json.load(open("/data/video2rig/projects/m11101_nogun/rig.json"))
print("keypoints", len(a["keypoints"]), "vs m11101_nogun", len(b["keypoints"]),
      "same names:", [k["name"] for k in a["keypoints"]] == [k["name"] for k in b["keypoints"]])
print("active", sum(x["active"] for x in a["bones"]), "size", round(a["size"], 4), "vs", round(b["size"], 4))
print("prepare_opts", json.load(open("$P/prepare_opts.json")))
EOF
