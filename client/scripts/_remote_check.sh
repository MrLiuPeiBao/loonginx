set -e
echo ====SYS====
uname -a
echo ====DIR====
ls -la /opt 2>/dev/null || true
ls -la /opt/loonginx-client 2>/dev/null || true
echo ====PY====
/opt/python3.9/bin/python3.9 -V 2>/dev/null || python3.9 -V 2>/dev/null || true
echo ====SVC====
systemctl status loonginx-client --no-pager 2>/dev/null | sed -n "1,12p" || true
echo ====PROC====
pgrep -af main.py || true