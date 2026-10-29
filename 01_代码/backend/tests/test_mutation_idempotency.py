"""Durable mutation replay and failure boundaries against real MongoDB."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from bson import Binary, ObjectId
from pydantic import TypeAdapter
from pymongo.client_session import ClientSession
from pymongo.errors import AutoReconnect, OperationFailure
import pytest

from app.errors import APIError
from app.idempotency import CHUNK_BYTES, MutationError, MutationRunner
from app.models import QuestionCreate
from app.views import (PublicSurvey, QuestionPage, QuestionVersionResult, QuestionView,
                       SurveyPage, SurveyView, VersionPage, VersionResult)
from test_api import assert_api_error, database_snapshot


def account(api, name="receipt-owner"):
    body = {"username": name, "password": "correct-horse-battery-123!"}
    assert api.post("/api/auth/register", json=body).status_code == 201
    login = api.post("/api/auth/login", json=body)
    assert login.status_code == 200, login.text
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def key_headers(headers, key=None):
    return {**headers, "Idempotency-Key": key or str(uuid4())}


def create(api, headers, title="版本一"):
    response = api.post("/api/questions", headers=key_headers(headers), json={"content": {"type": "text", "title": title}})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("key", [None, "not-a-uuid", "89F2D440-5D6E-4D77-A2C3-8A12D3974809", "89f2d440-5d6e-1d77-a2c3-8a12d3974809"])
def test_management_requires_lowercase_uuid_v4(api, db, key):
    headers = account(api)
    if key is not None:
        headers["Idempotency-Key"] = key
    before = database_snapshot(db)
    response = api.post("/api/questions", headers=headers, json={"content": {"type": "text", "title": "题目"}})
    assert_api_error(response, 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before
    assert db.questions.count_documents({}) == db.mutation_receipts.count_documents({}) == 0


def test_replay_is_original_response_before_current_revision_and_archive(api, db):
    headers = account(api)
    request_headers = key_headers(headers)
    first = api.post("/api/questions", headers=request_headers, json={"content": {"type": "text", "title": "  原始题目  "}})
    assert first.status_code == 201, first.text
    assert first.headers["idempotency-replayed"] == "false"
    saved = first.json()
    archived = api.patch(f"/api/questions/{saved['question']['_id']}", headers=key_headers(headers), json={"revision": saved["question"]["revision"], "archived": True})
    assert archived.status_code == 200, archived.text
    # Defaulted and trimmed DTO values, rather than raw JSON key order, identify
    # the same request. Do not re-read the now archived resource on replay.
    replay = api.post("/api/questions", headers=request_headers, json={"content": {"validation": {}, "description": "", "title": "原始题目", "type": "text"}})
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.content == first.content
    assert db.questions.count_documents({}) == 1
    assert db.question_versions.count_documents({}) == 1
    before = database_snapshot(db)
    different = api.post("/api/questions", headers=request_headers, json={"content": {"type": "text", "title": "不同题目"}})
    assert_api_error(different, 409, "IDEMPOTENCY_KEY_REUSED")
    assert database_snapshot(db) == before
    other_operation = api.post(f"/api/questions/{saved['question']['_id']}/copies", headers=request_headers, json={"versionId": saved["version"]["_id"]})
    assert_api_error(other_operation, 409, "IDEMPOTENCY_KEY_REUSED")
    assert database_snapshot(db) == before


def test_restore_replay_does_not_create_another_version(api, db):
    headers = account(api)
    original = create(api, headers)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    edited = api.post(f"/api/questions/{qid}/versions", headers=key_headers(headers), json={
        "revision": original["question"]["revision"], "baseVersionId": vid,
        "content": {"type": "text", "title": "版本二"},
    })
    assert edited.status_code == 201, edited.text
    body = {"revision": edited.json()["question"]["revision"], "sourceVersionId": vid}
    same_headers = key_headers(headers)
    restored = api.post(f"/api/questions/{qid}/restore", headers=same_headers, json=body)
    assert restored.status_code == 201, restored.text
    assert restored.json()["version"]["versionNo"] == 3
    replay = api.post(f"/api/questions/{qid}/restore", headers=same_headers, json=body)
    assert replay.status_code == 201 and replay.content == restored.content, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert db.question_versions.count_documents({}) == 3
    before = database_snapshot(db)
    changed = api.post(f"/api/questions/{qid}/restore", headers=same_headers, json={**body, "revision": restored.json()["question"]["revision"]})
    assert_api_error(changed, 409, "IDEMPOTENCY_KEY_REUSED")
    assert database_snapshot(db) == before
    failed_headers = key_headers(headers)
    stale = api.post(f"/api/questions/{qid}/restore", headers=failed_headers, json=body)
    assert_api_error(stale, 409, "REVISION_CONFLICT")
    assert database_snapshot(db) == before
    assert db.mutation_receipts.count_documents({"key": failed_headers["Idempotency-Key"]}) == 0


def test_same_key_concurrency_and_actor_scope(api, db):
    headers = account(api)
    request_headers = key_headers(headers)
    body = {"content": {"type": "text", "title": "并发只建一次"}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: api.post("/api/questions", headers=request_headers, json=body), range(4)))
    completed = [response for response in responses if response.status_code == 201]
    assert completed, [response.text for response in responses]
    for response in responses:
        assert response.status_code in (201, 409), response.text
        if response.status_code == 201:
            assert response.content == completed[0].content
        else:
            assert response.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
            assert response.headers["retry-after"] == "1"
    replay = api.post("/api/questions", headers=request_headers, json=body)
    assert replay.status_code == 201 and replay.content == completed[0].content
    assert db.questions.count_documents({}) == db.mutation_receipts.count_documents({}) == 1
    second_actor = account(api, "another-owner")
    separate = api.post("/api/questions", headers=key_headers(second_actor, request_headers["Idempotency-Key"]), json=body)
    assert separate.status_code == 201, separate.text
    assert separate.json()["question"]["_id"] != completed[0].json()["question"]["_id"]


def infrastructure(api, db):
    account(api)
    actor = db.users.find_one({})
    runner = MutationRunner(db)
    body = QuestionCreate.model_validate({"content": {"type": "text", "title": "事务题目"}})
    return actor, runner, body


def test_large_response_chunks_are_exact_and_corruption_never_reexecutes(api, db, monkeypatch):
    actor, runner, body = infrastructure(api, db)
    calls = []

    def callback(session):
        calls.append(True)
        result = api.app.state.questions.create(body, actor, session=session)
        return 201, {**result, "padding": "大响应" * CHUNK_BYTES}

    key = str(uuid4())
    first = runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    replay = runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    assert replay.body == first.body and replay.headers["idempotency-replayed"] == "true"
    receipt = db.mutation_receipts.find_one({"key": key})
    assert receipt["response"]["mode"] == "chunks"
    chunks = list(db.mutation_response_chunks.find({"receiptId": receipt["_id"]}))
    assert len(chunks) == receipt["response"]["chunkCount"] > 1
    assert all(0 < len(chunk["data"]) <= CHUNK_BYTES for chunk in chunks)
    assert len(calls) == 1
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs):
            raise AutoReconnect("response storage temporarily unavailable")

        patch.setattr(runner.chunks, "find", unavailable)
        with pytest.raises(MutationError) as unreadable:
            runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
        assert unreadable.value.code == "IDEMPOTENCY_RESULT_UNAVAILABLE"
        assert unreadable.value.status == 503 and len(calls) == 1
    db.mutation_response_chunks.update_one({"_id": chunks[0]["_id"]}, {"$set": {"data": Binary(b"damaged")}})
    with pytest.raises(MutationError) as error:
        runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    assert error.value.code == "IDEMPOTENCY_RESULT_UNAVAILABLE"
    assert len(calls) == db.questions.count_documents({}) == 1


def test_abort_after_receipt_and_chunks_rolls_back_everything(api, db, monkeypatch):
    actor, runner, body = infrastructure(api, db)
    original_save = runner._save_response

    def fail_after_save(*args, **kwargs):
        original_save(*args, **kwargs)
        raise APIError(409, "INJECTED_ROLLBACK", "回滚测试")

    monkeypatch.setattr(runner, "_save_response", fail_after_save)

    def callback(session):
        result = api.app.state.questions.create(body, actor, session=session)
        return 201, {**result, "padding": "响应" * CHUNK_BYTES}

    key = str(uuid4())
    with pytest.raises(APIError) as error:
        runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    assert error.value.code == "INJECTED_ROLLBACK"
    for collection in ("questions", "question_versions", "mutation_receipts", "mutation_response_chunks"):
        assert db[collection].count_documents({}) == 0, collection
    # A confirmed rollback does not burn the key.
    monkeypatch.setattr(runner, "_save_response", original_save)
    assert runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback).status_code == 201
    assert db.questions.count_documents({}) == 1


def test_commit_succeeded_but_confirmation_lost_keeps_key_and_never_reexecutes(api, db, monkeypatch):
    actor, runner, body = infrastructure(api, db)
    original_commit = ClientSession.commit_transaction
    original_lookup = runner._lookup
    committed, calls = [], []

    def lose_confirmation(session):
        if not committed:
            original_commit(session)
            committed.append(True)
        raise OperationFailure("lost commit confirmation", code=64, details={"errorLabels": ["UnknownTransactionCommitResult"]})

    def temporarily_missing(*args):
        return None if committed else original_lookup(*args)

    def callback(session):
        calls.append(True)
        return 201, api.app.state.questions.create(body, actor, session=session)

    monkeypatch.setattr(ClientSession, "commit_transaction", lose_confirmation)
    monkeypatch.setattr(runner, "_lookup", temporarily_missing)
    key = str(uuid4())
    with pytest.raises(MutationError) as error:
        runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    assert error.value.code == "IDEMPOTENCY_RESULT_UNKNOWN"
    assert len(calls) == db.questions.count_documents({}) == db.mutation_receipts.count_documents({}) == 1
    monkeypatch.setattr(ClientSession, "commit_transaction", original_commit)
    monkeypatch.setattr(runner, "_lookup", original_lookup)
    confirmed = runner.run(actor, key, "question.create", "/api/questions", body.model_dump(), callback)
    assert confirmed.status_code == 201 and confirmed.headers["idempotency-replayed"] == "true"
    assert len(calls) == 1


def test_cors_allows_management_protocol(api):
    from fastapi.testclient import TestClient
    from app.main import create_app

    origin = "https://management.example.test"
    settings = api.app.state.settings.model_copy(update={"cors_origins": [origin]})
    with TestClient(create_app(settings)) as configured:
        response = configured.options("/api/questions", headers={
            "Origin": origin, "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "Authorization,Idempotency-Key,Content-Type",
        })
        assert response.status_code == 200, response.text
        assert "DELETE" in response.headers["access-control-allow-methods"]
        assert "Idempotency-Key" in response.headers["access-control-allow-headers"]
        exposed = configured.get("/health", headers={"Origin": origin})
        assert "Idempotency-Replayed" in exposed.headers["access-control-expose-headers"]
        assert "Retry-After" in exposed.headers["access-control-expose-headers"]


def test_patch_fingerprint_keeps_top_level_presence_and_nested_defaults(api, db):
    headers = account(api)
    question = create(api, headers)
    # Response models also describe the raw JSONResponse-backed writes.
    QuestionVersionResult.model_validate(question)
    created = api.post("/api/surveys", headers=key_headers(headers), json={"title": "规范化草稿", "settings": {"anonymous": True}})
    assert created.status_code == 201, created.text
    survey = created.json()
    path = f"/api/surveys/{survey['_id']}"
    first_headers = key_headers(headers)
    first = api.patch(path, headers=first_headers, json={"revision": 1, "settings": {"anonymous": False}})
    assert first.status_code == 200, first.text
    defaults = api.patch(path, headers=first_headers, json={"revision": 1, "settings": {"anonymous": False, "allowRepeat": False}})
    assert defaults.status_code == 200 and defaults.content == first.content, defaults.text
    assert defaults.headers["idempotency-replayed"] == "true"
    before = database_snapshot(db)
    different = api.patch(path, headers=first_headers, json={"revision": 1, "settings": {"anonymous": False}, "description": ""})
    assert_api_error(different, 409, "IDEMPOTENCY_KEY_REUSED")
    assert database_snapshot(db) == before
    refs_headers = key_headers(headers)
    reference = {"id": "caseSensitiveLocal", "questionId": question["question"]["_id"].upper(), "versionId": question["version"]["_id"].upper()}
    pinned = api.patch(path, headers=refs_headers, json={"revision": first.json()["revision"], "questions": [reference]})
    assert pinned.status_code == 200, pinned.text
    explicit = {**reference, "questionId": reference["questionId"].lower(), "versionId": reference["versionId"].lower(), "required": True, "jumpRules": []}
    replay = api.patch(path, headers=refs_headers, json={"revision": first.json()["revision"], "questions": [explicit]})
    assert replay.status_code == 200 and replay.content == pinned.content, replay.text
    assert replay.headers["idempotency-replayed"] == "true"


def test_openapi_declares_concrete_new_responses_and_replay_headers(api):
    document = api.get("/openapi.json").json()
    for path, methods in document["paths"].items():
        if not path.startswith("/api/questions") and path not in {
            "/api/surveys", "/api/surveys/{survey_id}",
            "/api/surveys/{survey_id}/publish", "/api/surveys/{survey_id}/close",
            "/api/public/surveys/{public_id}",
        }:
            continue
        for method, operation in methods.items():
            success = operation["responses"].get("201") or operation["responses"]["200"]
            assert success["content"]["application/json"]["schema"], (path, method)
            assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
            if method != "get":
                assert "Idempotency-Replayed" in success["headers"]
                parameter = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
                assert parameter["required"] is True and parameter["in"] == "header"
    version_schema = document["components"]["schemas"]["QuestionVersion"]
    permission_ref = version_schema["properties"]["permissions"]["$ref"].split("/")[-1]
    assert set(document["components"]["schemas"][permission_ref]["properties"]) == {"canUse"}


def test_actual_management_and_public_json_matches_typed_contracts(api, db):
    headers = account(api)

    def write(method, path, body, expected, model):
        response = api.request(method, path, headers=key_headers(headers), json=body)
        assert response.status_code == expected, response.text
        value = response.json()
        model.model_validate(value)
        return value

    questions = []
    for kind in ("single", "multiple", "text", "number"):
        content = {"type": kind, "title": kind}
        if kind in ("single", "multiple"):
            content["options"] = [{"id": "yes", "label": "是"}, {"id": "no", "label": "否"}]
        result = write("POST", "/api/questions", {"content": content}, 201, QuestionVersionResult)
        questions.append(result)
        qid, vid = result["question"]["_id"], result["version"]["_id"]
        QuestionView.model_validate(api.get(f"/api/questions/{qid}", headers=headers).json())
        VersionPage.model_validate(api.get(f"/api/questions/{qid}/versions", headers=headers).json())
        version = api.get(f"/api/questions/{qid}/versions/{vid}", headers=headers)
        assert version.status_code == 200, version.text
        VersionResult.model_validate(version.json())
        # Omitted validation limits stay omitted instead of becoming JSON null.
        assert all(item is not None for item in version.json()["version"]["content"]["validation"].values())
    QuestionPage.model_validate(api.get("/api/questions", headers=headers).json())
    refs = [{"id": "local_" + item["version"]["content"]["type"], "questionId": item["question"]["_id"], "versionId": item["version"]["_id"]} for item in questions]
    created = api.post("/api/surveys", headers=key_headers(headers), json={"title": "有类型的问卷", "settings": {"anonymous": True}, "questions": refs})
    assert created.status_code == 201, created.text
    survey = created.json()
    survey_model = TypeAdapter(SurveyView)
    survey_model.validate_python(survey)
    path = f"/api/surveys/{survey['_id']}"
    survey_model.validate_python(api.get(path, headers=headers).json())
    SurveyPage.model_validate(api.get("/api/surveys", headers=headers).json())

    original = questions[0]
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    edited = write("POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": vid, "content": {**original["version"]["content"], "title": "新版题目"},
    }, 201, QuestionVersionResult)
    restored = write("POST", f"/api/questions/{qid}/restore", {"revision": edited["question"]["revision"], "sourceVersionId": vid}, 201, QuestionVersionResult)
    write("POST", f"/api/questions/{qid}/copies", {"versionId": vid}, 201, QuestionVersionResult)
    archived_version = write("PATCH", f"/api/questions/{qid}/versions/{vid}", {"revision": restored["question"]["revision"], "archived": True}, 200, VersionResult)
    write("PATCH", f"/api/questions/{qid}", {"revision": archived_version["questionRevision"], "archived": True}, 200, QuestionView)

    published = api.post(path + "/publish", headers=key_headers(headers), json={"revision": survey["revision"]})
    assert published.status_code == 200, published.text
    survey_model.validate_python(published.json())
    public_path = f"/api/public/surveys/{survey['publicId']}"
    public = api.get(public_path, headers=headers)
    assert public.status_code == 200, public.text
    PublicSurvey.model_validate(public.json())
    assert all("questionId" not in question and "jumpRules" not in question for question in public.json()["questions"])

    # Existing v1 documents use the same read endpoints and preserve their exact
    # embedded shape. This changes only this fixture's isolated database.
    legacy_questions = [{name: value for name, value in item.items() if name not in ("questionId", "versionId", "versionNo")} for item in survey["questions"]]
    db.surveys.update_one({"_id": ObjectId(survey["_id"])}, {"$set": {"schemaVersion": 1, "questions": legacy_questions}})
    legacy = api.get(path, headers=headers)
    assert legacy.status_code == 200 and legacy.json()["schemaVersion"] == 1, legacy.text
    survey_model.validate_python(legacy.json())
    SurveyPage.model_validate(api.get("/api/surveys", headers=headers).json())
    PublicSurvey.model_validate(api.get(public_path, headers=headers).json())
    closed = api.post(path + "/close", headers=key_headers(headers), json={"revision": legacy.json()["revision"]})
    assert closed.status_code == 200, closed.text
    survey_model.validate_python(closed.json())
