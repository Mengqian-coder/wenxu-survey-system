"""Execute the public 12 MiB request limit without reducing its configuration.

The payload is a small, valid registration JSON followed by insignificant JSON
whitespace. Its business data stays valid while its UTF-8 byte size hits the
real transport boundary exactly. No credentials or request bodies are recorded.
"""

import asyncio
import json

from bson import ObjectId
import httpx
import pytest

from test_api import PASSWORD, assert_api_error, database_snapshot


# This is the documented contract, deliberately independent of Settings' value.
DOCUMENTED_REQUEST_LIMIT = 12_582_912


def registration_body(byte_count):
    payload = json.dumps(
        {"username": "size_boundary", "password": PASSWORD},
        separators=(",", ":"),
    ).encode("utf-8")
    assert byte_count >= len(payload)
    return payload + b" " * (byte_count - len(payload))


@pytest.fixture
def request_size_baseline(api, db):
    assert api.app.state.settings.max_request_bytes == DOCUMENTED_REQUEST_LIMIT
    seeded = api.post(
        "/api/auth/register",
        json={"username": "size_existing", "password": PASSWORD},
    )
    assert seeded.status_code == 201, seeded.text
    before = database_snapshot(db)
    assert len(before["users"]) == 1
    return before


def stream_registration(api, body, declared_length):
    """Deliver real ASGI chunks; TestClient otherwise combines a generator.

    The existing api fixture owns the app's lifespan and isolated real MongoDB.
    Calling that same ASGI app here exercises its complete middleware chain,
    while allowing a missing/false Content-Length and actual multi-chunk reads.
    """
    headers = [(b"content-type", b"application/json")]
    if declared_length is not None:
        headers.append((b"content-length", declared_length.encode("ascii")))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/auth/register", "raw_path": b"/api/auth/register",
        "root_path": "", "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 31000), "server": ("testserver", 80),
        "state": {},
    }
    sent, consumed_sizes = [], []
    offset = 0

    async def receive():
        nonlocal offset
        if offset == len(body):
            return {"type": "http.disconnect"}
        chunk = body[offset:offset + 1024 * 1024]
        offset += len(chunk)
        consumed_sizes.append(len(chunk))
        return {
            "type": "http.request", "body": chunk,
            "more_body": offset < len(body),
        }

    async def send(message):
        sent.append(message)

    asyncio.run(api.app(scope, receive, send))
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    response = httpx.Response(
        starts[0]["status"],
        headers=starts[0].get("headers", []),
        content=b"".join(message.get("body", b"") for message in sent
                         if message["type"] == "http.response.body"),
    )
    return response, consumed_sizes


@pytest.mark.parametrize("byte_count", [
    pytest.param(12_582_911, id="12MiB-minus-one"),
    pytest.param(12_582_912, id="12MiB-exact"),
])
def test_default_request_limit_accepts_real_boundary_and_persists(
    api, db, request_size_baseline, record_property, byte_count,
):
    body = registration_body(byte_count)
    assert len(body) == byte_count
    request = api.build_request(
        "POST", "/api/auth/register", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert request.headers["Content-Length"] == str(byte_count)
    response = api.send(request)
    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result) == {"_id", "username", "createdAt"}
    assert result["username"] == "size_boundary"
    saved = db.users.find_one({"_id": ObjectId(result["_id"])})
    assert saved is not None
    assert saved["username"] == "size_boundary"
    assert saved["usernameKey"] == "size_boundary"
    assert isinstance(saved["passwordHash"], str) and saved["passwordHash"] != PASSWORD
    after = database_snapshot(db)
    expected_users = sorted([*request_size_baseline["users"], saved], key=lambda user: user["_id"])
    assert after == {**request_size_baseline, "users": expected_users}
    record_property("request_bytes", byte_count)
    record_property("http_status", response.status_code)
    record_property("database_result", "one user inserted; all existing documents unchanged")


def test_default_request_limit_rejects_declared_one_byte_over(
    api, db, request_size_baseline, record_property,
):
    byte_count = 12_582_913
    body = registration_body(byte_count)
    assert len(body) == byte_count
    request = api.build_request(
        "POST", "/api/auth/register", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert request.headers["Content-Length"] == str(byte_count)
    response = api.send(request)
    assert_api_error(response, 413, "REQUEST_TOO_LARGE")
    assert database_snapshot(db) == request_size_baseline
    record_property("request_bytes", byte_count)
    record_property("declared_bytes", byte_count)
    record_property("http_status", response.status_code)
    record_property("error_code", response.json()["error"]["code"])
    record_property("database_result", "full database snapshot unchanged")


@pytest.mark.parametrize("declared_length", [
    pytest.param("1", id="forged-small-content-length"),
    pytest.param(None, id="missing-content-length"),
])
def test_default_request_limit_measures_streamed_one_byte_over(
    api, db, request_size_baseline, record_property, declared_length,
):
    byte_count = 12_582_913
    body = registration_body(byte_count)
    assert len(body) == byte_count
    response, consumed_sizes = stream_registration(api, body, declared_length)
    assert_api_error(response, 413, "REQUEST_TOO_LARGE")
    # The first 12 chunks reach the legal limit; the 13th is the excess byte.
    assert consumed_sizes == [1024 * 1024] * 12 + [1]
    assert sum(consumed_sizes) == byte_count
    assert database_snapshot(db) == request_size_baseline
    record_property("request_bytes", byte_count)
    record_property("declared_bytes", declared_length or "absent")
    record_property("measured_chunks", len(consumed_sizes))
    record_property("measured_bytes", sum(consumed_sizes))
    record_property("http_status", response.status_code)
    record_property("error_code", response.json()["error"]["code"])
    record_property("database_result", "full database snapshot unchanged")
