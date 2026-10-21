"""Manage only this project's loopback-only development MongoDB replica set.

Run with the backend virtual environment. No server is started on import.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from pymongo import MongoClient
from pymongo.errors import AutoReconnect, ConnectionFailure, OperationFailure, PyMongoError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
REPLICA_SET = "rs0"


class ManagementError(RuntimeError):
    pass


def locations(port: int) -> tuple[Path, Path, Path]:
    root = PROJECT_ROOT / "output" / "dev-mongo" / str(port)
    return root, root / "data", root / "process.json"


def read_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if type(record["pid"]) is not int or record["pid"] <= 0:
            raise ValueError("invalid pid")
        return record
    except (KeyError, ValueError, OSError) as exc:
        raise ManagementError(f"Invalid process record: {path}; inspect it before continuing.") from exc


def canonical(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def client_for(port: int) -> MongoClient:
    return MongoClient(
        f"mongodb://127.0.0.1:{port}/?directConnection=true",
        serverSelectionTimeoutMS=800,
        connectTimeoutMS=800,
        socketTimeoutMS=2000,
    )


def server_identity(client: MongoClient) -> dict:
    status = client.admin.command("serverStatus")
    options = client.admin.command("getCmdLineOpts")["parsed"]
    return {
        "pid": int(status["pid"]),
        "dbpath": canonical(options.get("storage", {}).get("dbPath", "")),
        "port": int(options.get("net", {}).get("port", 27017)),
        "replicaSet": options.get("replication", {}).get("replSetName") or options.get("replication", {}).get("replSet"),
        "process": Path(status.get("process", "")).name.lower(),
    }


def require_owned(client: MongoClient, record: dict, data: Path, port: int) -> dict:
    identity = server_identity(client)
    if (
        identity["pid"] != record["pid"]
        or canonical(record.get("dbpath", "")) != canonical(data)
        or identity["dbpath"] != canonical(data)
        or identity["port"] != port
        or record.get("port") != port
        or identity["replicaSet"] != REPLICA_SET
        or identity["process"] not in {"mongod", "mongod.exe"}
    ):
        raise ManagementError("Server identity does not match this project's PID/dbPath/port/replica set; refusing to operate.")
    return identity


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def action_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "manage.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ManagementError(f"Another management operation holds {lock}. If interrupted, inspect the process before removing this lock.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def save_record(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temporary.replace(path)


def wait_primary(client: MongoClient, timeout: float = 35) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.admin.command("hello").get("isWritablePrimary"):
                return
        except PyMongoError:
            pass
        time.sleep(0.25)
    raise ManagementError("Replica set did not elect a primary within the bounded startup timeout; inspect mongod.log.")


def start(args: argparse.Namespace) -> None:
    root, data, pidfile = locations(args.port)
    with action_lock(root):
        record = read_record(pidfile)
        with client_for(args.port) as client:
            if port_open(args.port):
                if record is None:
                    raise ManagementError("Port is occupied without a project process record; refusing to reuse an unrelated service.")
                require_owned(client, record, data, args.port)
                wait_primary(client)
                print(f"Already running: PID {record['pid']}; {replica_uri(args.port)}")
                return

        binary = args.mongod or os.environ.get("MONGOD_BIN") or shutil.which("mongod")
        if not binary or not Path(binary).is_file():
            raise ManagementError("mongod not found. Add it to PATH, set MONGOD_BIN, or pass --mongod <absolute path>.")
        data.mkdir(parents=True, exist_ok=True)
        log = root / "mongod.log"
        command = [
            str(Path(binary).resolve()), "--dbpath", str(data.resolve()),
            "--logpath", str(log.resolve()), "--logappend",
            "--port", str(args.port), "--bind_ip", "127.0.0.1",
            "--replSet", REPLICA_SET,
        ]
        options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        record = {"pid": process.pid, "dbpath": str(data.resolve()), "port": args.port, "binary": command[0]}
        save_record(pidfile, record)
        deadline = time.monotonic() + 25
        with client_for(args.port) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ManagementError(f"mongod exited with code {process.returncode}; inspect {log}.")
                try:
                    client.admin.command("ping")
                    require_owned(client, record, data, args.port)
                    break
                except (ConnectionFailure, AutoReconnect):
                    time.sleep(0.25)
            else:
                raise ManagementError(f"mongod startup timed out; process record retained for safe stop. Inspect {log}.")
            try:
                client.admin.command("replSetGetStatus")
            except OperationFailure as exc:
                if exc.code != 94:  # NotYetInitialized
                    raise
                client.admin.command("replSetInitiate", {
                    "_id": REPLICA_SET,
                    "members": [{"_id": 0, "host": f"127.0.0.1:{args.port}"}],
                })
            wait_primary(client)
        print(f"Started: PID {process.pid}; {replica_uri(args.port)}")
        print(f"Data: {data}\nLog: {log}")


def replica_uri(port: int) -> str:
    return f"mongodb://127.0.0.1:{port}/?replicaSet={REPLICA_SET}"


def status(args: argparse.Namespace) -> None:
    _, data, pidfile = locations(args.port)
    record = read_record(pidfile)
    if not port_open(args.port):
        print("Stopped (port is not listening)." + (f" Recorded PID: {record['pid']}." if record else ""))
        return
    if record is None:
        raise ManagementError("Port is occupied by a service without this project's process record.")
    with client_for(args.port) as client:
        identity = require_owned(client, record, data, args.port)
        hello = client.admin.command("hello")
    print(f"Running: PID {identity['pid']}; primary={bool(hello.get('isWritablePrimary'))}; dbPath={data}")


def stop(args: argparse.Namespace) -> None:
    root, data, pidfile = locations(args.port)
    with action_lock(root):
        record = read_record(pidfile)
        if not port_open(args.port):
            print("Port is not listening; no process was terminated. Existing process record retained if present.")
            return
        if record is None:
            raise ManagementError("No project process record; refusing to stop an unrelated server.")
        with client_for(args.port) as client:
            require_owned(client, record, data, args.port)
            try:
                client.admin.command("shutdown", force=False, timeoutSecs=10)
            except (AutoReconnect, ConnectionFailure):
                pass  # A graceful shutdown closes this connection.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not port_open(args.port):
                pidfile.unlink(missing_ok=True)
                print("Stopped this project's MongoDB; all data retained.")
                return
            time.sleep(0.25)
        raise ManagementError("Shutdown did not finish within 20 seconds; no force kill attempted. Inspect mongod.log.")


def initialize(args: argparse.Namespace) -> None:
    _, data, pidfile = locations(args.port)
    record = read_record(pidfile)
    if record is None:
        raise ManagementError("Start this project's server before initialization.")
    with client_for(args.port) as client:
        require_owned(client, record, data, args.port)
        wait_primary(client)
    environment = os.environ.copy()
    environment["MONGODB_URI"] = replica_uri(args.port)
    environment["MONGODB_DATABASE"] = args.database
    subprocess.run([sys.executable, "-m", "app.bootstrap"], cwd=BACKEND_ROOT, env=environment, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "init"])
    parser.add_argument("--port", type=int, default=27018)
    parser.add_argument("--mongod", help="Path to mongod executable (start only)")
    parser.add_argument("--database", default="survey_system", help="Database to initialize (init only)")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        {"start": start, "stop": stop, "status": status, "init": initialize}[args.action](args)
        return 0
    except (ManagementError, PyMongoError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
