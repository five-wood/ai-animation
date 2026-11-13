#!/bin/bash
# After a verified deploy: move the previous static release out of the public root (into the
# non-public backup, same convention as batch two) and remove this deploy's /tmp files.
set -eu
REL=ui-v1-37a15bf91586
OLD=ui-v1-056895d632eb
S=/data/video2rig/code/web/static
B=/data/video2rig/web-release-backups/$REL
grep -q "/static/releases/$REL/app.js" "$S/index.html"            # live entry is the new release
! grep -q "$OLD" "$S/index.html"
test -d "$B" && test ! -e "$B/previous-static"
mkdir -p "$B/previous-static"
mv -- "$S/releases/$OLD" "$B/previous-static/$OLD"
ls -la "$S/releases/" "$B" "$B/previous-static"
# temp files of this deploy (exact names only)
rm -f -- /tmp/validation-b5728969c90d.tar.gz /tmp/$REL.tar.gz /tmp/deploy_web_batch.py /tmp/keyframes_auto.json \
         /tmp/v2r-kf-validate.sh /tmp/v2r-kf-install.sh
rm -rf -- /tmp/rel-$REL /tmp/v2r-web-validation-b5728969c90d
ls /tmp | grep -E 'v2r|keyframes|ui-v1' || echo "tmp clean"
systemctl is-active video2rig-web; systemctl show video2rig-web -p MainPID
