"""Execute isolated version/snapshot/permission evidence and retain its receipts.

Run with the backend virtual environment. No application .env is loaded here;
the pytest fixture creates and owns a separate MongoDB replica set/database.
Only an entirely successful run can be copied with --publish to a new directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
CASE_IDS = ("V01", "S01", "S02", "S03", "P01", "P02", "P03")
TEST_SELECTION = (
    "tests/test_version_snapshot_evidence.py",
    "tests/test_permission_evidence.py",
    "tests/test_management_races.py",
    "tests/test_mutation_idempotency.py::test_restore_replay_does_not_create_another_version",
    "tests/test_mutation_idempotency.py::test_commit_succeeded_but_confirmation_lost_keeps_key_and_never_reexecutes",
)
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_snapshot():
    """Hash the applicable backend/database source, including this runner."""
    patterns = (
        "backend/app/**/*.py", "backend/tests/**/*.py", "backend/scripts/**/*.py",
        "backend/requirements*.txt", "database/**/*.js", "database/**/*.json", "dev.ps1",
    )
    files = {path for pattern in patterns for path in ROOT.glob(pattern)
             if path.is_file() and not path.is_symlink()}
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in sorted(files)}


def junit_details(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    rows = []
    for case in cases:
        outcome = "passed"
        for tag in ("error", "failure", "skipped"):
            if case.find(tag) is not None:
                outcome = {"error": "error", "failure": "failed", "skipped": "skipped"}[tag]
                break
        rows.append({"classname": case.get("classname", ""), "name": case.get("name", ""),
                     "outcome": outcome, "elapsedSeconds": case.get("time", "")})
    counts = Counter(row["outcome"] for row in rows)
    return {"total": len(rows), "passed": counts["passed"], "failed": counts["failed"],
            "errors": counts["error"], "skipped": counts["skipped"]}, rows


def case_evidence(directory):
    """Count observed records and fail closed for missing/malformed case files."""
    summaries, documents, problems = [], {}, []
    present = {path.name for path in directory.glob("*.json")}
    expected = {case_id + ".json" for case_id in CASE_IDS}
    if present - expected:
        problems.append("Unexpected case JSON files: " + ", ".join(sorted(present - expected)))
    for case_id in CASE_IDS:
        relative = "cases/" + case_id + ".json"
        path = directory / (case_id + ".json")
        if not path.is_file() or path.is_symlink():
            problems.append(case_id + ": required case file is missing or is a symlink")
            summaries.append({"caseId": case_id, "file": relative, "passed": False, "available": False})
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("caseId") != case_id:
                raise ValueError("caseId does not match the expected filename")
            steps = document.get("steps")
            if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
                raise ValueError("steps must be a list of objects")
            http = [step for step in steps if step.get("kind") == "http"]
            checks = [step for step in steps if step.get("kind") == "assertion"]
            databases = [step for step in steps if step.get("kind") == "database"]
            if not http or not checks or not databases:
                raise ValueError("each case must contain HTTP, assertion and database evidence")
            if any(not isinstance(step.get("actualStatus"), int)
                   or not isinstance(step.get("expectedStatus"), int) for step in http):
                raise ValueError("HTTP evidence must contain integer expected/actual status codes")
            actual_http_passed = all(step.get("passed") is True
                                     and step["actualStatus"] == step["expectedStatus"] for step in http)
            checks_passed = all(step.get("passed") is True for step in checks)
            passed = document.get("passed") is True and actual_http_passed and checks_passed
            documents[case_id] = document
            summaries.append({
                "caseId": case_id, "title": document.get("title", case_id), "file": relative,
                "available": True, "passed": passed,
                "httpRequests": len(http),
                "httpStatusCounts": dict(sorted(Counter(str(step["actualStatus"]) for step in http).items())),
                "expectedRejections": sum(step["actualStatus"] >= 400 and step.get("passed") is True for step in http),
                "assertions": len(checks), "passedAssertions": sum(step.get("passed") is True for step in checks),
                "databaseSnapshots": len(databases),
                "databaseCollections": sorted({name for step in databases for name in step.get("collections", {})}),
            })
            if not passed:
                problems.append(case_id + ": the recorded case or one of its HTTP/assertion steps failed")
        except (OSError, ValueError, TypeError, KeyError) as error:
            problems.append(case_id + ": invalid evidence (" + type(error).__name__ + ")")
            summaries.append({"caseId": case_id, "file": relative, "passed": False, "available": False})
    return summaries, documents, problems


def md(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def markdown_report(report, documents):
    counts = report["pytest"]["counts"]
    lines = [
        "# 版本、快照与权限机制执行证据", "",
        "本报告由本次执行的 JUnit、病例 JSON、HTTP 回执和断言记录自动生成。", "",
        f"- 运行编号：`{report['runId']}`",
        f"- 开始时间（UTC）：{report['startedAtUtc']}",
        f"- 结束时间（UTC）：{report['finishedAtUtc']}",
        f"- 北京时间（Asia/Shanghai）：{report['startedAtAsiaShanghai']} 至 {report['finishedAtAsiaShanghai']}",
        f"- 本次结果：{'通过' if report['passed'] else '未通过；请检查失败记录，不可作为通过证明'}",
        f"- pytest 实际执行：{counts['total']} 项；通过 {counts['passed']}、失败 {counts['failed']}、错误 {counts['errors']}、跳过 {counts['skipped']}；进程退出码 {report['pytest']['exitCode']}。",
        f"- 专项病例：实际生成 {report['recordedCases']} 份，校验通过 {report['passedCases']} 份；必需病例 {len(CASE_IDS)} 份。",
        f"- 适用源码：{report['sourceFileCount']} 个后端、测试、脚本及数据库定义文件；执行期间{'未变化' if report['sourcesUnchangedDuringRun'] else '发生变化，本次结果不能对应同一份源码'}。", "",
        "## 可读证据目录", "",
        "| 病例 | 实际场景 | HTTP 请求 | 预期拒绝 | 断言通过/总数 | 数据库快照 | 结果 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        if case.get("available"):
            lines.append(f"| [{case['caseId']}]({case['file']}) | {md(case['title'])} | {case['httpRequests']} | {case['expectedRejections']} | {case['passedAssertions']}/{case['assertions']} | {case['databaseSnapshots']} | {'通过' if case['passed'] else '未通过'} |")
        else:
            lines.append(f"| {case['caseId']} | 文件缺失或不符合证据格式 | — | — | — | — | 未通过 |")
    lines.extend(["", "预期拒绝指真实返回的 4xx/5xx 与用例期望一致；这些反例通过不代表请求被允许。每份 JSON 记录脱敏的请求/响应、数据库文档及 SHA-256。", "",
                  "## 实际 HTTP 与业务断言", ""])
    for case_id in CASE_IDS:
        document = documents.get(case_id)
        if not document:
            continue
        lines.extend([f"### {case_id} · {md(document.get('title', case_id))}", "",
                      "| 操作者 | 请求 | 预期状态 | 实际状态 | 结果 |",
                      "|---|---|---:|---:|---|"])
        for step in document["steps"]:
            if step.get("kind") == "http":
                lines.append(f"| {md(step.get('actor', ''))} | `{md(step.get('method', ''))} {md(step.get('path', ''))}` | {step['expectedStatus']} | {step['actualStatus']} | {'符合预期' if step.get('passed') is True else '失败'} |")
        lines.extend(["", "| 已执行的业务断言 | 结果 |", "|---|---|"])
        for step in document["steps"]:
            if step.get("kind") == "assertion":
                lines.append(f"| {md(step.get('label', ''))} | {'通过' if step.get('passed') is True else '失败'} |")
        lines.append("")
    lines.extend(["## 其他已执行的并发和重试检查", "",
                  "下表来自本次 JUnit；这些已有测试没有另外生成逐步病例 JSON，不能把其断言总数算入上方的病例断言数。", "",
                  "| 测试 | 实际结果 |", "|---|---|"])
    for test in report["pytest"]["testcases"]:
        if "test_management_races" in test["classname"] or "test_mutation_idempotency" in test["classname"]:
            lines.append(f"| `{md(test['classname'])}::{md(test['name'])}` | {md(test['outcome'])} |")
    lines.extend(["", "## 证据边界与复核", "",
                  "- 测试通过 FastAPI TestClient 执行真实应用路由和真实 MongoDB 单节点复本集；不是浏览器界面或公网部署验收。",
                  "- 所有业务数据为合成测试数据；测试夹具创建独立子进程和随机数据库，不使用日常开发库。",
                  "- 历史内容不可覆盖指正常应用接口只追加版本；归档状态仍可更新。MongoDB JSON Schema 和唯一索引不等于字段级不可变，数据库管理员的直接改写不在本次证明范围内。",
                  "- 并发测试协调实际数据库写入顺序，并断言真实事务冲突；提交确认丢失是明确的故障注入场景。",
                  "- 病例 JSON 不记录登录请求和认证头，敏感凭据已脱敏，二进制仅保留长度和 SHA-256。源码摘要关联的是本地工作树，不表示已经 Git 提交或推送。",
                  "- [run-summary.json](run-summary.json) 给出机器可读结论；[backend-junit.xml](backend-junit.xml) 和 [pytest-console.txt](pytest-console.txt) 保留测试执行回执。",
                  "- [执行前源码摘要](source-manifest-before.json) 与 [执行后源码摘要](source-manifest-after.json) 应完全一致；[文件校验清单](artifact-manifest.json) 可用于复核证据文件 SHA-256。", ""])
    if report["problems"]:
        lines.extend(["## 本次未通过的原因", ""])
        lines.extend("- " + md(problem) for problem in report["problems"])
        lines.append("")
    return "\n".join(lines)


def prepare_output(parser, candidate):
    if candidate.is_symlink():
        parser.error("Output must not be a symbolic link")
    destination = candidate.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        parser.error("Output must be a new or empty directory; earlier evidence is never overwritten")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def publish_evidence(source, destination, relative_files, run_id):
    """Copy a literal file allowlist into a new directory, verifying every byte."""
    destination.mkdir(parents=True, exist_ok=False)
    copied = {}
    for relative in relative_files:
        original = source / relative
        target = destination / relative
        if original.is_symlink() or not original.is_file():
            raise ValueError("Only regular allowlisted evidence files may be published")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an unexpected concurrent file as well.
        with original.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        expected = sha256(original)
        if sha256(target) != expected:
            raise ValueError("A published evidence file failed SHA-256 verification")
        copied[relative] = {"bytes": original.stat().st_size, "sha256": expected}
    receipt = {"runId": run_id, "publishedAtUtc": now_utc(), "verified": True,
               "scope": "Only explicitly listed evidence files were copied; no MongoDB data/logs, environment files or runtime directories.",
               "files": copied}
    # This final receipt distinguishes a complete copy from a partial failed one.
    with (destination / "publish-receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New/empty run directory; default output/mechanism-evidence/<run-id>")
    parser.add_argument("--publish", type=Path, help="After success only, copy explicit evidence files to this new directory (must not exist)")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    publish_target = args.publish.resolve() if args.publish else None
    if args.publish and (args.publish.is_symlink() or publish_target.exists()):
        parser.error("Publish destination must not exist; existing evidence is never overwritten")
    destination = prepare_output(parser, args.output or ROOT / "output/mechanism-evidence" / run_id)
    if publish_target and (publish_target == destination or destination.is_relative_to(publish_target)
                           or publish_target.is_relative_to(destination)):
        parser.error("Output and publish destinations must be separate, non-nested directories")
    (destination / "cases").mkdir()
    before = source_snapshot()
    write_json(destination / "source-manifest-before.json", before)
    started = now_utc()
    dependencies = {}
    for package in ("pytest", "pymongo", "fastapi", "httpx"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = "not-installed"
    report = {
        "runId": run_id, "startedAtUtc": started,
        "startedAtAsiaShanghai": datetime.fromisoformat(started).astimezone(SHANGHAI).isoformat(),
        "displayTimezone": "Asia/Shanghai", "python": sys.version.split()[0], "dependencies": dependencies,
        "scope": "Selected API/TestClient checks against real, isolated MongoDB fixtures; no browser or production database acceptance is claimed.",
        "sourceScope": "backend/app, backend/tests, backend/scripts Python; backend requirements; database JS/JSON; dev.ps1. Frontend source is not part of this run.",
        "selectedTests": list(TEST_SELECTION), "publishTargetRequested": str(publish_target) if publish_target else None,
        "problems": [],
    }
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper() in {"MONGODB_URI", "MONGODB_DATABASE", "SECRET_KEY", "PYTHONPATH"}:
            environment.pop(key, None)
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                       WENXU_EVIDENCE_DIR=str(destination / "cases"))
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "--junitxml=" + str(destination / "backend-junit.xml"), *TEST_SELECTION]
    print("Running isolated mechanism evidence; directory: " + str(destination), flush=True)
    elapsed_start = time.monotonic()
    result = {"exitCode": None, "counts": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0},
              "testcases": []}
    try:
        with (destination / "pytest-console.txt").open("wb") as console:
            result["exitCode"] = subprocess.run(
                command, cwd=ROOT / "backend", env=environment, stdin=subprocess.DEVNULL,
                stdout=console, stderr=subprocess.STDOUT, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode
        result["counts"], result["testcases"] = junit_details(destination / "backend-junit.xml")
    except (OSError, ET.ParseError, ValueError) as error:
        report["problems"].append("Test process/JUnit evidence unavailable (" + type(error).__name__ + ")")
    result["elapsedSeconds"] = round(time.monotonic() - elapsed_start, 3)
    report["pytest"] = result
    if result["exitCode"] != 0:
        report["problems"].append("pytest did not exit successfully")
    counts = result["counts"]
    if counts["total"] == 0 or counts["passed"] != counts["total"]:
        report["problems"].append("Every selected pytest item must pass without errors or skips")
    cases, documents, case_problems = case_evidence(destination / "cases")
    report["cases"] = cases
    report["recordedCases"] = len(documents)
    report["passedCases"] = sum(case["passed"] is True for case in cases)
    report["httpRequests"] = sum(case.get("httpRequests", 0) for case in cases)
    report["assertions"] = sum(case.get("assertions", 0) for case in cases)
    report["databaseSnapshots"] = sum(case.get("databaseSnapshots", 0) for case in cases)
    report["problems"].extend(case_problems)
    after = source_snapshot()
    write_json(destination / "source-manifest-after.json", after)
    report["sourceFileCount"] = len(before)
    report["sourcesUnchangedDuringRun"] = before == after
    report["changedSourceFiles"] = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    if before != after:
        report["problems"].append("Backend/database source changed during this run")
    finished = now_utc()
    report["finishedAtUtc"] = finished
    report["finishedAtAsiaShanghai"] = datetime.fromisoformat(finished).astimezone(SHANGHAI).isoformat()
    report["passed"] = not report["problems"] and report["passedCases"] == len(CASE_IDS)
    write_json(destination / "run-summary.json", report)
    (destination / "验收摘要.md").write_text(markdown_report(report, documents), encoding="utf-8")
    # A literal publication list deliberately excludes MongoDB files/logs and .env.
    files = ["run-summary.json", "source-manifest-before.json", "source-manifest-after.json", "验收摘要.md"]
    files.extend(name for name in ("backend-junit.xml", "pytest-console.txt") if (destination / name).is_file())
    files.extend("cases/" + case_id + ".json" for case_id in CASE_IDS if (destination / "cases" / (case_id + ".json")).is_file())
    manifest = {"runId": run_id, "algorithm": "SHA-256", "files": {
        name: {"bytes": (destination / name).stat().st_size, "sha256": sha256(destination / name)} for name in files}}
    write_json(destination / "artifact-manifest.json", manifest)
    files.append("artifact-manifest.json")
    print(f"pytest: {counts['passed']}/{counts['total']} passed; cases: {report['passedCases']}/{len(CASE_IDS)} passed; source unchanged: {report['sourcesUnchangedDuringRun']}", flush=True)
    print("Evidence summary: " + str(destination / "run-summary.json"), flush=True)
    if not report["passed"]:
        if publish_target:
            print("Run did not pass; evidence was retained locally and was not published.", flush=True)
        return 1
    if publish_target:
        try:
            receipt = publish_evidence(destination, publish_target, files, run_id)
            write_json(destination / "publish-receipt.json", receipt)
        except (OSError, ValueError) as error:
            # A partial target is never silently removed or overwritten. Without
            # its final publish-receipt.json it must not be treated as delivered.
            print("Evidence passed, but publication failed (" + type(error).__name__ + "); inspect the new target directory. Existing files were not overwritten.", flush=True)
            return 1
        print("Published verified evidence: " + str(publish_target), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
