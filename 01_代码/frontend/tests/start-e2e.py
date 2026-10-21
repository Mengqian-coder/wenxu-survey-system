"""Run browser checks using only isolated QA services, never localhost:8000.

From frontend/:
    ../backend/.venv/Scripts/python.exe tests/start-e2e.py

Use --serve-only to keep QA services alive for manual inspection until Ctrl+C.
All child handles and test data belong to this invocation. Data/logs are retained.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def wait_http(url: str, process: subprocess.Popen, timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QA service exited before becoming ready: {url}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, OSError):
            pass
        time.sleep(0.15)
    raise RuntimeError(f"QA service did not become ready: {url}")


def require_free_port(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"QA port {port} is already in use; no existing service was stopped.") from exc


def stop_owned(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-only", action="store_true")
    args, playwright_args = parser.parse_known_args()
    api_port = int(os.environ.get("E2E_API_PORT", "8788"))
    ui_port = int(os.environ.get("E2E_UI_PORT", "5174"))
    if api_port == 8000:
        raise RuntimeError("E2E_API_PORT must not use the existing development API port 8000.")
    require_free_port(api_port)
    require_free_port(ui_port)
    node = shutil.which("node")
    vite = FRONTEND / "node_modules/vite/bin/vite.js"
    playwright = FRONTEND / "node_modules/@playwright/test/cli.js"
    if not node or not vite.is_file() or not playwright.is_file():
        raise RuntimeError("Install frontend dependencies with npm install first.")

    spec = importlib.util.spec_from_file_location("isolated_backend_fixture", ROOT / "backend/tests/conftest.py")
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    replica = fixture_module.replica_uri.__wrapped__()
    artifact_root = Path(os.environ.get("E2E_OUTPUT_DIR", str(ROOT / "output/frontend-e2e")))
    run_dir = artifact_root / "runs" / secrets.token_hex(8)
    run_dir.mkdir(parents=True)
    api_url = f"http://127.0.0.1:{api_port}"
    ui_url = f"http://127.0.0.1:{ui_port}"
    env = {
        **os.environ,
        "MONGODB_DATABASE": "test_frontend_" + secrets.token_hex(12),
        "SECRET_KEY": secrets.token_urlsafe(64),
        "AUTO_INIT_DB": "true",
        "API_TARGET": api_url,
        "E2E_BASE_URL": ui_url,
        "CORS_ORIGINS": '["' + ui_url + '"]',
    }
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        env["MONGODB_URI"] = next(replica)
        with ExitStack() as stack:
            api_log = stack.enter_context((run_dir / "backend.log").open("wb"))
            api_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port), "--no-access-log"],
                cwd=ROOT / "backend", env=env, stdin=subprocess.DEVNULL,
                stdout=api_log, stderr=subprocess.STDOUT, creationflags=flags,
            )
            stack.callback(stop_owned, api_process)
            wait_http(api_url + "/health", api_process)
            frontend_log = stack.enter_context((run_dir / "frontend.log").open("wb"))
            frontend_process = subprocess.Popen(
                [node, str(vite), "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"],
                cwd=FRONTEND, env=env, stdin=subprocess.DEVNULL,
                stdout=frontend_log, stderr=subprocess.STDOUT, creationflags=flags,
            )
            stack.callback(stop_owned, frontend_process)
            wait_http(ui_url, frontend_process)
            print(f"Isolated QA frontend: {ui_url}", flush=True)
            print(f"Logs retained: {run_dir}", flush=True)
            if args.serve_only:
                print("Press Ctrl+C to stop only these QA services.", flush=True)
                while True:
                    if api_process.poll() is not None or frontend_process.poll() is not None:
                        raise RuntimeError("A QA service exited; inspect retained logs.")
                    time.sleep(1)
            return subprocess.call(
                [node, str(playwright), "test", *playwright_args],
                cwd=FRONTEND, env=env, creationflags=flags,
            )
    except Exception:
        print(f"QA startup/test failure; logs retained: {run_dir}", flush=True)
        raise
    finally:
        replica.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("QA services stopped.")
        raise SystemExit(130)
