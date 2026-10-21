"""Exercise MongoDB directly, with three explicit domain-versus-Schema contrasts.

Database assertions bypass the API and Pydantic. Only the contrast tests then
call the domain validator on exactly the data that MongoDB accepted. Only the
isolated replica_uri fixture is used; Schema is not claimed to enforce foreign
keys, anonymity, embedded ID uniqueness, min/max relationships, publication
rules, or question routing.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import uuid

from bson import BSON, Decimal128, Int64, ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, WriteError
import pytest

from app.bootstrap import initialize_database
from app.domain import DomainError, validate_questions


COLLECTIONS = ("users", "surveys", "responses", "participations")
NOW = datetime(2026, 10, 19, 2, 0, 0, 123000, tzinfo=timezone.utc)
USER_ID = ObjectId("000000000000000000000001")
OTHER_USER_ID = ObjectId("000000000000000000000002")
SURVEY_ID = ObjectId("000000000000000000000010")
OTHER_SURVEY_ID = ObjectId("000000000000000000000011")
REMOVE = object()


@pytest.fixture
def schema_db(replica_uri):
    """Never read .env or reuse the API fixture or a developer database."""
    client = MongoClient(replica_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    name = "test_schema_" + uuid.uuid4().hex
    try:
        database = client[name]
        initialize_database(database)
        yield database
    finally:
        try:
            client.drop_database(name)
        finally:
            client.close()


def text_questions(count):
    return [{
        "id": f"q{index}", "type": "text", "title": f"Question {index}",
        "description": "", "required": False, "validation": {}, "jumpRules": [],
    } for index in range(count)]


def options(count):
    return [{"id": f"opt{index}", "label": f"Option {index}"} for index in range(count)]


def rules(count):
    return [{
        "id": f"rule{index}", "condition": {"operator": "eq", "value": "yes"},
        "target": {"kind": "end"},
    } for index in range(count)]


def answers(count):
    return [{"questionId": f"q{index}", "value": "Answer"} for index in range(count)]


def baseline_documents():
    """Independent hand-written BSON fixtures, not generated from the Schema."""
    return {
        "users": {
            "_id": USER_ID, "username": "Alice", "usernameKey": "alice",
            "passwordHash": "opaque-hash-for-schema-tests-only",
            "createdAt": NOW, "updatedAt": NOW,
        },
        "surveys": {
            "_id": SURVEY_ID, "ownerId": USER_ID, "publicId": "a" * 32,
            "title": "Course survey", "description": "Database layer sample",
            "status": "published", "settings": {"anonymous": True, "allowRepeat": False},
            "deadlineAt": None,
            "questions": [
                {
                    "id": "q_single", "type": "single", "title": "Attended?",
                    "description": "", "required": True, "validation": {},
                    "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
                    "jumpRules": [{
                        "id": "skip", "condition": {"operator": "eq", "value": "no"},
                        "target": {"kind": "question", "questionId": "q_number"},
                    }],
                },
                {
                    "id": "q_multiple", "type": "multiple", "title": "Useful parts",
                    "description": "", "required": True,
                    "validation": {"minSelections": 1, "maxSelections": 2},
                    "options": [{"id": "theory", "label": "Theory"}, {"id": "lab", "label": "Lab"}],
                    "jumpRules": [{
                        "id": "lab_rule", "condition": {"operator": "containsAny", "values": ["lab"]},
                        "target": {"kind": "question", "questionId": "q_number"},
                    }],
                },
                {
                    "id": "q_number", "type": "number", "title": "Weekly hours",
                    "description": "", "required": True,
                    "validation": {"integerOnly": False, "min": 0, "max": 168},
                    "jumpRules": [{
                        "id": "number_rule", "condition": {"operator": "gt", "value": 100},
                        "target": {"kind": "end"},
                    }],
                },
                {
                    "id": "q_text", "type": "text", "title": "Suggestions",
                    "description": "", "required": False,
                    "validation": {"maxLength": 1000}, "jumpRules": [],
                },
            ],
            "schemaVersion": 1, "revision": 2, "createdAt": NOW, "updatedAt": NOW,
            "publishedAt": NOW, "closedAt": None, "closeReason": None,
        },
        "responses": {
            "_id": ObjectId("000000000000000000000020"), "surveyId": SURVEY_ID,
            "submissionKey": "a" * 64,
            "answers": [
                {"questionId": "q_single", "value": "yes"},
                {"questionId": "q_multiple", "value": ["lab"]},
                {"questionId": "q_number", "value": 6.5},
                {"questionId": "q_text", "value": None},
            ],
            "submittedAt": NOW, "schemaVersion": 1,
        },
        "participations": {
            "_id": ObjectId("000000000000000000000030"),
            "surveyId": SURVEY_ID, "userId": USER_ID,
        },
    }


def seed_baselines(database):
    documents = baseline_documents()
    for name, document in documents.items():
        database[name].insert_one(deepcopy(document))
    return documents


def database_snapshot(database):
    # Read back persisted BSON first: UTC/date precision and BSON integer types
    # are compared as actually stored, rather than against Python input values.
    return {
        name: tuple(BSON.encode(document) for document in database[name].find().sort("_id", 1))
        for name in COLLECTIONS
    }


def metadata_snapshot(database):
    return {
        name: {
            "options": database[name].options(),
            "indexes": sorted((dict(index) for index in database[name].list_indexes()), key=lambda item: item["name"]),
        }
        for name in COLLECTIONS
    }


def candidate_document(collection):
    """Keep control records and avoid their unrelated unique-key collisions."""
    document = baseline_documents()[collection]
    document["_id"] = ObjectId()
    if collection == "users":
        document.update(username="Candidate", usernameKey="candidate")
    elif collection == "surveys":
        document["publicId"] = "c" * 32
    elif collection == "responses":
        document["submissionKey"] = "c" * 64
    else:
        document["userId"] = OTHER_USER_ID
    return document


def patch_path(document, path, value):
    parent = document
    for part in path[:-1]:
        parent = parent[part]
    if value is REMOVE:
        del parent[path[-1]]
    else:
        parent[path[-1]] = deepcopy(value)


def assert_schema_rejection(database, collection, document):
    before = database_snapshot(database)
    with pytest.raises(WriteError) as failure:
        database[collection].insert_one(document)
    assert failure.value.code == 121
    assert database_snapshot(database) == before


@pytest.mark.parametrize("collection", COLLECTIONS, ids=COLLECTIONS)
def test_handwritten_bson_baselines_are_accepted(schema_db, collection):
    document = baseline_documents()[collection]
    result = schema_db[collection].insert_one(document)
    assert result.inserted_id == document["_id"]
    stored = schema_db[collection].find_one({"_id": document["_id"]})
    assert stored == document
    assert schema_db[collection].count_documents({}) == 1


# insert_one supplies _id automatically; omission is therefore not a valid
# rejection test for that field. Its BSON type is tested separately below.
REQUIRED_FIELDS = {
    "users": ("username", "usernameKey", "passwordHash", "createdAt", "updatedAt"),
    "surveys": (
        "ownerId", "publicId", "title", "description", "status", "settings", "deadlineAt", "questions",
        "schemaVersion", "revision", "createdAt", "updatedAt", "publishedAt", "closedAt", "closeReason",
    ),
    "responses": ("surveyId", "submissionKey", "answers", "submittedAt", "schemaVersion"),
    "participations": ("surveyId", "userId"),
}


@pytest.mark.parametrize("collection,field", [
    pytest.param(collection, field, id=f"missing-{collection}-{field}")
    for collection, fields in REQUIRED_FIELDS.items() for field in fields
])
def test_top_level_required_fields_are_enforced(schema_db, collection, field):
    seed_baselines(schema_db)
    document = candidate_document(collection)
    del document[field]
    assert_schema_rejection(schema_db, collection, document)


@pytest.mark.parametrize("collection,path,value", [
    *[pytest.param(name, ("_id",), "not-an-object-id", id=f"{name}-id-must-be-objectid") for name in COLLECTIONS],
    pytest.param("users", ("username",), 123, id="username-must-be-string"),
    pytest.param("users", ("createdAt",), "2026-10-19T02:00:00Z", id="registration-date-not-string"),
    pytest.param("surveys", ("ownerId",), str(USER_ID), id="owner-reference-not-string"),
    pytest.param("surveys", ("questions",), {}, id="questions-must-be-array"),
    pytest.param("surveys", ("revision",), True, id="revision-not-boolean"),
    pytest.param("surveys", ("status",), "open", id="survey-status-enum"),
    pytest.param("surveys", ("deadlineAt",), "2026-12-01T00:00:00Z", id="deadline-not-string"),
    pytest.param("surveys", ("publicId",), "A" * 32, id="public-id-lowercase-hex"),
    pytest.param("responses", ("surveyId",), str(SURVEY_ID), id="response-survey-reference-not-string"),
    pytest.param("responses", ("respondentId",), None, id="optional-respondent-cannot-be-null"),
    pytest.param("responses", ("answers",), {}, id="answers-must-be-array"),
    pytest.param("responses", ("submissionKey",), "A" * 64, id="submission-key-lowercase-hex"),
    pytest.param("responses", ("submissionKey",), "a" * 63, id="submission-key-exactly-64"),
    pytest.param("responses", ("schemaVersion",), 2, id="response-schema-version-enum"),
    pytest.param("participations", ("userId",), str(USER_ID), id="participation-user-reference-not-string"),
])
def test_top_level_types_enums_and_patterns(schema_db, collection, path, value):
    seed_baselines(schema_db)
    document = candidate_document(collection)
    patch_path(document, path, value)
    assert_schema_rejection(schema_db, collection, document)


@pytest.mark.parametrize("collection", COLLECTIONS, ids=COLLECTIONS)
def test_top_level_extra_fields_are_rejected(schema_db, collection):
    seed_baselines(schema_db)
    document = candidate_document(collection)
    document["unexpectedField"] = "not declared"
    assert_schema_rejection(schema_db, collection, document)


@pytest.mark.parametrize("collection,path,value", [
    pytest.param("surveys", ("settings", "anonymous"), REMOVE, id="settings-anonymous-required"),
    pytest.param("surveys", ("settings", "allowRepeat"), REMOVE, id="settings-repeat-required"),
    pytest.param("surveys", ("settings", "anonymous"), "true", id="settings-flag-not-string"),
    pytest.param("surveys", ("settings", "unexpected"), 1, id="settings-extra-field"),
    pytest.param("surveys", ("questions", 0, "id"), REMOVE, id="question-id-required"),
    pytest.param("surveys", ("questions", 0, "id"), "1invalid", id="question-id-pattern"),
    pytest.param("surveys", ("questions", 0, "type"), "rating", id="question-type-enum"),
    pytest.param("surveys", ("questions", 0, "required"), "true", id="question-required-flag-type"),
    pytest.param("surveys", ("questions", 0, "order"), 1, id="question-extra-order-field"),
    pytest.param("surveys", ("questions", 0, "options", 0, "label"), REMOVE, id="option-label-required"),
    pytest.param("surveys", ("questions", 0, "options", 0, "extra"), True, id="option-extra-field"),
    pytest.param("surveys", ("questions", 0, "validation", "minSelections"), 1, id="single-validation-must-be-empty"),
    pytest.param("surveys", ("questions", 1, "validation", "minSelections"), 1.5, id="multiple-count-must-be-integer"),
    pytest.param("surveys", ("questions", 2, "options"), options(2), id="number-forbids-options"),
    pytest.param("surveys", ("questions", 2, "validation", "integerOnly"), REMOVE, id="number-integer-mode-required"),
    pytest.param("surveys", ("questions", 2, "validation", "min"), None, id="number-unset-limit-not-null"),
    pytest.param("surveys", ("questions", 3, "jumpRules"), rules(1), id="text-forbids-jump-rules"),
    pytest.param("surveys", ("questions", 0, "jumpRules", 0, "condition"), REMOVE, id="rule-condition-required"),
    pytest.param("surveys", ("questions", 0, "jumpRules", 0, "extra"), "code", id="rule-extra-field"),
    pytest.param("surveys", ("questions", 0, "jumpRules", 0, "condition"), {"operator": "containsAll", "values": ["yes"]}, id="single-condition-operator-shape"),
    pytest.param("surveys", ("questions", 0, "jumpRules", 0, "target"), {"kind": "end", "questionId": "q_text"}, id="end-target-forbids-question-id"),
    pytest.param("surveys", ("questions", 0, "jumpRules", 0, "target"), {"kind": "question"}, id="question-target-id-required"),
    pytest.param("surveys", ("questions", 1, "jumpRules", 0, "condition", "values"), ["lab", "lab"], id="condition-option-array-no-duplicates"),
    pytest.param("surveys", ("questions", 1, "jumpRules", 0, "condition", "values"), [], id="condition-option-array-nonempty"),
    pytest.param("surveys", ("questions", 2, "jumpRules", 0, "condition"), {"operator": "between", "min": 0}, id="numeric-between-max-required"),
    pytest.param("responses", ("answers", 0, "value"), REMOVE, id="answer-value-required-even-if-null"),
    pytest.param("responses", ("answers", 0, "questionId"), REMOVE, id="answer-question-id-required"),
    pytest.param("responses", ("answers", 0, "extra"), 1, id="answer-extra-field"),
    pytest.param("responses", ("answers", 0, "value"), True, id="answer-value-not-boolean"),
    pytest.param("responses", ("answers", 0, "value"), {"answer": "yes"}, id="answer-value-not-object"),
    pytest.param("responses", ("answers", 1, "value"), ["lab", "lab"], id="answer-option-array-no-duplicates"),
])
def test_nested_structures_are_validated_by_mongodb(schema_db, collection, path, value):
    seed_baselines(schema_db)
    document = candidate_document(collection)
    patch_path(document, path, value)
    assert_schema_rejection(schema_db, collection, document)


@pytest.mark.parametrize("collection,path,value", [
    pytest.param("users", ("username",), "ab", id="username-below-three"),
    pytest.param("users", ("username",), "a" * 33, id="username-above-thirty-two"),
    pytest.param("users", ("usernameKey",), "a" * 129, id="username-key-above-128"),
    pytest.param("users", ("passwordHash",), "a" * 1025, id="hash-above-1024"),
    pytest.param("surveys", ("title",), "", id="survey-title-nonempty"),
    pytest.param("surveys", ("title",), "a" * 201, id="survey-title-above-200"),
    pytest.param("surveys", ("description",), "a" * 5001, id="survey-description-above-5000"),
    pytest.param("surveys", ("questions",), text_questions(101), id="question-count-above-100"),
    pytest.param("surveys", ("questions", 0, "title"), "a" * 2001, id="question-title-above-2000"),
    pytest.param("surveys", ("questions", 0, "id"), "q" + "a" * 64, id="stable-id-above-64"),
    pytest.param("surveys", ("questions", 0, "description"), "a" * 2001, id="question-description-above-2000"),
    pytest.param("surveys", ("questions", 0, "options"), options(1), id="choice-needs-two-options"),
    pytest.param("surveys", ("questions", 0, "options"), options(51), id="choice-options-above-50"),
    pytest.param("surveys", ("questions", 0, "options", 0, "label"), "a" * 501, id="option-label-above-500"),
    pytest.param("surveys", ("questions", 0, "jumpRules"), rules(21), id="jump-rules-above-20"),
    pytest.param("surveys", ("questions", 1, "validation", "maxSelections"), 51, id="multiple-limit-above-50"),
    pytest.param("surveys", ("questions", 1, "validation", "minSelections"), -1, id="multiple-limit-nonnegative"),
    pytest.param("surveys", ("questions", 3, "validation", "maxLength"), 10001, id="text-limit-above-10000"),
    pytest.param("surveys", ("questions", 3, "validation", "minLength"), -1, id="text-limit-nonnegative"),
    pytest.param("responses", ("answers",), [], id="saved-response-needs-one-answer"),
    pytest.param("responses", ("answers",), answers(101), id="answer-count-above-100"),
    pytest.param("responses", ("answers", 0, "value"), "", id="saved-answer-string-nonempty"),
    pytest.param("responses", ("answers", 0, "value"), "a" * 10001, id="answer-text-above-10000"),
    pytest.param("responses", ("answers", 1, "value"), [], id="saved-choice-array-nonempty"),
    pytest.param("responses", ("answers", 1, "value"), [f"opt{i}" for i in range(51)], id="answer-options-above-50"),
])
def test_schema_capacity_limits_reject_outside_values(schema_db, collection, path, value):
    seed_baselines(schema_db)
    document = candidate_document(collection)
    patch_path(document, path, value)
    assert_schema_rejection(schema_db, collection, document)


@pytest.mark.parametrize("collection,path,value", [
    pytest.param("users", ("username",), "a" * 32, id="username-at-32"),
    pytest.param("users", ("usernameKey",), "a" * 128, id="username-key-at-128"),
    pytest.param("users", ("passwordHash",), "a" * 1024, id="hash-at-1024"),
    pytest.param("surveys", ("title",), "\U0001f600" * 200, id="title-at-200-unicode-codepoints"),
    pytest.param("surveys", ("description",), "a" * 5000, id="description-at-5000"),
    pytest.param("surveys", ("questions",), [], id="schema-allows-empty-draft-definition"),
    pytest.param("surveys", ("questions",), text_questions(100), id="question-count-at-100"),
    pytest.param("surveys", ("questions", 0, "title"), "a" * 2000, id="question-title-at-2000"),
    pytest.param("surveys", ("questions", 0, "id"), "q" + "a" * 63, id="stable-id-at-64"),
    pytest.param("surveys", ("questions", 0, "options"), options(50), id="choice-options-at-50"),
    pytest.param("surveys", ("questions", 0, "options", 0, "label"), "a" * 500, id="option-label-at-500"),
    pytest.param("surveys", ("questions", 0, "jumpRules"), rules(20), id="jump-rules-at-20"),
    pytest.param("responses", ("answers",), answers(100), id="answer-count-at-100"),
    pytest.param("responses", ("answers", 0, "value"), "a" * 10000, id="answer-text-at-10000"),
    pytest.param("responses", ("answers", 1, "value"), [f"opt{i}" for i in range(50)], id="answer-options-at-50"),
    pytest.param("responses", ("answers", 2, "value"), Int64(2**40), id="numeric-answer-bson-int64"),
    pytest.param("responses", ("answers", 2, "value"), Decimal128("1.25"), id="numeric-answer-bson-decimal"),
    pytest.param("responses", ("respondentId",), USER_ID, id="optional-respondent-bson-objectid"),
])
def test_schema_accepts_legal_boundaries_and_bson_types(schema_db, collection, path, value):
    # These assert Schema boundaries only; no cross-document/question business
    # validity is inferred from successful direct writes.
    document = baseline_documents()[collection]
    patch_path(document, path, value)
    if collection == "surveys" and path == ("questions",) and value == []:
        document.update(status="draft", publishedAt=None)
    schema_db[collection].insert_one(document)
    stored = schema_db[collection].find_one({"_id": document["_id"]})
    assert stored == document


@pytest.mark.parametrize("collection,index_name", [
    pytest.param("users", "uq_users_usernameKey", id="username-key-unique"),
    pytest.param("surveys", "uq_surveys_publicId", id="public-id-unique"),
    pytest.param("responses", "uq_responses_survey_submission", id="survey-submission-key-unique"),
    pytest.param("participations", "uq_participations_survey_user", id="survey-user-unique"),
])
def test_business_unique_indexes_reject_duplicates_without_writes(schema_db, collection, index_name):
    documents = seed_baselines(schema_db)
    duplicate = deepcopy(documents[collection])
    duplicate["_id"] = ObjectId()
    before = database_snapshot(schema_db)
    with pytest.raises(DuplicateKeyError) as failure:
        schema_db[collection].insert_one(duplicate)
    assert failure.value.code == 11000
    assert index_name in failure.value.details.get("errmsg", "")
    assert database_snapshot(schema_db) == before


@pytest.mark.parametrize("collection,index_name,key_fields", [
    pytest.param("users", "uq_users_usernameKey", ("usernameKey",), id="update-username-key-collision"),
    pytest.param("surveys", "uq_surveys_publicId", ("publicId",), id="update-public-id-collision"),
    pytest.param("responses", "uq_responses_survey_submission", ("surveyId", "submissionKey"), id="update-submission-key-collision"),
    pytest.param("participations", "uq_participations_survey_user", ("surveyId", "userId"), id="update-survey-user-collision"),
])
def test_unique_index_updates_leave_both_documents_unchanged(schema_db, collection, index_name, key_fields):
    first = seed_baselines(schema_db)[collection]
    second = candidate_document(collection)
    schema_db[collection].insert_one(second)
    assert schema_db[collection].count_documents({}) == 2
    before = database_snapshot(schema_db)
    with pytest.raises(DuplicateKeyError) as failure:
        schema_db[collection].update_one(
            {"_id": second["_id"]}, {"$set": {field: first[field] for field in key_fields}},
        )
    assert failure.value.code == 11000
    assert index_name in failure.value.details.get("errmsg", "")
    assert database_snapshot(schema_db) == before


@pytest.mark.parametrize("path,value,message", [
    pytest.param(
        ("questions", 0, "jumpRules", 0, "target"), {"kind": "question", "questionId": "q_single"},
        "跳转目标必须", id="same-question-jump-schema-only",
    ),
    pytest.param(
        ("questions", 1, "id"), "q_single", "题目 ID 不能重复",
        id="duplicate-question-id-schema-only",
    ),
    pytest.param(
        ("questions", 2, "validation", "min"), 169, "最小值不能大于最大值",
        id="numeric-inverted-range-schema-only",
    ),
])
def test_schema_acceptance_does_not_replace_cross_field_business_rules(schema_db, path, value, message):
    """The same question data passes BSON structure but fails domain rules."""
    document = baseline_documents()["surveys"]
    patch_path(document, path, value)
    schema_db.surveys.insert_one(document)
    stored = schema_db.surveys.find_one({"_id": document["_id"]})
    assert stored["questions"] == document["questions"]
    before = database_snapshot(schema_db)
    with pytest.raises(DomainError, match=message):
        validate_questions(deepcopy(stored["questions"]), publish=True)
    assert database_snapshot(schema_db) == before


@pytest.mark.parametrize("collection,field,value", [
    pytest.param("responses", "surveyId", OTHER_SURVEY_ID, id="same-key-different-survey-allowed"),
    pytest.param("responses", "submissionKey", "b" * 64, id="same-survey-different-key-allowed"),
    pytest.param("participations", "surveyId", OTHER_SURVEY_ID, id="same-user-different-survey-allowed"),
    pytest.param("participations", "userId", OTHER_USER_ID, id="same-survey-different-user-allowed"),
])
def test_composite_unique_indexes_use_the_complete_key(schema_db, collection, field, value):
    documents = seed_baselines(schema_db)
    other_user = deepcopy(documents["users"])
    other_user.update(_id=OTHER_USER_ID, username="Bob", usernameKey="bob")
    schema_db.users.insert_one(other_user)
    other_survey = deepcopy(documents["surveys"])
    other_survey.update(_id=OTHER_SURVEY_ID, publicId="b" * 32)
    schema_db.surveys.insert_one(other_survey)
    document = deepcopy(documents[collection])
    document.update(_id=ObjectId(), **{field: value})
    schema_db[collection].insert_one(document)
    assert schema_db[collection].count_documents({}) == 2


EXPECTED_INDEXES = {
    "users": {
        "_id_": ([("_id", 1)], True),
        "uq_users_usernameKey": ([("usernameKey", 1)], True),
    },
    "surveys": {
        "_id_": ([("_id", 1)], True),
        "uq_surveys_publicId": ([("publicId", 1)], True),
        "ix_surveys_owner_created": ([("ownerId", 1), ("createdAt", -1), ("_id", -1)], False),
    },
    "responses": {
        "_id_": ([("_id", 1)], True),
        "ix_responses_survey_submitted": ([("surveyId", 1), ("submittedAt", -1), ("_id", -1)], False),
        "uq_responses_survey_submission": ([("surveyId", 1), ("submissionKey", 1)], True),
    },
    "participations": {
        "_id_": ([("_id", 1)], True),
        "uq_participations_survey_user": ([("surveyId", 1), ("userId", 1)], True),
    },
}


def test_index_names_keys_order_and_uniqueness_match_the_contract(schema_db):
    assert set(schema_db.list_collection_names()) == set(COLLECTIONS)
    for name, expected in EXPECTED_INDEXES.items():
        actual = {index["name"]: index for index in schema_db[name].list_indexes()}
        assert set(actual) == set(expected)
        for index_name, (keys, unique) in expected.items():
            assert list(actual[index_name]["key"].items()) == keys
            # MongoDB may omit the implicit unique flag on its default _id index.
            assert bool(actual[index_name].get("unique", index_name == "_id_")) is unique


def test_each_collection_has_a_strict_error_validator(schema_db):
    for name in COLLECTIONS:
        configured = schema_db[name].options()
        assert configured["validationLevel"] == "strict"
        assert configured["validationAction"] == "error"
        schema = configured["validator"]["$jsonSchema"]
        assert schema["bsonType"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"_id", *REQUIRED_FIELDS[name]}


@pytest.mark.parametrize("collection,update", [
    pytest.param("users", {"$set": {"username": 123}}, id="invalid-user-update"),
    pytest.param("surveys", {"$set": {"settings.allowRepeat": "false"}}, id="invalid-survey-nested-update"),
    pytest.param("surveys", {"$unset": {"ownerId": ""}}, id="cannot-unset-required-owner"),
    pytest.param("responses", {"$set": {"answers.0.value": True}}, id="invalid-response-answer-update"),
    pytest.param("participations", {"$set": {"unexpectedField": 1}}, id="invalid-participation-update"),
])
def test_invalid_updates_leave_all_persisted_documents_unchanged(schema_db, collection, update):
    documents = seed_baselines(schema_db)
    before = database_snapshot(schema_db)
    with pytest.raises(WriteError) as failure:
        schema_db[collection].update_one({"_id": documents[collection]["_id"]}, update)
    assert failure.value.code == 121
    assert database_snapshot(schema_db) == before


def test_repeated_bootstrap_preserves_data_indexes_and_validators(schema_db):
    seed_baselines(schema_db)
    before_data = database_snapshot(schema_db)
    before_metadata = metadata_snapshot(schema_db)
    initialize_database(schema_db)
    initialize_database(schema_db)
    assert database_snapshot(schema_db) == before_data
    assert metadata_snapshot(schema_db) == before_metadata
    document = candidate_document("users")
    document["unexpectedField"] = True
    assert_schema_rejection(schema_db, "users", document)
