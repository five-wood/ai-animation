# drop-props on by default: check the three ways prepare can run (scratch code dir, scratch projects).
set -e
C=/tmp/v2r_dropprops
cp /tmp/dropprops_upload/cli.py $C/v2r/
PY=/data/video2rig/.venv/bin/python
M=/data/video2rig/inputs/11101_01.fbx
cd $C
rm -rf $C/projects/d_default $C/projects/d_keep $C/projects/d_template
OPTS="--facing=-Y --up=+Z --procedural-material --fist-points --offaxis-points"
$PY -m v2r.cli prepare $M $C/projects/d_default $OPTS > $C/d_default.log 2>&1 || { tail -20 $C/d_default.log; exit 1; }
$PY -m v2r.cli prepare $M $C/projects/d_keep $OPTS --no-drop-props > $C/d_keep.log 2>&1 || { tail -20 $C/d_keep.log; exit 1; }
# a template prepared before the default existed (no drop_props key): its props must be kept
$PY -m v2r.cli prepare $M $C/projects/d_template $OPTS --geometry-from /data/video2rig/projects/m11101 > $C/d_template.log 2>&1 || { tail -20 $C/d_template.log; exit 1; }
$PY -m v2r.cli --help >/dev/null
$PY -m v2r.cli prepare --help | grep -A3 "drop-props"
$PY - <<'EOF'
import json
ref = json.load(open("/data/video2rig/projects/m11101/rig.json"))
nop = json.load(open("/tmp/v2r_dropprops/projects/noprops/rig.json"))
same = lambda a, b: json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
for tag, want in (("d_default", nop), ("d_keep", ref), ("d_template", ref)):
    P = f"/tmp/v2r_dropprops/projects/{tag}"
    r = json.load(open(f"{P}/rig.json"))
    o = json.load(open(f"{P}/prepare_opts.json"))
    print(f"[{tag}] drop_props={o.get('drop_props')} keypoints {len(r['keypoints'])} "
          f"rig as expected ({'no-props' if want is nop else 'current m11101'}): {same(r, want)}")
EOF
for t in d_default d_keep d_template; do echo "$t: $(grep -c 'props dropped' $C/$t.log) 'props dropped' lines"; done
echo DONE
