#!/usr/bin/env bash
set -euo pipefail

TC=/root/loonginx-build/toolchain/loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.2
SYSROOT="$TC/loongarch64-linux-gnu/sysroot"
GCCLIB="$TC/lib/gcc/loongarch64-linux-gnu/8.3.0"

cat > /tmp/h.c <<'EOF'
int main(void) { return 0; }
EOF

loongarch64-linux-gnu-gcc-14 -nostartfiles --sysroot="$SYSROOT" \
  "$SYSROOT/usr/lib64/crt1.o" "$SYSROOT/usr/lib64/crti.o" "$GCCLIB/crtbegin.o" \
  /tmp/h.c \
  -L"$SYSROOT/usr/lib64" -L"$GCCLIB" \
  -Wl,-dynamic-linker,/lib64/ld.so.1 \
  -lc -lgcc -lgcc_s \
  "$GCCLIB/crtend.o" "$SYSROOT/usr/lib64/crtn.o" \
  -o /tmp/h_glibc228

file /tmp/h_glibc228
readelf --version-info /tmp/h_glibc228 | grep -E 'GLIBC_[0-9]+' | sort -u
readelf -l /tmp/h_glibc228 | grep -i interpreter
