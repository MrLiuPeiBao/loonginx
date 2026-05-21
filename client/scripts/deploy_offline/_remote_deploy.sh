set -e
mkdir -p /tmp/deploy_client_offline/wheelhouse
rm -rf /tmp/deploy_client_offline/new_client
mkdir -p /tmp/deploy_client_offline/new_client

cd /tmp/deploy_client_offline/new_client
tar -xzf /tmp/deploy_client_offline/client-offline-package.tar.gz

if [ ! -x /opt/python3.9/bin/python3.9 ]; then
  echo "ERROR: /opt/python3.9/bin/python3.9 not found"
  exit 1
fi

/opt/python3.9/bin/python3.9 -m pip install --no-index --find-links /tmp/deploy_client_offline/wheelhouse -r /tmp/deploy_client_offline/requirements.txt

if [ -d /opt/loonginx-client ]; then
  rm -rf /opt/loonginx-client.bak
  cp -a /opt/loonginx-client /opt/loonginx-client.bak
fi

mkdir -p /opt/loonginx-client
cp -a /tmp/deploy_client_offline/new_client/. /opt/loonginx-client/
mkdir -p /opt/loonginx-client/logs

pkill -f '/opt/loonginx-client/main.py' 2>/dev/null || true
nohup /opt/python3.9/bin/python3.9 /opt/loonginx-client/main.py >/opt/loonginx-client/logs/stdout.log 2>&1 &
sleep 4

if pgrep -af '/opt/loonginx-client/main.py' >/dev/null; then
  echo RUNNING_OK
  pgrep -af '/opt/loonginx-client/main.py'
else
  echo RUNNING_FAIL
  tail -n 80 /opt/loonginx-client/logs/stdout.log || true
  exit 1
fi