#!/bin/bash
# Read-only: what did the generate job change in the result dir, and the job record.
set -u
D=/data/video2rig/projects/m11101_nogun/out/112_temporal
cd "$D"
sha256sum * 2>/dev/null | sort -k2 > /tmp/v2r-kf-after.txt
echo "--- changed (should be empty):"
join -1 2 -2 2 /tmp/v2r-kf-before.txt /tmp/v2r-kf-after.txt | awk '$2!=$3'
echo "--- removed (should be empty):"
comm -23 <(awk '{print $2}' /tmp/v2r-kf-before.txt) <(awk '{print $2}' /tmp/v2r-kf-after.txt)
echo "--- added:"
comm -13 <(awk '{print $2}' /tmp/v2r-kf-before.txt) <(awk '{print $2}' /tmp/v2r-kf-after.txt)
rm -f /tmp/v2r-kf-before.txt /tmp/v2r-kf-after.txt
J=$(ls -t /data/video2rig/jobs/*keyframes*.json | head -1)
/data/video2rig/.venv/bin/python - "$J" <<'EOF'
import json, sys
j = json.load(open(sys.argv[1]))
print('job', j['id'], j['status'], 'rc', j['rc'], 'argv', j['argv'][1:], 'took %.1fs' % (j['ended'] - j['started']))
EOF
tail -2 "${J%.json}.log"
echo "leftover temp dirs: $(ls /tmp | grep -c '^v2r-keyframes-' || true)"
