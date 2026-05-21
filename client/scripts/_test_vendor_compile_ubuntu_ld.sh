#!/usr/bin/env bash
set -euo pipefail

TC=/root/loonginx-build/toolchain/loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.2

cat > /tmp/v.c <<'SRC'
#include <stdlib.h>
int main(int argc, char **argv) {
    return (int)strtol(argc > 1 ? argv[1] : "0", 0, 10);
}
SRC

"$TC/bin/loongarch64-linux-gnu-gcc" -B/usr/bin/ /tmp/v.c -o /tmp/v_b
file /tmp/v_b
readelf --version-info /tmp/v_b | grep -E 'GLIBC_[0-9]+' | sort -u
readelf -l /tmp/v_b | grep -i interpreter
