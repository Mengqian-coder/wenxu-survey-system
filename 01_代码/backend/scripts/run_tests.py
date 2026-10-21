"""Run isolated project checks and retain machine-readable evidence for each run.

From the project/code directory:
    backend/.venv/Scripts/python.exe backend/scripts/run_tests.py --suite all
No .env is read by this script, and the test fixtures create their own MongoDB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def source_snapshot():
    patterns = ["backend/app/*.py", "backend/scripts/*.py", "backend/tests/*.py",
                "database/*.js", "database/*.json", "frontend/src/**/*.js", "frontend/src/**/*.jsx",
                "frontend/src/**/*.css", "frontend/tests/*.js", "frontend/tests/*.py",
                "frontend/*config.js", "frontend/package*.json", "backend/requirements*.txt"]
    files = {path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()}
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def junit_counts(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    failed = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    return {"total": len(cases), "passed": len(cases) - failed - errors - skipped,
            "failed": failed, "errors": errors, "skipped": skipped}


def free_port(exclude=()):
    for _ in range(20):
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        if port not in exclude:
            return port
    raise RuntimeError("Could not allocate an isolated test port")


def run_command(command, cwd, environment, stdout, stderr=None):
    with stdout.open("wb") as out:
        if stderr is None:
            return subprocess.run(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=subprocess.STDOUT, check=False).returncode
        with stderr.open("wb") as err:
            return subprocess.run(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=err, check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["all", "backend", "frontend", "e2e"], default="all")
    parser.add_argument("--output", type=Path, help="New empty evidence directory; default output/test-runs/<run-id>")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    destination = (args.output or ROOT / "output/test-runs" / run_id).resolve()
    if destination.exists() and any(destination.iterdir()):
        parser.error("Output directory must be new or empty; earlier results are never overwritten")
    destination.mkdir(parents=True, exist_ok=True)
    selected = ["backend", "frontend", "e2e"] if args.suite == "all" else [args.suite]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    environment.pop("PYTHONPATH", None)
    # Explicit test Settings override application defaults; never connect to the user's DB.
    for key in ["MONGODB_URI", "MONGODB_DATABASE", "SECRET_KEY"]:
        environment.pop(key, None)
    node = shutil.which("node")
    if node:
        environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")
    before = source_snapshot()
    report = {"runId": run_id, "startedAtUtc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version.split()[0],
              "dependencies": {name: version(name) for name in ["pytest", "pymongo", "fastapi"]},
              "scope": "Only selected suites are counted. MongoDB fixtures create isolated child processes.",
              "suites": {}}
    for suite in selected:
        print(f"Running {suite}; evidence: {destination}", flush=True)
        started = time.monotonic()
        result = {}
        try:
            if suite == "backend":
                result["exitCode"] = run_command(
                    [sys.executable, "-B", "-m", "pytest", "-q", "--junitxml=" + str(destination / "backend-junit.xml")],
                    ROOT / "backend", environment, destination / "backend-console.txt")
                result["counts"] = junit_counts(destination / "backend-junit.xml")
                result["evidence"] = ["backend-junit.xml", "backend-console.txt"]
            elif suite == "frontend":
                if not node:
                    raise RuntimeError("Node.js must be available on PATH for frontend tests")
                tests = sorted((ROOT / "frontend/tests").glob("*.test.js"))
                result["exitCode"] = run_command(
                    [node, "--test", "--test-reporter=junit", *map(str, tests)],
                    ROOT / "frontend", environment, destination / "frontend-junit.xml", destination / "frontend-stderr.txt")
                result["counts"] = junit_counts(destination / "frontend-junit.xml")
                result["evidence"] = ["frontend-junit.xml", "frontend-stderr.txt"]
            else:
                env = dict(environment, E2E_OUTPUT_DIR=str(destination / "e2e"))
                api_port = free_port(exclude=[8000])
                env.update(E2E_API_PORT=str(api_port), E2E_UI_PORT=str(free_port(exclude=[8000, api_port])))
                result["exitCode"] = run_command(
                    [sys.executable, "-B", str(ROOT / "frontend/tests/start-e2e.py")],
                    ROOT, env, destination / "e2e-console.txt")
                stats = json.loads((destination / "e2e/results.json").read_text(encoding="utf-8"))["stats"]
                result["counts"] = {"total": sum(stats.get(k, 0) for k in ["expected", "unexpected", "flaky", "skipped"]),
                                    "passed": stats.get("expected", 0), "failed": stats.get("unexpected", 0),
                                    "flaky": stats.get("flaky", 0), "skipped": stats.get("skipped", 0)}
                result["evidence"] = ["e2e/results.json", "e2e/report/index.html", "e2e-console.txt"]
            counts = result["counts"]
            result["passed"] = (result["exitCode"] == 0 and counts["total"] > 0
                                and counts["passed"] == counts["total"])
        except Exception as exc:
            result.update(passed=False, error=str(exc))
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        report["suites"][suite] = result
        print(f"{suite}: " + json.dumps(result, ensure_ascii=False), flush=True)
    report["finishedAtUtc"] = datetime.now(timezone.utc).isoformat()
    report["sourceFilesSha256"] = before
    report["sourcesUnchangedDuringRun"] = source_snapshot() == before
    report["passed"] = report["sourcesUnchangedDuringRun"] and all(r["passed"] for r in report["suites"].values())
    (destination / "run-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Evidence summary: " + str(destination / "run-summary.json"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
