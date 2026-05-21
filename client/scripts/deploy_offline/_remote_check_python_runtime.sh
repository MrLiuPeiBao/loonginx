set -e
export LD_LIBRARY_PATH=/opt/python3.9/lib:${LD_LIBRARY_PATH}
/opt/python3.9/bin/python3.9 -V
/opt/python3.9/bin/python3.9 - << 'PY'
import zlib, ensurepip
print('PY_ZLIB_OK')
PY