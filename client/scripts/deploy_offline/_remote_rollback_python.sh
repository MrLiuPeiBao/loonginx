set -e
# rollback to previously runnable python tree
if [ -d /opt/python3.9.bad.20260509165705 ]; then
  rm -rf /opt/python3.9.broken.current || true
  if [ -d /opt/python3.9 ]; then
    mv /opt/python3.9 /opt/python3.9.broken.current
  fi
  mv /opt/python3.9.bad.20260509165705 /opt/python3.9
fi

ls -la /opt | sed -n '1,80p'
/opt/python3.9/bin/python3.9 -V || true
/opt/python3.9/bin/python3.9 - << 'PY' || true
import sys
print('exe=', sys.executable)
for mod in ('zlib','pip'):
    try:
        __import__(mod)
        print(mod, 'OK')
    except Exception as e:
        print(mod, 'FAIL', e)
PY