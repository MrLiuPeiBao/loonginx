#!/usr/bin/env bash
set -euo pipefail
PW='123'
REMOTE='root@192.168.1.10'

sshpass -p "$PW" ssh -o StrictHostKeyChecking=accept-new "$REMOTE" 'set -e
cd /
tar -xzf /tmp/deploy_py39/python3.9.19-loongarch64-glibc228.tar.gz
test -x /opt/python3.9/bin/python3.9
/opt/python3.9/bin/python3.9 -V
/opt/python3.9/bin/python3.9 -m pip install --no-index --find-links /tmp/deploy_py39/wheelhouse -r /tmp/deploy_py39/requirements.txt
/opt/python3.9/bin/python3.9 - << "PY"
import serial
import minimalmodbus
import paho.mqtt.client as mqtt
print("IMPORT_OK")
PY
'
