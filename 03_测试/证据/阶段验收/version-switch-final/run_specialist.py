from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FRONTEND = ROOT / 'frontend'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def snapshot():
    patterns = ['src/**/*', 'tests/**/*', 'dist/**/*', '*config.js', 'package*.json', 'index.html']
    files = {p for pattern in patterns for p in FRONTEND.glob(pattern)
             if p.is_file() and '__pycache__' not in p.parts and not p.is_symlink()}
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def free_port(excluded=()):
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        if port not in excluded and port not in (8000, 8001, 5175, 27019):
            return port
    raise RuntimeError('Unable to allocate independent QA ports')


started = datetime.now(timezone.utc)
clock = time.monotonic()
before = snapshot()
save('source-manifest-before.json', before)
api_port = free_port()
ui_port = free_port((api_port,))
environment = {**os.environ, 'E2E_API_PORT': str(api_port), 'E2E_UI_PORT': str(ui_port), 'E2E_OUTPUT_DIR': str(OUT), 'PYTHONIOENCODING': 'utf-8'}
for name in ('MONGODB_URI', 'MONGODB_DATABASE', 'SECRET_KEY', 'AUTO_INIT_DB', 'API_TARGET', 'E2E_BASE_URL', 'CORS_ORIGINS'):
    environment.pop(name, None)
command = [sys.executable, str(FRONTEND / 'tests/start-e2e.py'), 'version-switch.spec.js']
print(f'Starting 2 browser specialist checks; API {api_port}, UI {ui_port}', flush=True)
with (OUT / 'e2e-console.txt').open('wb') as log:
    completed = subprocess.run(command, cwd=FRONTEND, env=environment, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
after = snapshot()
save('source-manifest-after.json', after)
report = json.loads((OUT / 'results.json').read_text(encoding='utf-8')) if (OUT / 'results.json').is_file() else {}
stats = report.get('stats', {})
counts = {'total': sum(stats.get(k, 0) for k in ('expected', 'unexpected', 'flaky', 'skipped')),
          'passed': stats.get('expected', 0), 'failed': stats.get('unexpected', 0),
          'flaky': stats.get('flaky', 0), 'skipped': stats.get('skipped', 0)}
unchanged = before == after
passed = completed.returncode == 0 and unchanged and counts == {'total': 2, 'passed': 2, 'failed': 0, 'flaky': 0, 'skipped': 0}
summary = {'startedAtUtc': started.isoformat(), 'finishedAtUtc': datetime.now(timezone.utc).isoformat(),
           'scope': 'Only version-switch.spec.js: 2 targeted real-browser checks. Not a full 14-case suite rerun.',
           'isolation': 'start-e2e.py creates its own MongoDB replica child process and random test_frontend database; existing development services are not used.',
           'qaPorts': {'api': api_port, 'frontend': ui_port}, 'exitCode': completed.returncode,
           'suites': {'versionSwitchBrowser': counts}, 'sourcesUnchangedDuringRun': unchanged,
           'sourceHashScope': 'frontend/src, frontend/tests, frontend/*config.js, frontend/package*.json, frontend/index.html, frontend/dist',
           'sourceFileCount': len(before),
           'changedPaths': sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)),
           'evidence': ['results.json', 'report/index.html', 'e2e-console.txt', 'source-manifest-before.json', 'source-manifest-after.json'],
           'elapsedSeconds': round(time.monotonic() - clock, 3), 'passed': passed}
save('run-summary.json', summary)
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
if not passed:
    print((OUT / 'e2e-console.txt').read_text(encoding='utf-8', errors='replace'), flush=True)
raise SystemExit(0 if passed else 1)
