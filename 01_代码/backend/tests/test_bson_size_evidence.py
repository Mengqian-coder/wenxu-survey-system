"""Measured BSON boundaries and the real pre-write rejection/rollback path.

The service-path checks intentionally expand an already validated internal
snapshot.  They exercise a defensive guard, not an ordinary HTTP input that
can bypass the smaller request/field/array limits.  The production size check
is always executed; neither its result nor the BSON encoder is stubbed.
"""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from bson import BSON, ObjectId
from pymongo.collection import Collection
import pytest

from app.errors import APIError
from app.question_service import QuestionService
import app.service as service_module
from test_api import register


BSON_LIMIT = 16 * 1024 * 1024


def sized_document(target_bytes):
    """Hit an actual encoded byte size, including BSON overhead and UTF-8."""
    document = {"_id": ObjectId(), "payload": ""}
    available = target_bytes - len(BSON.encode(document))
    unit = "测😀"
    repeats, remainder = divmod(available, len(unit.encode("utf-8")))
    document["payload"] = unit * repeats + "x" * remainder
    assert len(BSON.encode(document)) == target_bytes
    return document


def database_snapshot(db):
    """Compare every persisted document byte for byte, not only row counts."""
    return {
        name: [BSON.encode(doc) for doc in db[name].find({}).sort("_id", 1)]
        for name in sorted(db.list_collection_names())
    }


def snapshot_digest(snapshot):
    manifest = {
        name: [hashlib.sha256(document).hexdigest() for document in documents]
        for name, documents in snapshot.items()
    }
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["limit-minus-one", "exact-limit", "limit-plus-one"])
def test_real_bson_encoded_size_boundary(offset, record_property):
    document = sized_document(BSON_LIMIT + offset)
    measured = len(BSON.encode(document))
    record_property("scope", "real BSON encoding and production ensure_size; no Mongo write")
    record_property("limit_bytes", BSON_LIMIT)
    record_property("actual_bson_bytes", measured)
    record_property("offset_bytes", offset)

    if offset > 0:
        with pytest.raises(APIError) as rejected:
            service_module.ensure_size(document)
        assert (rejected.value.status, rejected.value.code) == (413, "DOCUMENT_TOO_LARGE")
        record_property("status", rejected.value.status)
        record_property("error_code", rejected.value.code)
    else:
        assert service_module.ensure_size(document) is None
        record_property("result", "accepted by size guard")


@pytest.mark.parametrize("operation", ["create", "patch"])
def test_oversized_final_survey_is_rejected_before_write_and_transaction_is_unchanged(
    api, db, monkeypatch, record_property, operation,
):
    """A small real HTTP request reaches an overgrown internal snapshot.

    A real question guard write and a processing receipt occur inside the
    transaction before the size error.  Both must disappear after rejection,
    and no insert/update of the final survey may even be attempted.
    """
    _, owner = register(api, "bson_guard_owner")

    def headers():
        return {**owner, "Idempotency-Key": str(uuid4())}

    created = api.post("/api/questions", headers=headers(), json={
        "content": {"type": "text", "title": "BSON guard source"},
    })
    assert created.status_code == 201, created.text
    source = created.json()
    reference = {
        "id": "local_original", "questionId": source["question"]["_id"],
        "versionId": source["version"]["_id"], "required": False, "jumpRules": [],
    }
    create_body = {
        "title": "BSON guard survey", "description": "",
        "settings": {"anonymous": True, "allowRepeat": False},
        "questions": [reference],
    }
    seeded = api.post("/api/surveys", headers=headers(), json=create_body)
    assert seeded.status_code == 201, seeded.text
    seed_id = ObjectId(seeded.json()["_id"])
    seed = db.surveys.find_one({"_id": seed_id})
    assert seed is not None

    # A different instance ID forces the genuine bind/_use guard-write path
    # for patch too, rather than merely reusing the already saved snapshot.
    incoming = {**reference, "id": "local_incoming"}
    request_body = {**create_body, "questions": [incoming]}
    if operation == "patch":
        request_body = {"revision": seed["revision"], "questions": [incoming]}

    actual_ensure_size = service_module.ensure_size
    original_bind = QuestionService.bind
    observed_sizes = []
    write_events = []

    def expand_validated_snapshot(self, references, user, existing=(), session=None):
        snapshots = original_bind(self, references, user, existing, session)
        candidate = deepcopy(seed)
        candidate["questions"] = deepcopy(snapshots)
        if operation == "patch":
            candidate["revision"] += 1
        # All generated scalar types/lengths match the seeded real document.
        # The observation below independently verifies the actual service doc.
        padding = BSON_LIMIT + 1 - len(BSON.encode(candidate))
        assert padding > 0
        snapshots[0]["description"] += "x" * padding
        candidate["questions"] = snapshots
        assert len(BSON.encode(candidate)) == BSON_LIMIT + 1
        return snapshots

    def observe_actual_guard(document):
        # This spy never accepts/rejects a document itself: the unmodified
        # production function below performs its own real BSON encoding.
        observed_sizes.append(len(BSON.encode(document)))
        return actual_ensure_size(document)

    original_insert = Collection.insert_one
    original_update = Collection.find_one_and_update

    def track_insert(collection, *args, **kwargs):
        if collection.database.name == db.name:
            write_events.append((collection.name, "insert_one"))
        return original_insert(collection, *args, **kwargs)

    def track_update(collection, *args, **kwargs):
        if collection.database.name == db.name:
            write_events.append((collection.name, "find_one_and_update"))
        return original_update(collection, *args, **kwargs)

    before = database_snapshot(db)
    monkeypatch.setattr(QuestionService, "bind", expand_validated_snapshot)
    monkeypatch.setattr(service_module, "ensure_size", observe_actual_guard)
    monkeypatch.setattr(Collection, "insert_one", track_insert)
    monkeypatch.setattr(Collection, "find_one_and_update", track_update)
    target = "/api/surveys" if operation == "create" else f"/api/surveys/{seed_id}"
    response = api.request("POST" if operation == "create" else "PATCH", target,
                           headers=headers(), json=request_body)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "DOCUMENT_TOO_LARGE"
    assert observed_sizes == [BSON_LIMIT + 1]
    assert ("mutation_receipts", "insert_one") in write_events
    assert ("questions", "find_one_and_update") in write_events
    assert not any(name == "surveys" for name, _ in write_events)
    after = database_snapshot(db)
    assert after == before

    record_property("scope", "fault-injected internal snapshot; real HTTP, guard and Mongo transaction")
    record_property("operation", operation)
    record_property("request_bytes", len(response.request.content))
    record_property("limit_bytes", BSON_LIMIT)
    record_property("actual_final_bson_bytes", observed_sizes[0])
    record_property("status", response.status_code)
    record_property("error_code", response.json()["error"]["code"])
    record_property("final_survey_write_attempts", 0)
    record_property("collection_count", len(before))
    record_property("document_counts", json.dumps({name: len(docs) for name, docs in before.items()}, sort_keys=True))
    record_property("database_before_sha256", snapshot_digest(before))
    record_property("database_after_sha256", snapshot_digest(after))
    record_property("all_persisted_documents_unchanged", True)
