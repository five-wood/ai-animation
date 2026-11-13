# Deploy the user studio (video2rig/studio) to the training server and (re)start its service on :8700.
# Only copies studio/ (+ the service unit). Never touches web/, v2r/, projects or the 8600 dashboard.
#   .\video2rig\tools\deploy_studio.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tmp = Join-Path $env:TEMP "studio_deploy.tgz"
tar -czf $tmp -C $here --exclude=__pycache__ studio
scp -q $tmp animsrv:/tmp/studio_deploy.tgz
ssh animsrv @"
set -e
cd /data/video2rig/code
rm -rf /tmp/studio_new && mkdir -p /tmp/studio_new && tar -xzf /tmp/studio_deploy.tgz -C /tmp/studio_new
rm -rf studio.prev && if [ -d studio ]; then mv studio studio.prev; fi
mv /tmp/studio_new/studio studio
mkdir -p /data/video2rig/studio && chmod 700 /data/video2rig/studio
if [ ! -f /data/video2rig/studio/studio.env ]; then
  umask 077
  echo "STUDIO_ADMIN_USER=admin" > /data/video2rig/studio/studio.env
  echo "STUDIO_ADMIN_PASSWORD=`$(head -c 12 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 12)" >> /data/video2rig/studio/studio.env
  echo "STUDIO_PUBLISH=m11101_tex:11101 怪物,lizard:蜥蜴" >> /data/video2rig/studio/studio.env
  echo "created studio.env (admin password inside)"
fi
cp studio/video2rig-studio.service /etc/systemd/system/video2rig-studio.service
systemctl daemon-reload
systemctl enable video2rig-studio >/dev/null 2>&1
systemctl restart video2rig-studio
sleep 3
systemctl is-active video2rig-studio
curl -fsS http://127.0.0.1:8700/healthz
"@
Write-Host "deployed -> http://10.0.3.117:8700"
