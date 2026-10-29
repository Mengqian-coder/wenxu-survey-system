"""Run the three validation follow-ups against an isolated real MongoDB.

From 01_代码, use backend/.venv/Scripts/python.exe -B
backend/scripts/run_validation_evidence.py. Optional --publish copies only a
successful, unchanged-source run into a new evidence directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from run_mechanism_evidence import (
    ROOT, junit_details, publish_evidence, sha256, source_snapshot, write_json,
)


TEST_SELECTION = (
    "tests/test_request_size_evidence.py",
    "tests/test_bson_size_evidence.py",
    "tests/test_api.py::test_request_body_limit_rejects_declared_and_actual_oversize",
    "tests/test_library_flow.py",
    "tests/test_cross_survey_statistics.py",
    "tests/test_question_banks.py",
    "tests/test_question_sharing.py",
    "tests/test_mutation_idempotency.py",
    "tests/test_management_races.py",
    "tests/test_frontend_support.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", type=Path, help="New evidence directory; never overwrite an earlier run")
    args = parser.parse_args()
    if args.publish and args.publish.exists():
        parser.error("--publish must name a directory that does not exist")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    destination = ROOT / "output" / "validation-evidence" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    before = source_snapshot()
    write_json(destination / "source-manifest-before.json", before)
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper() in {"MONGODB_URI", "MONGODB_DATABASE", "SECRET_KEY", "PYTHONPATH"}:
            environment.pop(key, None)
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-o", "junit_family=xunit1",
               "--junitxml=" + str(destination / "backend-junit.xml"), *TEST_SELECTION]
    report = {
        "runId": run_id, "startedAtUtc": datetime.now(timezone.utc).isoformat(),
        "scope": "Selected validation follow-ups and affected backend regression; isolated real MongoDB; no browser or full-backend rerun",
        "python": sys.version.split()[0],
        "dependencies": {name: version(name) for name in ("pytest", "pymongo", "fastapi", "httpx")},
        "selectedTests": list(TEST_SELECTION), "command": command,
    }
    print("Running validation evidence; directory: " + str(destination), flush=True)
    started = time.monotonic()
    with (destination / "pytest-console.txt").open("wb") as console:
        completed = subprocess.run(
            command, cwd=ROOT / "backend", env=environment, stdin=subprocess.DEVNULL,
            stdout=console, stderr=subprocess.STDOUT, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    result = {"exitCode": completed.returncode, "elapsedSeconds": round(time.monotonic() - started, 3)}
    try:
        result["counts"], result["testcases"] = junit_details(destination / "backend-junit.xml")
        nodes = ET.parse(destination / "backend-junit.xml").getroot().findall(".//testcase")
        for case, node in zip(result["testcases"], nodes, strict=True):
            case["properties"] = {
                prop.get("name"): prop.get("value") for prop in node.findall("./properties/property")
            }
    except (OSError, ET.ParseError, ValueError) as error:
        result["evidenceError"] = str(error)
        result["counts"] = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        result["testcases"] = []
    report["pytest"] = result
    after = source_snapshot()
    write_json(destination / "source-manifest-after.json", after)
    report["sourcesUnchangedDuringRun"] = before == after
    report["sourceFileCount"] = len(before)
    report["changedSourceFiles"] = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    counts = result["counts"]
    report["finishedAtUtc"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = (completed.returncode == 0 and counts["total"] > 0
                        and counts["passed"] == counts["total"] and before == after)
    write_json(destination / "run-summary.json", report)
    files = ["run-summary.json", "pytest-console.txt", "source-manifest-before.json", "source-manifest-after.json"]
    if (destination / "backend-junit.xml").exists():
        files.append("backend-junit.xml")
    write_json(destination / "artifact-manifest.json", {
        "runId": run_id, "algorithm": "SHA-256",
        "files": {name: {"bytes": (destination / name).stat().st_size, "sha256": sha256(destination / name)}
                  for name in files},
    })
    files.append("artifact-manifest.json")
    print(f"pytest: {counts['passed']}/{counts['total']} passed; source unchanged: {before == after}", flush=True)
    print("Evidence summary: " + str(destination / "run-summary.json"), flush=True)
    if not report["passed"]:
        return 1
    if args.publish:
        publish_evidence(destination, args.publish.resolve(), files, run_id)
        print("Published verified evidence: " + str(args.publish.resolve()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
