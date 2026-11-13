#!/bin/bash
# Install the keyframe extraction scripts as a NEW directory code/tools/max2026 (nothing existing is replaced).
set -eu
SHA=7ff16e5bd407d5001bfc7f0cc8622cf0165a576b5dffa4b4e122480e12dd7712
CODE=/data/video2rig/code
DEST=$CODE/tools/max2026
echo "$SHA  /tmp/kf-tools.tar.gz" | sha256sum -c -
test -d "$CODE/tools"
test ! -e "$DEST"
STAGE=$(mktemp -d /tmp/v2r-kf-tools-XXXX)
tar -xzf /tmp/kf-tools.tar.gz -C "$STAGE"
cp -r "$STAGE/max2026" "$CODE/tools/.max2026.next"
mv "$CODE/tools/.max2026.next" "$DEST"
rm -rf "$STAGE"
cd "$DEST" && sha256sum *.py profiles/*.json
# backup record for the web release (tools did not exist before)
mkdir -p /data/video2rig/web-release-backups/tools-max2026
echo '{"installed": "code/tools/max2026", "existed_before": false, "archive_sha256": "'$SHA'"}' > /data/video2rig/web-release-backups/tools-max2026/record.json
echo INSTALLED
