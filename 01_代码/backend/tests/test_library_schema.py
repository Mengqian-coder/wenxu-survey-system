"""Direct BSON constraints for the question library, references and receipts.

These tests bypass the API. Referential integrity, version immutability,
authorization and the 256 KiB binary chunk limit are service responsibilities.
"""

from copy import deepcopy
from datetime import datetime, timezone
import uuid

from bson import Binary, ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, WriteError
import pytest

from app.bootstrap import initialize_database


NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
OWNER = ObjectId()
QUESTION = ObjectId()
VERSION = ObjectId()
BANK = ObjectId()
RECEIPT = ObjectId()
NEW_COLLECTIONS = (
    "questions", "question_versions", "question_shares", "question_banks",
    "question_bank_items", "mutation_receipts", "mutation_response_chunks",
)


@pytest.fixture
def library_db(replica_uri):
    client = MongoClient(replica_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    name = "test_library_schema_" + uuid.uuid4().hex
    try:
        database = client[name]
        initialize_database(database)
        yield database
    finally:
        try:
            client.drop_database(name)
        finally:
            client.close()


def content(kind="text"):
    result = {"type": kind, "title": "Reusable question", "description": "", "validation": {}}
    if kind in ("single", "multiple"):
        result["options"] = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
    if kind == "number":
        result["validation"] = {"integerOnly": False, "min": 0, "max": 10}
    return result


def documents():
    return {
        "questions": {
            "_id": QUESTION, "ownerId": OWNER, "latestVersionId": VERSION, "versionSeq": 1,
            "revision": 1, "guardRevision": 0, "origin": {"kind": "original"},
            "archivedAt": None, "createdAt": NOW, "updatedAt": NOW,
        },
        "question_versions": {
            "_id": VERSION, "questionId": QUESTION, "versionNo": 1, "baseVersionId": None,
            "operation": "create", "content": content(), "createdBy": OWNER,
            "createdAt": NOW, "archivedAt": None,
        },
        "question_shares": {
            "_id": ObjectId(), "questionId": QUESTION, "recipientId": ObjectId(), "grantedBy": OWNER,
            "grantedAt": NOW, "revokedAt": None, "revision": 1, "createdAt": NOW, "updatedAt": NOW,
        },
        "question_banks": {
            "_id": BANK, "ownerId": OWNER, "name": "Course bank", "description": "",
            "revision": 1, "guardRevision": 0, "createdAt": NOW, "updatedAt": NOW,
        },
        "question_bank_items": {
            "_id": ObjectId(), "bankId": BANK, "questionId": QUESTION, "createdAt": NOW,
        },
        "mutation_receipts": {
            "_id": RECEIPT, "actorId": OWNER, "key": "b8e90908-929f-45ff-b34a-0e3379968d1c",
            "operationId": "createQuestion", "target": "/api/questions", "canonicalVersion": 1,
            "fingerprint": "a" * 64, "status": "succeeded", "resultRefs": {"questionId": QUESTION},
            "httpStatus": 201, "response": {
                "mode": "inline", "byteLength": 2, "sha256": "b" * 64, "data": b"{}",
            }, "createdAt": NOW, "completedAt": NOW,
        },
        "mutation_response_chunks": {
            "_id": ObjectId(), "receiptId": RECEIPT, "index": 0, "data": b"{}",
        },
    }


def survey(kind="text", schema_version=2):
    question = {"id": "local_question", **content(kind), "required": True, "jumpRules": []}
    if schema_version == 2:
        question.update(questionId=QUESTION, versionId=VERSION, versionNo=1)
    return {
        "_id": ObjectId(), "ownerId": OWNER, "publicId": uuid.uuid4().hex,
        "title": "Referenced survey", "description": "", "status": "draft",
        "settings": {"anonymous": True, "allowRepeat": False}, "deadlineAt": None,
        "questions": [question], "schemaVersion": schema_version, "revision": 1,
        "createdAt": NOW, "updatedAt": NOW, "publishedAt": None, "closedAt": None, "closeReason": None,
    }


def rejected(database, collection, candidate):
    before = list(database[collection].find())
    with pytest.raises(WriteError) as failure:
        database[collection].insert_one(candidate)
    assert failure.value.code == 121
    assert list(database[collection].find()) == before


@pytest.mark.parametrize("name", NEW_COLLECTIONS)
def test_library_bson_records_and_strict_validators(library_db, name):
    candidate = documents()[name]
    library_db[name].insert_one(candidate)
    assert library_db[name].find_one({"_id": candidate["_id"]}) == candidate
    configured = library_db[name].options()
    assert configured["validationLevel"] == "strict"
    assert configured["validationAction"] == "error"
    extra = deepcopy(candidate)
    extra.update(_id=ObjectId(), unexpected="forbidden")
    rejected(library_db, name, extra)


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_and_reference_survey_branches_accept_all_question_types(library_db, kind, schema_version):
    candidate = survey(kind, schema_version)
    library_db.surveys.insert_one(candidate)
    assert library_db.surveys.find_one({"_id": candidate["_id"]}) == candidate


@pytest.mark.parametrize("field,value", [
    ("questionId", None), ("versionId", None), ("versionNo", None),
    ("questionId", str(QUESTION)), ("versionId", str(VERSION)),
    ("versionNo", 0), ("versionNo", True),
])
def test_v2_reference_fields_are_required_and_bson_typed(library_db, field, value):
    candidate = survey()
    if value is None:
        del candidate["questions"][0][field]
    else:
        candidate["questions"][0][field] = value
    rejected(library_db, "surveys", candidate)


def test_legacy_questions_cannot_silently_contain_library_references(library_db):
    candidate = survey()
    candidate["schemaVersion"] = 1
    rejected(library_db, "surveys", candidate)


@pytest.mark.parametrize("field", ["required", "jumpRules", "id", "questionId", "versionNo"])
def test_library_content_forbids_instance_and_reference_fields(library_db, field):
    candidate = documents()["question_versions"]
    candidate["content"][field] = "unexpected"
    rejected(library_db, "question_versions", candidate)


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
def test_library_content_supports_each_existing_question_type(library_db, kind):
    candidate = documents()["question_versions"]
    candidate["content"] = content(kind)
    library_db.question_versions.insert_one(candidate)


@pytest.mark.parametrize("name,field,value", [
    ("questions", "versionSeq", 0), ("questions", "guardRevision", -1),
    ("questions", "origin", {"kind": "copy", "questionId": QUESTION}),
    ("questions", "origin", {"kind": "legacy", "surveyId": QUESTION, "instanceId": "1invalid"}),
    ("questions", "origin", {"kind": "original", "questionId": QUESTION}),
    ("question_versions", "baseVersionId", str(VERSION)),
    ("question_versions", "operation", "overwrite"),
    ("question_shares", "revokedAt", "2026-10-22"),
    ("question_banks", "name", ""), ("question_bank_items", "bankId", str(BANK)),
    ("mutation_receipts", "key", "B8E90908-929F-45FF-B34A-0E3379968D1C"),
    ("mutation_receipts", "key", "b8e90908-929f-15ff-b34a-0e3379968d1c"),
    ("mutation_receipts", "resultRefs", {"questionId": 1}),
    ("mutation_receipts", "httpStatus", 500),
    ("mutation_response_chunks", "index", -1), ("mutation_response_chunks", "data", "{}"),
])
def test_library_and_receipt_structure_rejections(library_db, name, field, value):
    candidate = documents()[name]
    candidate[field] = value
    rejected(library_db, name, candidate)


@pytest.mark.parametrize("field", ["httpStatus", "response", "completedAt"])
def test_success_receipt_requires_status_response_and_completion(library_db, field):
    candidate = documents()["mutation_receipts"]
    del candidate[field]
    rejected(library_db, "mutation_receipts", candidate)


def test_processing_receipt_can_be_reserved_and_finalized_in_one_transaction(library_db):
    candidate = documents()["mutation_receipts"]
    processing = {key: value for key, value in candidate.items() if key not in {"httpStatus", "response", "completedAt"}}
    processing.update(status="processing", resultRefs={})
    with library_db.client.start_session() as session:
        with session.start_transaction():
            library_db.mutation_receipts.insert_one(processing, session=session)
            library_db.mutation_receipts.update_one({"_id": RECEIPT}, {"$set": {
                key: candidate[key] for key in ("status", "resultRefs", "httpStatus", "response", "completedAt")
            }}, session=session)
    assert library_db.mutation_receipts.find_one({"_id": RECEIPT}) == candidate


@pytest.mark.parametrize("response", [
    {"mode": "inline", "byteLength": 2, "sha256": "b" * 64},
    {"mode": "chunks", "byteLength": 2, "sha256": "b" * 64, "chunkCount": 0},
    {"mode": "chunks", "byteLength": 2, "sha256": "b" * 64, "chunkCount": 1, "data": Binary(b"{}")},
])
def test_receipt_storage_modes_cannot_be_mixed_or_incomplete(library_db, response):
    candidate = documents()["mutation_receipts"]
    candidate["response"] = response
    rejected(library_db, "mutation_receipts", candidate)


@pytest.mark.parametrize("name,index_name", [
    ("question_versions", "uq_question_versions_question_number"),
    ("question_shares", "uq_question_shares_question_recipient"),
    ("question_bank_items", "uq_question_bank_items_bank_question"),
    ("mutation_receipts", "uq_mutation_receipts_actor_key"),
    ("mutation_response_chunks", "uq_mutation_response_chunks_receipt_index"),
])
def test_library_composite_unique_indexes(library_db, name, index_name):
    candidate = documents()[name]
    library_db[name].insert_one(candidate)
    duplicate = deepcopy(candidate)
    duplicate["_id"] = ObjectId()
    with pytest.raises(DuplicateKeyError) as failure:
        library_db[name].insert_one(duplicate)
    assert index_name in failure.value.details["errmsg"]
    assert library_db[name].count_documents({}) == 1


def test_receipt_keys_are_global_per_actor_and_never_expire(library_db):
    candidate = documents()["mutation_receipts"]
    library_db.mutation_receipts.insert_one(candidate)
    duplicate = deepcopy(candidate)
    duplicate.update(_id=ObjectId(), operationId="restoreQuestion", target="/api/questions/another")
    with pytest.raises(DuplicateKeyError):
        library_db.mutation_receipts.insert_one(duplicate)
    duplicate.update(_id=ObjectId(), actorId=ObjectId())
    library_db.mutation_receipts.insert_one(duplicate)
    for name in ("mutation_receipts", "mutation_response_chunks"):
        assert all("expireAfterSeconds" not in index for index in library_db[name].list_indexes())


def test_legacy_origin_uniqueness_only_applies_to_explicit_migrations(library_db):
    candidate = documents()["questions"]
    library_db.questions.insert_one(candidate)
    other_original = deepcopy(candidate)
    other_original["_id"] = ObjectId()
    library_db.questions.insert_one(other_original)
    legacy = deepcopy(candidate)
    legacy.update(_id=ObjectId(), origin={"kind": "legacy", "surveyId": ObjectId(), "instanceId": "local_q"})
    library_db.questions.insert_one(legacy)
    duplicate = deepcopy(legacy)
    duplicate.update(_id=ObjectId(), ownerId=ObjectId())
    with pytest.raises(DuplicateKeyError) as failure:
        library_db.questions.insert_one(duplicate)
    assert "uq_questions_legacy_origin" in failure.value.details["errmsg"]
