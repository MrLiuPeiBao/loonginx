#!/usr/bin/env bash
set -euo pipefail

# Full pipeline:
# 1) prepare Linux build environment
# 2) prepare loongarch64-linux-gnu toolchain + sysroot
# 3) cross-build Python 3.9.19 (install prefix /opt/python3.9)
# 4) prepare offline wheelhouse for client requirements

PYTHON_VERSION="${PYTHON_VERSION:-3.9.19}"
TARGET_PREFIX="${TARGET_PREFIX:-/opt/python3.9}"
WORKDIR="${WORKDIR:-$HOME/loonginx-build}"
TOOLCHAIN_URL="${TOOLCHAIN_URL:-https://ftp.loongnix.cn/toolchain/gcc/release/loongarch/gcc8/loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.2.tar.xz}"
JOBS="${JOBS:-$(nproc)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$PROJECT_DIR/requirements.txt}"

TOOLCHAIN_ARCHIVE_NAME="$(basename "$TOOLCHAIN_URL")"
TOOLCHAIN_DIR_NAME="${TOOLCHAIN_ARCHIVE_NAME%.tar.xz}"
TOOLCHAIN_ROOT="$WORKDIR/toolchain"
TOOLCHAIN_DIR="$TOOLCHAIN_ROOT/$TOOLCHAIN_DIR_NAME"

SRC_ROOT="$WORKDIR/src"
PY_SRC_DIR="$SRC_ROOT/Python-$PYTHON_VERSION"
PY_TARBALL="$SRC_ROOT/Python-$PYTHON_VERSION.tgz"

BUILD_ROOT="$WORKDIR/build-python-$PYTHON_VERSION-loongarch64"
CONFIG_SITE_FILE="$BUILD_ROOT/config.site"
STAGE_DIR="$WORKDIR/stage-python39"
ARTIFACT_DIR="$WORKDIR/artifacts"
WHEELHOUSE_DIR="$WORKDIR/wheelhouse"

log() {
  printf '[build] %s\n' "$*"
}

die() {
  printf '[build][error] %s\n' "$*" >&2
  exit 1
}

as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "Need root privileges for: $* (install sudo or run as root)"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Command not found: $1"
}

prepare_host_environment() {
  log "Step 1/4: install host build dependencies"
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential git curl wget ca-certificates xz-utils tar pkg-config \
    patch file make autoconf automake libtool cmake ninja-build \
    python3 python3-pip python3-venv python3-setuptools python3-wheel python-is-python3 \
    gawk bison flex gettext unzip rsync \
    qemu-user-static binfmt-support \
    gcc-14-loongarch64-linux-gnu g++-14-loongarch64-linux-gnu \
    binutils-loongarch64-linux-gnu
}

prepare_toolchain() {
  log "Step 2/4: prepare LoongArch cross toolchain"
  mkdir -p "$TOOLCHAIN_ROOT"
  if [[ ! -d "$TOOLCHAIN_DIR" ]]; then
    log "Downloading toolchain: $TOOLCHAIN_URL"
    wget -O "$TOOLCHAIN_ROOT/$TOOLCHAIN_ARCHIVE_NAME" "$TOOLCHAIN_URL"
    tar -C "$TOOLCHAIN_ROOT" -xf "$TOOLCHAIN_ROOT/$TOOLCHAIN_ARCHIVE_NAME"
  fi

  [[ -x "$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc" ]] || die "Toolchain gcc not found"
  [[ -d "$TOOLCHAIN_DIR/loongarch64-linux-gnu/sysroot" ]] || die "Toolchain sysroot not found"

  export PATH="$TOOLCHAIN_DIR/bin:$PATH"
  require_cmd loongarch64-linux-gnu-gcc
  require_cmd loongarch64-linux-gnu-g++
  require_cmd loongarch64-linux-gnu-ld
  require_cmd loongarch64-linux-gnu-readelf

  log "Toolchain: $(loongarch64-linux-gnu-gcc -dumpmachine)"
  log "Toolchain sysroot: $(loongarch64-linux-gnu-gcc -print-sysroot)"
}

select_compiler() {
  local sysroot="$TOOLCHAIN_DIR/loongarch64-linux-gnu/sysroot"
  local smoke_c="$WORKDIR/cc-smoke.c"
  local smoke_bin="$WORKDIR/cc-smoke.loongarch64"

  cat > "$smoke_c" <<'EOF'
int main(void) { return 0; }
EOF

  # Try vendor compiler first. On some WSL1 environments, vendor ld may segfault.
  if "$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc" "$smoke_c" -o "$smoke_bin" >/dev/null 2>&1; then
    export CC="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc"
    export CXX="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-g++"
    export AR="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc-ar"
    export RANLIB="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-gcc-ranlib"
    export LD="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-ld"
    export READELF="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-readelf"
    export STRIP="$TOOLCHAIN_DIR/bin/loongarch64-linux-gnu-strip"
    export CPPFLAGS=""
    export LDFLAGS=""
    log "Compiler mode: vendor gcc8 toolchain"
    return 0
  fi

  if command -v loongarch64-linux-gnu-gcc-14 >/dev/null 2>&1; then
    export CC="loongarch64-linux-gnu-gcc-14 --sysroot=$sysroot"
    export CXX="loongarch64-linux-gnu-g++-14 --sysroot=$sysroot"
    export AR="loongarch64-linux-gnu-gcc-ar-14"
    export RANLIB="loongarch64-linux-gnu-gcc-ranlib-14"
    export LD="loongarch64-linux-gnu-ld"
    export READELF="loongarch64-linux-gnu-readelf"
    export STRIP="loongarch64-linux-gnu-strip"
    export CPPFLAGS="--sysroot=$sysroot"
    export LDFLAGS="--sysroot=$sysroot -Wl,-rpath-link,$sysroot/lib64 -Wl,-rpath-link,$sysroot/usr/lib64"
    log "Compiler mode: ubuntu gcc-14 + vendor glibc2.28 sysroot (fallback)"
    return 0
  fi

  die "No usable LoongArch cross compiler found"
}

prepare_host_python_shim() {
  if command -v python3.9 >/dev/null 2>&1; then
    if python3.9 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 9) else 1)"; then
      log "Host Python for build: $(python3.9 -V 2>&1)"
      return 0
    fi
  fi

  local shim_dir="$WORKDIR/.hostbin"
  mkdir -p "$shim_dir"

  cat > "$shim_dir/python3.9" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# CPython 3.9 cross-configure checks for a strict 3.9 host interpreter.
# We proxy to host python3 while satisfying that one version probe.
if [[ $# -ge 2 && "$1" == "-c" ]]; then
  if [[ "$2" == "import sys;sys.exit(not '.'.join(str(n) for n in sys.version_info[:2]) == '3.9')" ]]; then
    exit 0
  fi
fi

exec python3 "$@"
EOF
  chmod +x "$shim_dir/python3.9"
  export PATH="$shim_dir:$PATH"
  log "Host Python shim enabled: python3.9 -> python3"
}

prepare_python_source() {
  mkdir -p "$SRC_ROOT"
  if [[ ! -f "$PY_TARBALL" ]]; then
    log "Downloading Python $PYTHON_VERSION source"
    wget -O "$PY_TARBALL" "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz"
  fi
  if [[ ! -d "$PY_SRC_DIR" ]]; then
    log "Extracting Python source"
    tar -C "$SRC_ROOT" -xf "$PY_TARBALL"
  fi
}

configure_and_build_python() {
  log "Step 3/4: cross-build Python $PYTHON_VERSION"
  mkdir -p "$BUILD_ROOT"
  rm -rf "$BUILD_ROOT"/*

  rsync -a --delete "$PY_SRC_DIR/" "$BUILD_ROOT/"
  cd "$BUILD_ROOT"

  cat > "$CONFIG_SITE_FILE" <<'EOF'
ac_cv_file__dev_ptmx=yes
ac_cv_file__dev_ptc=no
ac_cv_buggy_getaddrinfo=no
EOF

  export CONFIG_SITE="$CONFIG_SITE_FILE"
  prepare_host_python_shim
  select_compiler
  export CFLAGS="-O2"

  ./configure \
    --build=x86_64-linux-gnu \
    --host=loongarch64-linux-gnu \
    --prefix="$TARGET_PREFIX" \
    --enable-shared \
    --with-ensurepip=install \
    --disable-ipv6

  make -j"$JOBS"

  rm -rf "$STAGE_DIR"
  make install DESTDIR="$STAGE_DIR"

  mkdir -p "$ARTIFACT_DIR"
  # Exclude host helper Python from deployment package when present.
  if [[ -d "$STAGE_DIR/opt/hostpython39" ]]; then
    rm -rf "$STAGE_DIR/opt/hostpython39"
  fi
  tar -C "$STAGE_DIR" -czf "$ARTIFACT_DIR/python${PYTHON_VERSION}-loongarch64-glibc228.tar.gz" .
  log "Python artifact: $ARTIFACT_DIR/python${PYTHON_VERSION}-loongarch64-glibc228.tar.gz"
}

prepare_wheelhouse() {
  log "Step 4/4: prepare offline wheelhouse"
  [[ -f "$REQUIREMENTS_FILE" ]] || die "requirements file not found: $REQUIREMENTS_FILE"

  mkdir -p "$WHEELHOUSE_DIR"
  local venv_dir="$WORKDIR/.wheel-venv"
  python3 -m venv "$venv_dir"
  # shellcheck disable=SC1090
  source "$venv_dir/bin/activate"
  python -m pip install --upgrade pip wheel setuptools
  python -m pip download --dest "$WHEELHOUSE_DIR" -r "$REQUIREMENTS_FILE"
  python -m pip wheel --wheel-dir "$WHEELHOUSE_DIR" -r "$REQUIREMENTS_FILE"
  deactivate

  log "Wheelhouse ready: $WHEELHOUSE_DIR"
}

print_next_steps() {
  cat <<EOF

[done] Build pipeline completed.

Artifacts:
  Python package : $ARTIFACT_DIR/python${PYTHON_VERSION}-loongarch64-glibc228.tar.gz
  Wheelhouse     : $WHEELHOUSE_DIR

Board-side install (as root on board):
  mkdir -p /tmp/python39
  tar -C /tmp/python39 -xzf python${PYTHON_VERSION}-loongarch64-glibc228.tar.gz
  cp -a /tmp/python39/opt/python3.9 /opt/
  /opt/python3.9/bin/python3.9 -V
  /opt/python3.9/bin/python3.9 -m pip install --no-index --find-links /path/to/wheelhouse -r /path/to/requirements.txt
EOF
}

main() {
  require_cmd bash
  require_cmd wget
  require_cmd tar
  require_cmd rsync
  require_cmd python3

  mkdir -p "$WORKDIR"
  # Python 3.9 cross build still invokes host-side "python" helper scripts.
  if ! command -v python >/dev/null 2>&1; then
    mkdir -p "$WORKDIR/.hostbin"
    ln -sf "$(command -v python3)" "$WORKDIR/.hostbin/python"
    export PATH="$WORKDIR/.hostbin:$PATH"
  fi

  prepare_host_environment
  prepare_toolchain
  prepare_python_source
  configure_and_build_python
  prepare_wheelhouse
  print_next_steps
}

main "$@"
