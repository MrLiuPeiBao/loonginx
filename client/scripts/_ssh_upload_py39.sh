#!/usr/bin/env bash
set -euo pipefail
PW='123'
REMOTE='root@192.168.1.10'
BASE='/mnt/c/Users/lpb/Desktop/loonginx/client'
ART="$BASE/scripts/python3.9.19-loongarch64-glibc228.tar.gz"
REQ="$BASE/requirements.txt"
WH="$BASE/scripts/wheelhouse"

sshpass -p "$PW" ssh -o StrictHostKeyChecking=accept-new "$REMOTE" 'mkdir -p /tmp/deploy_py39/wheelhouse'
sshpass -p "$PW" scp -o StrictHostKeyChecking=accept-new "$ART" "$REMOTE:/tmp/deploy_py39/"
sshpass -p "$PW" scp -o StrictHostKeyChecking=accept-new "$REQ" "$REMOTE:/tmp/deploy_py39/"
sshpass -p "$PW" scp -o StrictHostKeyChecking=accept-new "$WH"/* "$REMOTE:/tmp/deploy_py39/wheelhouse/"
echo UPLOAD_DONE
