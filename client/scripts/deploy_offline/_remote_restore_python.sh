set -e
cd /
TS=$(date +%Y%m%d%H%M%S)
if [ -d /opt/python3.9 ]; then
  mv /opt/python3.9 /opt/python3.9.bad.$TS
fi
tar -xzf /tmp/deploy_client_offline/python3.9.19-loongarch64-glibc228-fixed-board.tar.gz
/opt/python3.9/bin/python3.9 -V
/opt/python3.9/bin/python3.9 - << 'PY'
import zlib, ensurepip
print('PY_ZLIB_OK')
PY