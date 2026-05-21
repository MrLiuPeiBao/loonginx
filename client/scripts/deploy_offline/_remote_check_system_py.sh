set -e
echo '=== PY PATH ==='
command -v python3 || true
python3 -V || true
echo '=== PY CHECK ==='
python3 - << 'PY'
import sys
print('exe=', sys.executable)
print('ver=', sys.version)
for mod in ('zlib','serial','minimalmodbus','paho.mqtt.client'):
    try:
        __import__(mod)
        print(mod, 'OK')
    except Exception as e:
        print(mod, 'FAIL', e)
PY