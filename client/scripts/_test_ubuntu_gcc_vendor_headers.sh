#!/usr/bin/env bash
set -euo pipefail

TC=/root/loonginx-build/toolchain/loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.2
SYSROOT="$TC/loongarch64-linux-gnu/sysroot"
GCCLIB="$TC/lib/gcc/loongarch64-linux-gnu/8.3.0"
GCC14_INCLUDE=/usr/lib/gcc-cross/loongarch64-linux-gnu/14/include

cat > /tmp/uh.c <<'SRC'
#include <stdlib.h>
#include <wchar.h>
int main(int argc, char **argv) {
    wchar_t w[] = L"1";
    return (int)(strtol(argc > 1 ? argv[1] : "0", 0, 10) + wcstol(w, 0, 10));
}
SRC

loongarch64-linux-gnu-gcc-14 -nostdinc \
  -isystem "$GCC14_INCLUDE" \
  -isystem "$SYSROOT/usr/include" \
  -nostartfiles --sysroot="$SYSROOT" \
  "$SYSROOT/usr/lib64/crt1.o" "$SYSROOT/usr/lib64/crti.o" "$GCCLIB/crtbegin.o" \
  /tmp/uh.c \
  -L"$SYSROOT/usr/lib64" -L"$GCCLIB" \
  -Wl,-dynamic-linker,/lib64/ld.so.1 \
  -lc -lgcc -lgcc_s \
  "$GCCLIB/crtend.o" "$SYSROOT/usr/lib64/crtn.o" \
  -o /tmp/uh_vendor_headers

file /tmp/uh_vendor_headers
readelf --version-info /tmp/uh_vendor_headers | grep -E 'GLIBC_[0-9]+' | sort -u
readelf -l /tmp/uh_vendor_headers | grep -i interpreter
