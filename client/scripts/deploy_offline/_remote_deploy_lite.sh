set -e
mkdir -p /tmp/deploy_client_offline/new_client
rm -rf /tmp/deploy_client_offline/new_client/*
cd /tmp/deploy_client_offline/new_client
# toybox tar on this board works reliably without leading ./ in archive entries
tar -xzf /tmp/deploy_client_offline/client-lite.tar.gz

if [ ! -x /opt/python3.9/bin/python3.9 ]; then
  echo "ERROR: /opt/python3.9/bin/python3.9 not found"
  exit 1
fi

/opt/python3.9/bin/python3.9 -m pip install --no-index --find-links /tmp/deploy_client_offline/new_client/scripts/wheelhouse -r /tmp/deploy_client_offline/new_client/requirements.txt

rm -rf /opt/loonginx-client.bak
if [ -d /opt/loonginx-client ]; then
  cp -a /opt/loonginx-client /opt/loonginx-client.bak
fi
rm -rf /opt/loonginx-client
mkdir -p /opt/loonginx-client
cp -a /tmp/deploy_client_offline/new_client/. /opt/loonginx-client/
mkdir -p /opt/loonginx-client/logs

pkill -f '/opt/loonginx-client/main.py' 2>/dev/null || true
nohup /opt/python3.9/bin/python3.9 /opt/loonginx-client/main.py >/opt/loonginx-client/logs/stdout.log 2>&1 &
sleep 4

if pgrep -af '/opt/loonginx-client/main.py' >/dev/null; then
  echo RUNNING_OK
  pgrep -af '/opt/loonginx-client/main.py'
  tail -n 40 /opt/loonginx-client/logs/stdout.log || true
else
  echo RUNNING_FAIL
  tail -n 120 /opt/loonginx-client/logs/stdout.log || true
  exit 1
fi