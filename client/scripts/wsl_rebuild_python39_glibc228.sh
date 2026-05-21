#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.9.19}"
TARGET_PREFIX="${TARGET_PREFIX:-/opt/python3.9}"
WORKDIR="${WORKDIR:-/root/loonginx-build}"
JOBS="${JOBS:-$(nproc)}"

TOOLCHAIN_DIR="$WORKDIR/toolchain/loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.2"
SYSROOT="$TOOLCHAIN_DIR/loongarch64-linux-gnu/sysroot"
GCCLIB="$TOOLCHAIN_DIR/lib/gcc/loongarch64-linux-gnu/8.3.0"
SRC_DIR="$WORKDIR/src/Python-$PYTHON_VERSION"
BUILD_DIR="$WORKDIR/build-python-$PYTHON_VERSION-loongarch64-glibc228"
STAGE_DIR="$WORKDIR/stage-python39-glibc228"
ARTIFACT_DIR="$WORKDIR/artifacts"
WRAP_DIR="$WORKDIR/wrappers"
ARTIFACT_NAME="python${PYTHON_VERSION}-loongarch64-glibc228-objv0.tar.gz"

log() {
  printf '[rebuild] %s\n' "$*"
}

die() {
  printf '[rebuild][error] %s\n' "$*" >&2
  exit 1
}

[[ -x "$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc" ]] || die "missing vendor gcc: $TOOLCHAIN_DIR"
[[ -x "$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-ld" ]] || die "missing vendor ld: $TOOLCHAIN_DIR"
[[ -d "$SYSROOT/usr/lib64" ]] || die "missing sysroot: $SYSROOT"
[[ -d "$SRC_DIR" ]] || die "missing Python source: $SRC_DIR"
command -v python3.9 >/dev/null 2>&1 || die "host python3.9 is required"

mkdir -p "$WRAP_DIR" "$ARTIFACT_DIR"

cat > "$WRAP_DIR/loongarch64-vendor-ldwrap" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

TOOL=${LOONGARCH_VENDOR_TOOLCHAIN:?LOONGARCH_VENDOR_TOOLCHAIN is required}
SYSROOT="$TOOL/loongarch64-linux-gnu/sysroot"
GCCLIB="$TOOL/lib/gcc/loongarch64-linux-gnu/8.3.0"
LD="$TOOL/bin/loongarch64-linux-gnu-ld"

mode=exe
out=
objs=()
libs=()
ldopts=()
skip_start=0

add_wl_opts() {
  local rest="$1"
  local part
  IFS=',' read -r -a parts <<< "$rest"
  for part in "${parts[@]}"; do
    case "$part" in
      -export-dynamic) ldopts+=(--export-dynamic) ;;
      --no-as-needed|--as-needed|--export-dynamic|--eh-frame-hdr|--whole-archive|--no-whole-archive) ldopts+=("$part") ;;
      --version-script=*|--dynamic-list=*|-h*|-soname*|-rpath*|-rpath-link*|-z*) ldopts+=("$part") ;;
      *) ldopts+=("$part") ;;
    esac
  done
}

args=("$@")
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  case "$a" in
    -shared)
      mode=shared
      ;;
    -o)
      i=$((i + 1))
      out="${args[$i]}"
      ;;
    -Wl,*)
      add_wl_opts "${a#-Wl,}"
      ;;
    -Xlinker)
      i=$((i + 1))
      x="${args[$i]}"
      case "$x" in
        -export-dynamic) ldopts+=(--export-dynamic) ;;
        *) ldopts+=("$x") ;;
      esac
      ;;
    -L*|-l*)
      libs+=("$a")
      ;;
    -pthread)
      libs+=("-lpthread")
      ;;
    -rdynamic)
      ldopts+=(--export-dynamic)
      ;;
    -nostartfiles|-nostdlib)
      skip_start=1
      ;;
    --sysroot=*|-fPIC|-fPIE|-fno-*|-fuse-*|-m*|-O*|-g|-pipe|-std=*|-D*|-U*|-I*|-Wall|-Wextra|-Werror*|-Wstrict-*|-Wno-*|-Wsign-*|-DNDEBUG)
      ;;
    *.o|*.a|*.so)
      objs+=("$a")
      ;;
    *)
      if [ -f "$a" ]; then
        objs+=("$a")
      else
        ldopts+=("$a")
      fi
      ;;
  esac
  i=$((i + 1))
done

[ -n "$out" ] || out=a.out

common=(
  -m elf64loongarch
  --sysroot="$SYSROOT"
  --eh-frame-hdr
  -L"$GCCLIB"
  -L"$SYSROOT/usr/lib64"
  -L"$SYSROOT/lib64"
  -L"$SYSROOT/usr/lib"
  -L"$SYSROOT/lib"
  -rpath-link "$SYSROOT/usr/lib64"
  -rpath-link "$SYSROOT/lib64"
)

if [ "$mode" = shared ]; then
  start=()
  finish=()
  if [ "$skip_start" -eq 0 ]; then
    start=("$SYSROOT/usr/lib64/crti.o" "$GCCLIB/crtbeginS.o")
    finish=("$GCCLIB/crtendS.o" "$SYSROOT/usr/lib64/crtn.o")
  fi
  exec "$LD" "${common[@]}" -shared -o "$out" "${start[@]}" "${ldopts[@]}" \
    "${objs[@]}" "${libs[@]}" -lgcc --as-needed -lgcc_s --no-as-needed -lc \
    -lgcc --as-needed -lgcc_s --no-as-needed "${finish[@]}"
else
  start=()
  finish=()
  if [ "$skip_start" -eq 0 ]; then
    start=("$SYSROOT/usr/lib64/crt1.o" "$SYSROOT/usr/lib64/crti.o" "$GCCLIB/crtbegin.o")
    finish=("$GCCLIB/crtend.o" "$SYSROOT/usr/lib64/crtn.o")
  fi
  exec "$LD" "${common[@]}" -dynamic-linker /lib64/ld.so.1 -o "$out" "${start[@]}" \
    "${ldopts[@]}" "${objs[@]}" "${libs[@]}" -lgcc --as-needed -lgcc_s \
    --no-as-needed -lc -lgcc --as-needed -lgcc_s --no-as-needed "${finish[@]}"
fi
EOF

cat > "$WRAP_DIR/loongarch64-vendor-ccwrap" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

TOOL=${LOONGARCH_VENDOR_TOOLCHAIN:?LOONGARCH_VENDOR_TOOLCHAIN is required}
GCC="$TOOL/bin/loongarch64-linux-gnu-gcc"
LDWRAP=${LOONGARCH_VENDOR_LDWRAP:-loongarch64-vendor-ldwrap}

compile_only=0
preprocess_only=0
assemble_only=0
out=
sources=()
objs=()
link_args=()
compile_args=()

args=("$@")
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  case "$a" in
    -c)
      compile_only=1
      compile_args+=("$a")
      ;;
    -E)
      preprocess_only=1
      compile_args+=("$a")
      ;;
    -S)
      assemble_only=1
      compile_args+=("$a")
      ;;
    -o)
      i=$((i + 1))
      out="${args[$i]}"
      compile_args+=("-o" "$out")
      ;;
    *.c|*.cc|*.cpp|*.cxx|*.S|*.s)
      sources+=("$a")
      compile_args+=("$a")
      ;;
    *.o|*.a|*.so)
      objs+=("$a")
      ;;
    -shared|-pthread|-rdynamic|-Wl,*|-Xlinker|-L*|-l*|-nostartfiles|-nostdlib)
      link_args+=("$a")
      if [ "$a" = "-Xlinker" ]; then
        i=$((i + 1))
        link_args+=("${args[$i]}")
      fi
      ;;
    --sysroot=*)
      compile_args+=("$a")
      ;;
    *)
      compile_args+=("$a")
      ;;
  esac
  i=$((i + 1))
done

if [ "$compile_only" -eq 1 ] || [ "$preprocess_only" -eq 1 ] || [ "$assemble_only" -eq 1 ]; then
  exec "$GCC" "$@"
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

filtered_compile_args=()
skip=0
for a in "${compile_args[@]}"; do
  if [ "$skip" -eq 1 ]; then
    skip=0
    continue
  fi
  case "$a" in
    -o)
      skip=1
      ;;
    *.c|*.cc|*.cpp|*.cxx|*.S|*.s)
      ;;
    *)
      filtered_compile_args+=("$a")
      ;;
  esac
done

idx=0
for src in "${sources[@]}"; do
  obj="$tmpdir/src_${idx}.o"
  "$GCC" "${filtered_compile_args[@]}" -c "$src" -o "$obj"
  objs+=("$obj")
  idx=$((idx + 1))
done

if [ -z "$out" ]; then
  out=a.out
fi

exec "$LDWRAP" "${link_args[@]}" -o "$out" "${objs[@]}"
EOF

chmod +x "$WRAP_DIR/loongarch64-vendor-ldwrap" "$WRAP_DIR/loongarch64-vendor-ccwrap"

export LOONGARCH_VENDOR_TOOLCHAIN="$TOOLCHAIN_DIR"
export LOONGARCH_VENDOR_LDWRAP="$WRAP_DIR/loongarch64-vendor-ldwrap"
export PATH="$WRAP_DIR:$TOOLCHAIN_DIR/bin:$PATH"

log "prepare build directory"
rm -rf "$BUILD_DIR" "$STAGE_DIR"
mkdir -p "$BUILD_DIR"
rsync -a --delete "$SRC_DIR/" "$BUILD_DIR/"

cd "$BUILD_DIR"

cat > config.site <<'EOF'
ac_cv_file__dev_ptmx=yes
ac_cv_file__dev_ptc=no
ac_cv_buggy_getaddrinfo=no
EOF

cat > Modules/Setup.local <<'EOF'
*disabled*
_ssl
_hashlib
_ctypes
_ctypes_test
EOF

sed -i "s/^TEST_EXTENSIONS = True/TEST_EXTENSIONS = False/" setup.py
sed -i "s/^DISABLED_MODULE_LIST = .*/DISABLED_MODULE_LIST = ['_ssl', '_hashlib', '_ctypes', '_ctypes_test']/" setup.py

export CONFIG_SITE="$BUILD_DIR/config.site"
export CC="loongarch64-vendor-ccwrap"
export CXX="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-g++"
export AR="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-ar"
export RANLIB="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-ranlib"
export READELF="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-readelf"
export STRIP="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-strip"
export LDSHARED="$CC -shared"
export BLDSHARED="$CC -shared"
export CFLAGS="-O2"
export CPPFLAGS=""
export LDFLAGS="-Wl,-rpath,$TARGET_PREFIX/lib"
export LIBS=""

log "configure Python $PYTHON_VERSION for loongarch64 glibc2.28 OBJ-v0"
./configure \
  --build=x86_64-linux-gnu \
  --host=loongarch64-linux-gnu \
  --prefix="$TARGET_PREFIX" \
  --enable-shared \
  --without-ensurepip \
  --disable-ipv6

log "build"
make -j"$JOBS" \
  LINKCC="$CC" \
  LINKFORSHARED="-Xlinker -export-dynamic"

log "install into staging"
make install DESTDIR="$STAGE_DIR"

log "strip target binaries"
find "$STAGE_DIR$TARGET_PREFIX" -type f \( -name 'python3.9' -o -name '*.so' \) -exec "$STRIP" --strip-unneeded {} + || true

log "verify target ABI"
"$READELF" -h "$STAGE_DIR$TARGET_PREFIX/bin/python3.9" | grep Flags
"$READELF" -l "$STAGE_DIR$TARGET_PREFIX/bin/python3.9" | grep 'interpreter'
"$READELF" -d "$STAGE_DIR$TARGET_PREFIX/bin/python3.9" | grep -E 'NEEDED|RUNPATH|RPATH' || true
"$READELF" --version-info "$STAGE_DIR$TARGET_PREFIX/bin/python3.9" | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -Vu || true
"$READELF" --version-info "$STAGE_DIR$TARGET_PREFIX/lib/libpython3.9.so.1.0" | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -Vu || true

log "package without toybox-incompatible ./ entry"
tar -C "$STAGE_DIR" -czf "$ARTIFACT_DIR/$ARTIFACT_NAME" opt
cp -f "$ARTIFACT_DIR/$ARTIFACT_NAME" /mnt/c/Users/lpb/Desktop/loonginx/client/scripts/
log "artifact: $ARTIFACT_DIR/$ARTIFACT_NAME"
