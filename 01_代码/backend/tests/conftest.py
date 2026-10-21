"""API tests use an isolated, real single-node MongoDB replica set.

The process and files belong to this test session only. Logs are retained under
output/backend-tests for diagnosis; an existing MongoDB service is never used.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError


@pytest.fixture(scope="session")
def replica_uri():
    configured = os.environ.get("MONGOD_BIN") or os.environ.get("MONGOD_PATH")
    binary = configured or shutil.which("mongod") or "D:/Softwares/MongoDB/bin/mongod.exe"
    if not Path(binary).is_file():
        pytest.fail("Real MongoDB integration tests require mongod; set MONGOD_BIN.")
    workspace = Path(__file__).resolve().parents[2]
    run_dir = workspace / "output" / "backend-tests" / uuid.uuid4().hex
    data_dir = run_dir / "db"
    data_dir.mkdir(parents=True)
    with socket.socket() as available_port:
        available_port.bind(("127.0.0.1", 0))
        port = available_port.getsockname()[1]
    repl_name = "backend_test_rs"
    direct_uri = f"mongodb://127.0.0.1:{port}/?directConnection=true"
    uri = f"mongodb://127.0.0.1:{port}/?replicaSet={repl_name}"
    command = [
        str(binary), "--dbpath", str(data_dir), "--bind_ip", "127.0.0.1",
        "--port", str(port), "--replSet", repl_name,
        "--logpath", str(run_dir / "mongod.log"),
        "--oplogSize", "64", "--wiredTigerCacheSizeGB", "0.25",
    ]
    with (run_dir / "process.log").open("wb") as process_log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=process_log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        connection = MongoClient(direct_uri, serverSelectionTimeoutMS=500)
        try:
            until = time.monotonic() + 40
            while True:
                if process.poll() is not None:
                    pytest.fail(f"Test mongod exited; inspect {run_dir}")
                try:
                    connection.admin.command("ping")
                    break
                except PyMongoError:
                    if time.monotonic() >= until:
                        pytest.fail(f"Test mongod did not start; inspect {run_dir}")
                    time.sleep(0.1)
            connection.admin.command({
                "replSetInitiate": {
                    "_id": repl_name,
                    "members": [{"_id": 0, "host": f"127.0.0.1:{port}"}],
                }
            })
            while not connection.admin.command("hello").get("isWritablePrimary"):
                if time.monotonic() >= until:
                    pytest.fail(f"Test replica set did not elect a primary; inspect {run_dir}")
                time.sleep(0.1)
            yield uri
        finally:
            connection.close()
            # Operate only on the child handle created above, never by process name.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


@pytest.fixture
def api(replica_uri):
    from app.config import Settings
    from app.main import create_app

    db_name = "test_backend_" + uuid.uuid4().hex
    settings = Settings(
        _env_file=None,
        mongodb_uri=replica_uri,
        mongodb_database=db_name,
        secret_key="integration-only-" + "a3d76f42" * 10,
        auto_init_db=True,
    )
    application = create_app(settings=settings)
    with TestClient(application, raise_server_exceptions=False) as client:
        try:
            yield client
        finally:
            # This random database was created by this fixture on its owned replica.
            application.state.client.drop_database(db_name)


@pytest.fixture
def db(api):
    return api.app.state.db
