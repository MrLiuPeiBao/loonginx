set -e
echo '=== LIB FILES ==='
ls -l /opt/python3.9/lib/libpython3.9.so* 2>/dev/null || true
ls -l /opt/python3.9.backup.20260509162244/lib/libpython3.9.so* 2>/dev/null || true

echo '=== LD TOOLS ==='
command -v ldconfig || true
ls -l /etc/ld.so.conf /etc/ld.so.conf.d 2>/dev/null || true

echo '=== TEST WITH LD_LIBRARY_PATH ==='
LD_LIBRARY_PATH=/opt/python3.9/lib /opt/python3.9/bin/python3.9 -V || true
LD_LIBRARY_PATH=/opt/python3.9.backup.20260509162244/lib /opt/python3.9.backup.20260509162244/bin/python3.9 -V || true