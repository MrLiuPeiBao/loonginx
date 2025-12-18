#!/usr/bin/env python3
"""One-click launcher for FastAPI and legacy GUI server."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / 'venv_widows'
IS_WINDOWS = os.name == 'nt'
SCRIPTS_DIR = VENV_DIR / ('Scripts' if IS_WINDOWS else 'bin')
PYTHON_BIN = SCRIPTS_DIR / ('python.exe' if IS_WINDOWS else 'python')

# load .env before reading env values
load_dotenv()

def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {'1', 'true', 'yes', 'on'}

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default

SKIP_PIP_INSTALL = _env_bool('SKIP_PIP_INSTALL', False)
INSTALL_DEPENDENCIES = _env_bool('INSTALL_DEPENDENCIES', True)
GUI_ENABLED = _env_bool('GUI_ENABLED', True)
PIP_INSTALL_TIMEOUT = _env_int('RUN_ALL_PIP_TIMEOUT', 60)
PIP_INSTALL_RETRIES = os.getenv('RUN_ALL_PIP_RETRIES', '0')
PIP_INSTALL_DEFAULT_TIMEOUT = os.getenv('RUN_ALL_PIP_DEFAULT_TIMEOUT', '5')

def _read_pyvenv_cfg(path: Path) -> Dict[str, str]:
    """Read pyvenv.cfg as a simple key/value mapping."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return values
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip().lower()] = value.strip()
    return values


def _parse_major_minor(version_text: str) -> Optional[Tuple[int, int]]:
    """Parse a version string like '3.11.9' into (3, 11)."""
    if not version_text:
        return None
    parts = version_text.strip().split('.')
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _venv_python_works(timeout: float = 6.0) -> bool:
    """Verify venv python is runnable (venv copy is not portable across machines)."""
    if not PYTHON_BIN.exists():
        return False
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), '-c', 'import sys; print(sys.version)'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        print(f'[WARN] venv python check failed: {exc}')
        return False

    if result.returncode != 0:
        stderr = (result.stderr or '').strip().replace('\n', ' ')
        print(f'[WARN] venv python is not runnable (rc={result.returncode}): {stderr[:240]}')
        return False
    return True


def _repair_venv() -> bool:
    """Repair an existing venv in-place to reuse installed packages when possible."""
    cfg = _read_pyvenv_cfg(VENV_DIR / 'pyvenv.cfg')
    venv_version = cfg.get('version', '')
    venv_major_minor = _parse_major_minor(venv_version)
    current_major_minor = (sys.version_info.major, sys.version_info.minor)
    if venv_major_minor and venv_major_minor != current_major_minor:
        print(
            '[WARN] venv_widows was created with Python '
            f'{venv_version}, but current Python is {sys.version_info.major}.{sys.version_info.minor}. '
            'To reuse the existing venv packages, install the matching Python version.',
        )
        return False

    try:
        print('[INFO] Attempting to repair existing venv_widows (python -m venv --upgrade) ...')
        subprocess.run([sys.executable, '-m', 'venv', '--upgrade', str(VENV_DIR)], check=True)
    except subprocess.CalledProcessError as exc:
        print(f'[WARN] venv repair failed with code {exc.returncode}')
        return False
    except Exception as exc:
        print(f'[WARN] venv repair failed: {exc}')
        return False

    return _venv_python_works()


def _ensure_venv() -> None:
    if _venv_python_works():
        return
    if PYTHON_BIN.exists():
        print('[WARN] Detected a broken/moved venv_widows, trying to repair it ...')
        if _repair_venv():
            return
        print('[WARN] Repair failed, recreating venv_widows (packages may need reinstall) ...')
    print('[INFO] Creating virtual environment at venv_widows ...')
    subprocess.run([sys.executable, '-m', 'venv', '--clear', str(VENV_DIR)], check=True)


def _ensure_dependencies() -> None:
    requirements = ROOT / 'requirements.txt'
    if not requirements.exists():
        print('[WARN] requirements.txt not found, skipping dependency installation')
        return
    if SKIP_PIP_INSTALL or not INSTALL_DEPENDENCIES:
        print('[WARN] Dependency installation skipped by env (SKIP_PIP_INSTALL/INSTALL_DEPENDENCIES)')
        return
    print('[INFO] Installing/upgrading dependencies ...')
    base_args = [
        '--retries',
        PIP_INSTALL_RETRIES,
        '--default-timeout',
        PIP_INSTALL_DEFAULT_TIMEOUT,
    ]
    if not _run_pip_command(
        [str(PYTHON_BIN), '-m', 'pip', 'install', '--upgrade', 'pip', *base_args],
        'pip upgrade',
    ):
        return
    _run_pip_command(
        [str(PYTHON_BIN), '-m', 'pip', 'install', '-r', str(requirements), *base_args],
        'dependency installation',
    )


def _run_pip_command(command: List[str], description: str) -> bool:
    """Execute pip command and continue even if it fails."""
    try:
        subprocess.run(command, check=True, timeout=PIP_INSTALL_TIMEOUT)
        return True
    except subprocess.CalledProcessError as exc:
        print(f'[WARN] {description} failed with code {exc.returncode}, continuing without stopping the server.')
    except subprocess.TimeoutExpired:
        print(f'[WARN] {description} timed out after {PIP_INSTALL_TIMEOUT}s, continuing without stopping the server.')
    except Exception as exc:
        print(f'[WARN] {description} encountered an unexpected error: {exc}. Continuing anyway.')
    return False


def _start_process(command: List[str], name: str) -> subprocess.Popen:
    print(f'[INFO] Starting {name}: {" ".join(command)}')
    return subprocess.Popen(command, cwd=ROOT)


def _ensure_process_alive(proc: subprocess.Popen, name: str, delay: float = 2.0) -> None:
    """Give the process a short warm-up window and fail fast if it exits."""
    time.sleep(delay)
    if proc.poll() is not None:
        raise RuntimeError(f'{name} exited immediately with code {proc.returncode}. Check its output for details.')


def main() -> None:
    _ensure_venv()
    _ensure_dependencies()

    processes: List[subprocess.Popen] = []
    try:
        log_level = os.getenv('UVICORN_LOG_LEVEL', 'warning')
        uvicorn_cmd = [
            str(PYTHON_BIN),
            '-m',
            'uvicorn',
            'main:app',
            '--host',
            os.getenv('API_HOST', '0.0.0.0'),
            '--port',
            os.getenv('API_PORT', '8000'),
            '--log-level',
            log_level,
            '--no-access-log',
        ]
        fastapi_proc = _start_process(uvicorn_cmd, 'FastAPI (uvicorn)')
        processes.append(fastapi_proc)
        _ensure_process_alive(fastapi_proc, 'FastAPI (uvicorn)')

        if GUI_ENABLED:
            gui_cmd = [str(PYTHON_BIN), '-m', 'app.gui.app']
            gui_proc = _start_process(gui_cmd, 'Monitoring GUI')
            processes.append(gui_proc)
            _ensure_process_alive(gui_proc, 'Monitoring GUI', delay=1.0)
        else:
            print('[INFO] GUI disabled by env (GUI_ENABLED=false), only FastAPI will run.')

        print('[INFO] All services started. Press Ctrl+C to stop.')
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n[INFO] Stop requested, shutting down ...')
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print('[INFO] All processes stopped.')


if __name__ == '__main__':
    if shutil.which('python') is None:
        raise SystemExit('Python is required to run this launcher.')
    main()
