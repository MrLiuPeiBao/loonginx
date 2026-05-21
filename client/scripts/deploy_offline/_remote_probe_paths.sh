set -e
echo '=== PATH ==='
echo "$PATH"
echo '=== PY BIN FILES ==='
ls -l /usr/bin/python* /bin/python* /opt/python3.9*/bin/python* 2>/dev/null || true
echo '=== /opt LIST ==='
ls -la /opt 2>/dev/null || true
echo '=== TRY EXEC ==='
for p in /usr/bin/python3 /usr/bin/python3.9 /bin/python3 /bin/python3.9 /opt/python3.9/bin/python3.9; do
  if [ -x "$p" ]; then
    echo "-- $p --"
    $p -V || true
  fi
done