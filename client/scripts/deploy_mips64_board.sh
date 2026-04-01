#!/usr/bin/env bash
set -e
if (set -o pipefail) >/dev/null 2>&1; then
  set -o pipefail
fi

# Deploy loonginx client to a yum-based MIPS64 board.
# Usage:
#   bash client/scripts/deploy_mips64_board.sh /opt/loonginx-client
# Optional env:
#   PYTHON_BIN=/opt/python3.9/bin/python3.9
#   PYTHON39_VERSION=3.9.19
#   PYTHON_PREFIX=/opt/python3.9
#   PYTHON_SOURCE_TARBALL=/root/client/scripts/Python-3.9.19.tgz
#   MAKE_JOBS=2
#   ENABLE_SERVICE=1
#   SERVICE_NAME=loonginx-client
#   WHEELHOUSE_DIR=/path/to/wheels
#   SKIP_YUM=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${1:-}"
PYTHON39_VERSION="${PYTHON39_VERSION:-3.9.19}"
PYTHON_PREFIX="${PYTHON_PREFIX:-/opt/python3.9}"
PYTHON_BIN="${PYTHON_BIN:-}"
PYTHON_SOURCE_TARBALL="${PYTHON_SOURCE_TARBALL:-}"
ENABLE_SERVICE="${ENABLE_SERVICE:-0}"
SERVICE_NAME="${SERVICE_NAME:-loonginx-client}"
WHEELHOUSE_DIR="${WHEELHOUSE_DIR:-}"
PYTHON_BUILD_ROOT="${PYTHON_BUILD_ROOT:-/usr/local/src}"
MAKE_JOBS="${MAKE_JOBS:-}"
SKIP_YUM="${SKIP_YUM:-0}"

log() {
  printf '[deploy] %s\n' "$*"
}

warn() {
  printf '[deploy][warn] %s\n' "$*" >&2
}

die() {
  printf '[deploy][error] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  bash client/scripts/deploy_mips64_board.sh /opt/loonginx-client

Environment overrides:
  PYTHON_BIN       Existing Python 3.9+ binary to use.
  PYTHON39_VERSION Python version to build from source when yum cannot provide one.
  PYTHON_PREFIX    Install prefix for source-built Python.
  PYTHON_SOURCE_TARBALL
                   Local Python source tarball path for offline builds.
  MAKE_JOBS        Parallelism for Python build.
  ENABLE_SERVICE   Set to 1 to install and enable a systemd service.
  SERVICE_NAME     systemd service name. Default: loonginx-client
  WHEELHOUSE_DIR   Local wheel directory for offline dependency install.
  SKIP_YUM         Set to 1 to skip all yum attempts.
EOF
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

resolve_binary() {
  local candidate="$1"
  if [[ -z "$candidate" ]]; then
    return 1
  fi

  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  if command_exists "$candidate"; then
    command -v "$candidate"
    return 0
  fi

  return 1
}

python_is_supported() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
}

pick_make_jobs() {
  if [[ -n "$MAKE_JOBS" ]]; then
    printf '%s\n' "$MAKE_JOBS"
    return 0
  fi

  if command_exists getconf; then
    getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2\n'
    return 0
  fi

  printf '2\n'
}

ensure_yum_packages() {
  local packages=("$@")
  if [[ "$SKIP_YUM" == "1" ]]; then
    die "yum is disabled by SKIP_YUM=1; install these packages manually: ${packages[*]}"
  fi

  if ! command_exists yum; then
    die "yum is required to install Python 3.9 automatically"
  fi

  yum -y install "${packages[@]}"
}

try_install_python39_from_yum() {
  local pkg
  local candidates=(python39 python39u python3.9)

  if [[ "$SKIP_YUM" == "1" ]]; then
    return 1
  fi

  for pkg in "${candidates[@]}"; do
    log "trying yum install ${pkg}"
    if yum -y install "$pkg"; then
      local resolved=""
      resolved="$(resolve_binary python3.9 || true)"
      if [[ -n "$resolved" ]] && python_is_supported "$resolved"; then
        PYTHON_BIN="$resolved"
        return 0
      fi
    fi
  done

  return 1
}

download_file() {
  local url="$1"
  local output="$2"

  if [[ -n "$PYTHON_SOURCE_TARBALL" && -f "$PYTHON_SOURCE_TARBALL" ]]; then
    log "using local source tarball ${PYTHON_SOURCE_TARBALL}"
    cp -f "$PYTHON_SOURCE_TARBALL" "$output"
    return 0
  fi

  if command_exists wget; then
    if wget -O "$output" "$url"; then
      return 0
    fi
    warn "wget failed to download ${url}"
  fi

  if command_exists curl; then
    if curl -L "$url" -o "$output"; then
      return 0
    fi
    warn "curl failed to download ${url}"
  fi

  if [[ "$SKIP_YUM" != "1" ]]; then
    ensure_yum_packages wget
    if wget -O "$output" "$url"; then
      return 0
    fi
    warn "wget still failed after yum install"
  fi

  die "cannot fetch ${url}. Put ${output##*/} on the board and rerun with PYTHON_SOURCE_TARBALL=/path/${output##*/}"
}

check_build_commands() {
  local missing=()
  local cmd

  for cmd in gcc make tar; do
    if ! command_exists "$cmd"; then
      missing+=("$cmd")
    fi
  done

  if [[ "${#missing[@]}" -gt 0 ]]; then
    die "missing build tools: ${missing[*]}. Install them manually or restore yum access."
  fi
}

find_header_dir() {
  local header="$1"
  local dir

  for dir in /usr/include /usr/local/include /opt/include; do
    if [[ -f "${dir}/${header}" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
  done

  return 1
}

check_python_build_headers() {
  local ffi_dir=""
  local zlib_dir=""

  ffi_dir="$(find_header_dir ffi.h || true)"
  if [[ -z "$ffi_dir" ]]; then
    die "missing ffi.h (libffi-devel). Install libffi-devel manually or copy ffi.h and libffi to the board before building Python 3.9."
  fi

  zlib_dir="$(find_header_dir zlib.h || true)"
  if [[ -z "$zlib_dir" ]]; then
    die "missing zlib.h (zlib-devel). Install zlib-devel manually or copy zlib.h and libz to the board before building Python 3.9."
  fi
}

install_python39_from_source() {
  local tarball="Python-${PYTHON39_VERSION}.tgz"
  local src_dir="Python-${PYTHON39_VERSION}"
  local url="https://www.python.org/ftp/python/${PYTHON39_VERSION}/${tarball}"
  local jobs
  jobs="$(pick_make_jobs)"

  if [[ "$SKIP_YUM" != "1" ]]; then
    log "installing build dependencies for Python ${PYTHON39_VERSION}"
    yum -y groupinstall "Development Tools" || warn "yum groupinstall Development Tools failed, continuing with explicit packages"
    ensure_yum_packages gcc make tar openssl-devel bzip2-devel libffi-devel zlib-devel xz-devel readline-devel sqlite-devel findutils || true
  else
    warn "SKIP_YUM=1 set; assuming build dependencies are already installed"
  fi

  mkdir -p "$PYTHON_BUILD_ROOT"
  cd "$PYTHON_BUILD_ROOT"
  check_build_commands
  check_python_build_headers

  if [[ ! -f "$tarball" ]]; then
    log "preparing Python source tarball"
    download_file "$url" "$tarball"
  fi

  if [[ -f "$tarball" ]]; then
    log "using cached source tarball ${PYTHON_BUILD_ROOT}/${tarball}"
  fi

  rm -rf "$src_dir"
  tar xf "$tarball"
  cd "$src_dir"

  log "building Python ${PYTHON39_VERSION} into ${PYTHON_PREFIX}"
  ./configure --prefix="$PYTHON_PREFIX" --with-ensurepip=install
  make -j"$jobs"
  make altinstall

  if [[ ! -x "${PYTHON_PREFIX}/bin/python3.9" ]]; then
    die "Python 3.9 build finished but ${PYTHON_PREFIX}/bin/python3.9 was not created"
  fi

  PYTHON_BIN="${PYTHON_PREFIX}/bin/python3.9"
}

ensure_python39() {
  local resolved=""
  local candidate=""

  for candidate in "$PYTHON_BIN" python3.9 python3 /opt/python3.9/bin/python3.9 /usr/local/bin/python3.9 /usr/bin/python3.9; do
    resolved="$(resolve_binary "$candidate" || true)"
    if [[ -n "$resolved" ]] && python_is_supported "$resolved"; then
      PYTHON_BIN="$resolved"
      log "using python: $("$PYTHON_BIN" -V 2>&1)"
      return 0
    fi
  done

  warn "Python 3.9+ not found, trying yum"
  if try_install_python39_from_yum; then
    log "using python: $("$PYTHON_BIN" -V 2>&1)"
    return 0
  fi

  warn "yum could not provide Python 3.9, building from source"
  install_python39_from_source

  if ! python_is_supported "$PYTHON_BIN"; then
    die "installed Python is still lower than 3.9: $("$PYTHON_BIN" -V 2>&1)"
  fi

  log "using python: $("$PYTHON_BIN" -V 2>&1)"
}

sync_client_tree() {
  mkdir -p "$TARGET_DIR"
  mkdir -p "$TARGET_DIR/logs"

  if command_exists rsync; then
    log "syncing client files with rsync"
    rsync -a \
      --delete \
      --exclude '.git' \
      --exclude '.venv' \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      "${CLIENT_DIR}/" "${TARGET_DIR}/"
    return 0
  fi

  log "syncing client files with tar"
  tar \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    -C "$CLIENT_DIR" \
    -cf - . | tar -C "$TARGET_DIR" -xf -
}

install_requirements() {
  local venv_dir="${TARGET_DIR}/.venv"
  local venv_python="${venv_dir}/bin/python"
  local venv_pip="${venv_dir}/bin/pip"

  log "creating virtual environment"
  rm -rf "$venv_dir"
  "$PYTHON_BIN" -m venv "$venv_dir"

  log "upgrading pip tooling"
  "$venv_python" -m pip install --upgrade pip setuptools wheel

  if [[ -f "${TARGET_DIR}/requirements.txt" ]]; then
    if [[ -n "$WHEELHOUSE_DIR" ]]; then
      log "installing requirements from wheelhouse ${WHEELHOUSE_DIR}"
      "$venv_pip" install --no-index --find-links "$WHEELHOUSE_DIR" -r "${TARGET_DIR}/requirements.txt"
    else
      log "installing requirements from requirements.txt"
      "$venv_pip" install -r "${TARGET_DIR}/requirements.txt"
    fi
  else
    log "requirements.txt not found, skipping dependency installation"
  fi

  log "running syntax precheck"
  "$venv_python" -m compileall -q "$TARGET_DIR"
}

ensure_env_template() {
  if [[ -f "${TARGET_DIR}/.env" ]]; then
    return 0
  fi

  cat > "${TARGET_DIR}/.env" <<'EOF'
# Fill in the runtime values required by your board before starting the client.
# This placeholder file is generated by deploy_mips64_board.sh.
EOF
  warn "created placeholder ${TARGET_DIR}/.env, fill it before starting the client"
}

install_service() {
  local service_path="/etc/systemd/system/${SERVICE_NAME}.service"
  local venv_python="${TARGET_DIR}/.venv/bin/python"

  if ! command_exists systemctl; then
    warn "systemctl not found, skipping service installation"
    return 0
  fi

  log "installing systemd service ${SERVICE_NAME}"
  cat > "$service_path" <<EOF
[Unit]
Description=Loonginx Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${TARGET_DIR}
EnvironmentFile=-${TARGET_DIR}/.env
ExecStart=${venv_python} ${TARGET_DIR}/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  warn "service installed but not started automatically; review ${service_path} and run: systemctl start ${SERVICE_NAME}"
}

main() {
  if [[ "$#" -ne 1 ]]; then
    usage
    exit 1
  fi

  TARGET_DIR="$1"
  ensure_python39
  sync_client_tree
  install_requirements
  ensure_env_template

  if [[ "$ENABLE_SERVICE" == "1" ]]; then
    install_service
  fi

  log "deployment finished"
  log "target directory: ${TARGET_DIR}"
  log "python binary: ${PYTHON_BIN}"
  log "start manually with: ${TARGET_DIR}/.venv/bin/python ${TARGET_DIR}/main.py"
}

main "$@"
