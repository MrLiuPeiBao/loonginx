set -e
for p in /opt/python3.9 /opt/python3.9.backup.*; do
  [ -d "$p" ] || continue
  echo "=== $p ==="
  if [ -x "$p/bin/python3.9" ]; then
    $p/bin/python3.9 -V || true
    $p/bin/python3.9 - << 'PY' || true
import sys
print('zlib_ok=', end='')
try:
    import zlib
    print(True)
except Exception as e:
    print(False, e)
print('pip_ok=', end='')
try:
    import pip
    print(True)
except Exception as e:
    print(False, e)
PY
  else
    echo "python3.9 missing"
  fi
done