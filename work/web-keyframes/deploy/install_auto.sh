#!/bin/bash
# Put the candidate file into the ladingwu_temporal result dir (new file only) and remove the stray copy at /.
set -eu
SHA=bbd1f063a2016bc07182465dce9fd32ec07c43299292a6ed1ae83d5d5d85a958
D=/data/video2rig/projects/m11101_tex/out/ladingwu_temporal
test -d "$D"
test ! -e "$D/keyframes_auto.json"
echo "$SHA  /tmp/keyframes_auto.json" | sha256sum -c -
cp /tmp/keyframes_auto.json "$D/.keyframes_auto.json.tmp"
mv "$D/.keyframes_auto.json.tmp" "$D/keyframes_auto.json"
ls -la "$D/keyframes_auto.json"
sha256sum "$D/keyframes_auto.json"
# stray copy created by a quoting mistake at 10:21; remove only if it is exactly that file
if [ -f /keyframes_auto.json ] && [ "$(sha256sum /keyframes_auto.json | cut -d' ' -f1)" = "$SHA" ]; then
  rm -f -- /keyframes_auto.json
  echo "removed stray /keyframes_auto.json"
fi
ls -la /keyframes_auto.json 2>&1 || true
echo "inputs unchanged:"; sha256sum "$D/motion.npz" "$D/reading.npz" "$D/summary.json"
