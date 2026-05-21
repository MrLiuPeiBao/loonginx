set -e
/opt/python3.9/bin/python3.9 -V
/opt/python3.9/bin/python3.9 -m ensurepip --upgrade || true
/opt/python3.9/bin/python3.9 -m pip --version || true
ls -l /opt/python3.9/bin | grep pip || true