#!/bin/bash
# Move superseded static releases out of the public root into the non-public backup of the
# release that replaced them, then remove this deploy's temp files (exact names only).
set -eu
S=/data/video2rig/code/web/static
LIVE=ui-v1-290ca5592cf5
grep -q "/static/releases/$LIVE/app.js" "$S/index.html"
move_old() {  # old_release  backup_dir
  local old=$1 b=$2
  [ -d "$S/releases/$old" ] || return 0
  ! grep -q "$old" "$S/index.html"
  mkdir -p "$b/previous-static"
  test ! -e "$b/previous-static/$old"
  mv -- "$S/releases/$old" "$b/previous-static/$old"
  echo "archived $old -> $b/previous-static/"
}
move_old ui-v1-37a15bf91586 /data/video2rig/web-release-backups/ui-v1-375e02edc095
mkdir -p /data/video2rig/web-release-backups/$LIVE
move_old ui-v1-375e02edc095 /data/video2rig/web-release-backups/$LIVE
ls -la "$S/releases/"
rm -f -- /tmp/validation-ca05bc934dd1.tar.gz /tmp/kf-tools.tar.gz /tmp/ui-v1-375e02edc095.tar.gz /tmp/ui-v1-290ca5592cf5.tar.gz \
         /tmp/deploy_web_batch.py /tmp/activate_web_ui.py /tmp/v2r-kf-validate2.sh /tmp/v2r-kf-install-tools.sh /tmp/v2r-kf-preflight.sh \
         /tmp/v2r-kf-install.sh /tmp/v2r-kf-validate.sh
rm -rf -- /tmp/rel-ui-v1-375e02edc095 /tmp/rel-ui-v1-290ca5592cf5 /tmp/v2r-web-validation-ca05bc934dd1
ls /tmp | grep -E '^(v2r-kf|v2r-keyframes|kf-tools|rel-ui-v1-(375|290)|v2r-web-validation-ca05)' || echo "tmp clean (this deploy)"
systemctl is-active video2rig-web; systemctl show video2rig-web -p MainPID
