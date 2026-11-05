# Verify prepare --drop-props on m11101 in a scratch code dir and scratch projects (the real project is only read).
set -e
C=/tmp/v2r_dropprops
rm -rf $C && mkdir -p $C/projects
cp -r /data/video2rig/code/v2r $C/
cp /tmp/dropprops_upload/common.py /tmp/dropprops_upload/prepare.py /tmp/dropprops_upload/gen_poses.py \
   /tmp/dropprops_upload/export_anim.py /tmp/dropprops_upload/inspect_model.py $C/v2r/blender/
cp /tmp/dropprops_upload/cli.py $C/v2r/
PY=/data/video2rig/.venv/bin/python
M=/data/video2rig/inputs/11101_01.fbx
cd $C
echo "== inspect"
$PY -m v2r.cli inspect $M | grep -E "roots|props"
echo "== prepare (default: must reproduce the current rig)"
$PY -m v2r.cli prepare $M $C/projects/default --facing=-Y --up=+Z --procedural-material --fist-points --offaxis-points \
  > $C/prepare_default.log 2>&1 || { tail -30 $C/prepare_default.log; exit 1; }
echo "== prepare --drop-props"
$PY -m v2r.cli prepare $M $C/projects/noprops --facing=-Y --up=+Z --procedural-material --fist-points --offaxis-points --drop-props \
  > $C/prepare_noprops.log 2>&1 || { tail -30 $C/prepare_noprops.log; exit 1; }
grep -E "props dropped|bones=" $C/prepare_noprops.log
$PY - <<'EOF'
import json
ref = json.load(open("/data/video2rig/projects/m11101/rig.json"))
st_ref = json.load(open("/data/video2rig/projects/m11101/studio.json"))
for tag in ("default", "noprops"):
    r = json.load(open(f"/tmp/v2r_dropprops/projects/{tag}/rig.json"))
    s = json.load(open(f"/tmp/v2r_dropprops/projects/{tag}/studio.json"))
    same_rig = json.dumps(r, sort_keys=True) == json.dumps(ref, sort_keys=True)
    same_studio = json.dumps(s, sort_keys=True) == json.dumps(st_ref, sort_keys=True)
    kn = [k["name"] for k in r["keypoints"]]
    gone = [k["name"] for k in ref["keypoints"] if k["name"] not in kn]
    new = [n for n in kn if n not in [k["name"] for k in ref["keypoints"]]]
    act = [b["name"] for b in r["bones"] if b["active"]]
    act_ref = [b["name"] for b in ref["bones"] if b["active"]]
    print(f"[{tag}] rig identical to current m11101: {same_rig}; studio identical: {same_studio}")
    print(f"   bones {len(r['bones'])} keypoints {len(kn)} (current {len(ref['keypoints'])}) removed {gone} added {new}")
    print(f"   active {len(act)} same active set {act == act_ref}; size {r['size']:.4f} (current {ref['size']:.4f}) "
          f"ground_z {r['ground_z']:.4f} ({ref['ground_z']:.4f}) bbox {[[round(x, 3) for x in v] for v in r['bbox']]}")
    if not same_rig and tag == "default":
        diff = [k for k in ref if json.dumps(ref[k], sort_keys=True) != json.dumps(r.get(k), sort_keys=True)]
        print("   differing keys:", diff)
EOF
echo "== gen 4 random poses (no-props project)"
$PY -m v2r.cli gen $C/projects/noprops --n 4 --workers 1 > $C/gen.log 2>&1 || { tail -30 $C/gen.log; cat $C/projects/noprops/logs/*.log | tail -30; exit 1; }
tail -2 $C/gen.log
grep -E "props dropped|Error|Traceback" $C/projects/noprops/logs/*.log || true
ls $C/projects/noprops/train_data | head
cd $C/projects/noprops && $PY -c "
from PIL import Image
a=Image.open('train_data/000000_front.png'); b=Image.open('train_data/000000_side.png')
o=Image.new('RGBA',(a.width*2,a.height),(200,200,200,255)); o.alpha_composite(a.convert('RGBA'),(0,0)); o.alpha_composite(b.convert('RGBA'),(a.width,0)); o.convert('RGB').save('/tmp/v2r_dropprops/gen_sample.png')"
echo DONE
