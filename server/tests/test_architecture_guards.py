from __future__ import annotations

import ast
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = SERVER_ROOT / "app"


def _iter_python_files():
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _parse_module(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_only_db_modules_open_database_sessions():
    allowed_session_calls = {
        Path("db/session.py"),
        Path("runtime/db_worker.py"),
    }
    offenders: list[str] = []
    for path in _iter_python_files():
        rel_path = path.relative_to(APP_ROOT)
        module = _parse_module(path)
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "session_scope":
                if rel_path not in allowed_session_calls:
                    offenders.append(str(rel_path))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Session":
                if rel_path not in allowed_session_calls:
                    offenders.append(str(rel_path))
    assert offenders == [], f"Direct DB session usage found outside DB worker ownership: {sorted(set(offenders))}"


def test_only_mqtt_worker_instantiates_mqtt_manager():
    allowed = {Path("runtime/mqtt_worker.py")}
    offenders: list[str] = []
    for path in _iter_python_files():
        rel_path = path.relative_to(APP_ROOT)
        module = _parse_module(path)
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "MQTTManager":
                if rel_path not in allowed:
                    offenders.append(str(rel_path))
    assert offenders == [], f"MQTTManager must be owned only by mqtt-worker: {sorted(set(offenders))}"


def test_api_routes_are_async():
    routes_path = APP_ROOT / "api" / "routes.py"
    module = _parse_module(routes_path)
    offenders: list[str] = []
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "router":
                if not isinstance(node, ast.AsyncFunctionDef):
                    offenders.append(node.name)
                break
    assert offenders == [], f"API route handlers must be async def: {offenders}"


def test_gui_main_does_not_import_requests_or_spawn_threads():
    gui_path = APP_ROOT / "gui" / "app.py"
    module = _parse_module(gui_path)
    has_requests_import = False
    thread_calls: list[int] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "requests":
                    has_requests_import = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "requests":
                has_requests_import = True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "threading" and func.attr == "Thread":
                    thread_calls.append(node.lineno)
    assert not has_requests_import, "GUI main must not import requests"
    assert thread_calls == [], f"GUI main must not spawn background threads: {thread_calls}"


def test_runtime_code_does_not_instantiate_legacy_cableway_service():
    offenders: list[str] = []
    for path in _iter_python_files():
        rel_path = path.relative_to(APP_ROOT)
        module = _parse_module(path)
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"CablewayPLCService", "LegacyCablewayPLCService"}:
                offenders.append(f"{rel_path}:{node.lineno}")
            elif isinstance(func, ast.Attribute) and func.attr in {"CablewayPLCService", "LegacyCablewayPLCService"}:
                offenders.append(f"{rel_path}:{node.lineno}")
    assert offenders == [], f"Legacy in-process PLC service must not be instantiated by runtime code: {offenders}"


def test_only_runtime_telemetry_bridge_uses_media_write_worker():
    allowed = {Path("runtime/telemetry_bridge.py"), Path("api/__init__.py")}
    offenders: list[str] = []
    for path in _iter_python_files():
        rel_path = path.relative_to(APP_ROOT)
        module = _parse_module(path)
        for node in ast.walk(module):
            if isinstance(node, ast.Name) and node.id == "MediaWriteWorker":
                if rel_path not in allowed:
                    offenders.append(str(rel_path))
    assert offenders == [], f"MediaWriteWorker must be wired only in app bootstrap/runtime bridge: {sorted(set(offenders))}"


def test_media_posts_use_media_db_worker():
    routes_path = APP_ROOT / "api" / "routes.py"
    source = routes_path.read_text(encoding="utf-8")
    required_markers = [
        "def _get_media_db_worker(",
        "def _get_media_data_service(",
        "media_data_service = _get_media_data_service(request)",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert missing == [], f"Media write path must use isolated media DB worker markers missing={missing}"


def test_api_bootstrap_wires_read_write_db_workers():
    source = (APP_ROOT / "api" / "__init__.py").read_text(encoding="utf-8")
    required_markers = [
        "read_db_worker = DBWorker(",
        "write_db_worker = DBWorker(",
        "application.state.read_db_worker = read_db_worker",
        "application.state.write_db_worker = write_db_worker",
        "read_db_worker=read_db_worker",
        "write_db_worker=write_db_worker",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert missing == [], f"Read/write DB worker wiring markers missing={missing}"


def test_data_service_dependency_uses_read_write_routing():
    source = (APP_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    required_markers = [
        "from app.runtime import RoutedAsyncDataServiceProxy",
        "return RoutedAsyncDataServiceProxy(read_worker=read_db_worker, write_worker=write_db_worker)",
        "def _get_read_db_worker(",
        "def _get_write_db_worker(",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert missing == [], f"Read/write data-service routing markers missing={missing}"


def test_runtime_media_status_route_exists():
    source = (APP_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    required_markers = [
        "@router.get('/runtime/media-status')",
        "async def runtime_media_status(",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert missing == [], f"runtime media status route markers missing={missing}"


def test_api_slow_request_middleware_exists():
    source = (APP_ROOT / "api" / "__init__.py").read_text(encoding="utf-8")
    required_markers = [
        "@application.middleware(\"http\")",
        "async def _slow_request_logging(",
        "Slow API request method=%s path=%s",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert missing == [], f"slow request middleware markers missing={missing}"
