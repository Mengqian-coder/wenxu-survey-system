"""Basic v2 workflow using the real HTTP contract and isolated replica set."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
from uuid import uuid4

from bson import ObjectId
from pymongo.collection import Collection
import pytest

from test_api import register, start, submit, answer, assert_api_error, database_snapshot


def write(api, headers, method, path, body, status=200, key=None):
    response = api.request(method, path, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=body)
    assert response.status_code == status, response.text
    return response.json()


def content(title="年龄", kind="number"):
    value = {"type": kind, "title": title, "description": "", "validation": {}}
    if kind == "number":
        value["validation"] = {"integerOnly": True, "min": 0, "max": 150}
    elif kind in ("single", "multiple"):
        value["options"] = [{"id": "yes", "label": "是"}, {"id": "no", "label": "否"}]
    return value


def new_question(api, user, value=None):
    return write(api, user, "POST", "/api/questions", {"content": value or content()}, 201)


def ref(result, instance="q1", **extra):
    return {"id": instance, "questionId": result["question"]["_id"], "versionId": result["version"]["_id"],
            "required": True, "jumpRules": [], **extra}


def new_survey(api, user, refs):
    return write(api, user, "POST", "/api/surveys", {
        "title": "基础流程", "description": "", "settings": {"anonymous": True, "allowRepeat": True},
        "questions": refs,
    }, 201)


def test_independent_question_version_pinned_publish_fill_and_results(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    original = new_question(api, owner)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    initial_document = db.question_versions.find_one({"_id": ObjectId(vid)})
    survey = new_survey(api, owner, [ref(original)])
    assert survey["schemaVersion"] == 2
    assert survey["questions"][0]["versionNo"] == 1
    assert survey["questions"][0]["title"] == "年龄"
    updated = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": vid, "content": content("新版年龄"),
    }, 201)
    assert updated["version"]["versionNo"] == 2
    assert updated["version"]["baseVersionId"] == vid
    assert db.question_versions.find_one({"_id": ObjectId(vid)}) == initial_document
    assert api.get(f"/api/surveys/{survey['_id']}", headers=owner).json()["questions"][0]["title"] == "年龄"
    published = write(api, owner, "POST", f"/api/surveys/{survey['_id']}/publish", {"revision": survey["revision"]})
    public = api.get(f"/api/public/surveys/{published['publicId']}", headers=filler).json()
    assert set(public["questions"][0]) == {"id", "type", "title", "description", "required", "validation"}
    assert public["questions"][0]["title"] == "年龄"
    started = start(api, filler, published)
    submitted = submit(api, filler, published, started, [answer(24)])
    assert submitted.status_code == 201, submitted.text
    assert submit(api, filler, published, started, [answer(24)]).status_code == 201
    stats = api.get(f"/api/surveys/{survey['_id']}/statistics", headers=owner).json()
    assert stats["totalResponses"] == 1
    assert stats["questions"][0]["average"] == 24
    assert db.responses.count_documents({}) == 1
    current = api.get(f"/api/surveys/{survey['_id']}", headers=owner).json()
    before = database_snapshot(db)
    result = api.patch(f"/api/surveys/{survey['_id']}", headers={**owner, "Idempotency-Key": str(uuid4())},
                       json={"revision": current["revision"], "questions": [ref(updated)]})
    assert_api_error(result, 409, "SURVEY_LOCKED")
    assert database_snapshot(db) == before


def test_two_history_operations_restore_new_version_and_select_old_without_new_version(api, db):
    _, user = register(api, "owner")
    original = new_question(api, user)
    qid, first = original["question"]["_id"], original["version"]["_id"]
    second = write(api, user, "POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": first, "content": content("新内容"),
    }, 201)
    survey = new_survey(api, user, [ref(second)])
    changed = write(api, user, "PATCH", f"/api/surveys/{survey['_id']}", {
        "revision": survey["revision"], "questions": [ref(original)],
    })
    assert changed["questions"][0]["versionId"] == first
    assert db.question_versions.count_documents({}) == 2
    restored = write(api, user, "POST", f"/api/questions/{qid}/restore", {
        "revision": 2, "sourceVersionId": first,
    }, 201)
    assert restored["version"]["versionNo"] == 3
    assert restored["version"]["operation"] == "restore"
    assert restored["version"]["baseVersionId"] == first
    assert restored["version"]["content"] == original["version"]["content"]
    assert restored["version"]["_id"] != first
    assert db.question_versions.count_documents({}) == 3


def test_duplicate_identity_and_client_snapshot_rejected_without_partial_write(api, db):
    _, user = register(api, "owner")
    original = new_question(api, user)
    before = database_snapshot(db)
    response = api.post("/api/surveys", headers={**user, "Idempotency-Key": str(uuid4())}, json={
        "title": "重复", "settings": {"anonymous": True}, "questions": [ref(original), ref(original, "q2")],
    })
    assert_api_error(response, 422, "DUPLICATE_QUESTION")
    assert database_snapshot(db) == before
    response = api.post("/api/surveys", headers={**user, "Idempotency-Key": str(uuid4())}, json={
        "title": "篡改", "settings": {"anonymous": True}, "questions": [{**ref(original), "title": "伪造"}],
    })
    assert_api_error(response, 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before


def test_archive_blocks_new_binding_but_keeps_saved_draft_publishable(api, db):
    _, user = register(api, "owner")
    original = new_question(api, user)
    qid = original["question"]["_id"]
    survey = new_survey(api, user, [ref(original)])
    write(api, user, "PATCH", f"/api/questions/{qid}", {"revision": 1, "archived": True})
    assert api.get("/api/questions", headers=user).json()["total"] == 0
    archived = api.get("/api/questions?archive=only", headers=user).json()
    assert archived["total"] == 1 and archived["items"][0]["permissions"]["canUse"] is False
    same = write(api, user, "PATCH", f"/api/surveys/{survey['_id']}", {
        "revision": survey["revision"], "questions": [ref(original, required=False)],
    })
    assert same["questions"][0]["required"] is False
    published = write(api, user, "POST", f"/api/surveys/{survey['_id']}/publish", {"revision": same["revision"]})
    assert published["status"] == "published"
    before = database_snapshot(db)
    response = api.post("/api/surveys", headers={**user, "Idempotency-Key": str(uuid4())}, json={
        "title": "新增", "settings": {"anonymous": True}, "questions": [ref(original)],
    })
    assert_api_error(response, 409, "QUESTION_ARCHIVED")
    assert database_snapshot(db) == before
    assert db.surveys.count_documents({}) == 1


def test_version_archive_is_independent_and_requires_fresh_permission_for_new_instance(api, db):
    _, user = register(api, "owner")
    original = new_question(api, user)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    survey = new_survey(api, user, [ref(original)])
    write(api, user, "PATCH", f"/api/questions/{qid}/versions/{vid}", {"revision": 1, "archived": True})
    write(api, user, "PATCH", f"/api/surveys/{survey['_id']}", {"revision": 1, "questions": [ref(original, required=False)]})
    before = database_snapshot(db)
    response = api.patch(f"/api/surveys/{survey['_id']}", headers={**user, "Idempotency-Key": str(uuid4())},
                         json={"revision": 2, "questions": [ref(original, "renamed")]})
    assert_api_error(response, 409, "VERSION_ARCHIVED")
    assert database_snapshot(db) == before
    assert api.get(f"/api/questions/{qid}/versions", headers=user).json()["total"] == 0
    assert api.get(f"/api/questions/{qid}/versions?archive=include", headers=user).json()["total"] == 1
    result = api.get(f"/api/questions/{qid}", headers=user).json()
    assert result["latestVersionId"] == vid and result["permissions"]["canUse"] is False


def test_copy_has_independent_identity_and_source_relation(api, db):
    _, user = register(api, "owner")
    original = new_question(api, user)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    copied = write(api, user, "POST", f"/api/questions/{qid}/copies", {"versionId": vid}, 201)
    assert copied["question"]["_id"] != qid
    assert copied["question"]["origin"] == {"kind": "copy", "questionId": qid, "versionId": vid}
    assert copied["version"]["baseVersionId"] is None
    assert copied["version"]["operation"] == "copy"
    assert copied["version"]["content"] == original["version"]["content"]
    assert len(new_survey(api, user, [ref(original), ref(copied, "q2")])["questions"]) == 2


def test_new_version_and_management_require_owner_and_version_membership(api, db):
    _, owner = register(api, "owner")
    _, other = register(api, "other")
    original, foreign = new_question(api, owner), new_question(api, other)
    qid = original["question"]["_id"]
    before = database_snapshot(db)
    assert_api_error(api.get(f"/api/questions/{qid}", headers=other), 404, "NOT_FOUND")
    assert database_snapshot(db) == before
    for who, source in [(other, original["version"]["_id"]), (owner, foreign["version"]["_id"])]:
        response = api.post(f"/api/questions/{qid}/versions", headers={**who, "Idempotency-Key": str(uuid4())}, json={
            "revision": 1, "baseVersionId": source, "content": content(),
        })
        assert_api_error(response, 404, "NOT_FOUND")
        assert database_snapshot(db) == before
    assert db.question_versions.count_documents({}) == 2


def test_saved_shared_binding_survives_revocation_and_removal_then_readd_does_not(api, db):
    owner_user, owner = register(api, "owner")
    other_user, other = register(api, "other")
    original = new_question(api, owner)
    qid = ObjectId(original["question"]["_id"])
    now = datetime.now(timezone.utc)
    # Shared-management UI is a later phase; provision its agreed relation here.
    db.question_shares.insert_one(dict(_id=ObjectId(), questionId=qid, recipientId=ObjectId(other_user["_id"]),
        grantedBy=ObjectId(owner_user["_id"]), grantedAt=now, revokedAt=None, revision=1, createdAt=now, updatedAt=now))
    survey = new_survey(api, other, [ref(original)])
    db.question_shares.update_one({"questionId": qid}, {"$set": {"revokedAt": now}, "$inc": {"revision": 1}})
    before = database_snapshot(db)
    assert_api_error(api.get(f"/api/questions/{qid}", headers=other), 404, "NOT_FOUND")
    assert database_snapshot(db) == before
    retained = write(api, other, "PATCH", f"/api/surveys/{survey['_id']}", {"revision": 1, "questions": [ref(original, required=False)]})
    removed = write(api, other, "PATCH", f"/api/surveys/{survey['_id']}", {"revision": retained["revision"], "questions": []})
    before = database_snapshot(db)
    failed = api.patch(f"/api/surveys/{survey['_id']}", headers={**other, "Idempotency-Key": str(uuid4())},
                       json={"revision": removed["revision"], "questions": [ref(original)]})
    assert_api_error(failed, 404, "NOT_FOUND")
    assert database_snapshot(db) == before


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
def test_all_content_kinds_can_be_saved_and_selected(api, kind):
    _, owner = register(api, "owner")
    original = new_question(api, owner, content("题目", kind))
    assert new_survey(api, owner, [ref(original)])["questions"][0]["type"] == kind


@pytest.mark.parametrize("archive_first", [True, False])
def test_new_binding_and_archive_serialize_at_real_master_write(api, db, monkeypatch, archive_first):
    _, owner = register(api, "owner")
    original = new_question(api, owner)
    qid = original["question"]["_id"]
    entered, release = threading.Event(), threading.Event()
    real_write = Collection.find_one_and_update
    gate_claimed = threading.Event()

    def gated_write(collection, query, update, *args, **kwargs):
        increments = update.get("$inc", {})
        selected = (increments == {"guardRevision": 1}) if archive_first else "revision" in increments
        if (collection.database.name == db.name and collection.name == "questions" and
                selected and not gate_claimed.is_set()):
            gate_claimed.set()
            entered.set()
            assert release.wait(10), "Concurrent operation was not released"
        return real_write(collection, query, update, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", gated_write)

    def archive_request():
        return api.patch(f"/api/questions/{qid}", headers={**owner, "Idempotency-Key": str(uuid4())},
                         json={"revision": 1, "archived": True})

    def bind_request():
        return api.post("/api/surveys", headers={**owner, "Idempotency-Key": str(uuid4())}, json={
            "title": "竞争中的草稿", "settings": {"anonymous": True}, "questions": [ref(original)],
        })

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="paused") as pool:
        pending = pool.submit(bind_request if archive_first else archive_request)
        assert entered.wait(10)
        try:
            winner = archive_request() if archive_first else bind_request()
        finally:
            release.set()
        delayed = pending.result(15)
    if archive_first:
        assert winner.status_code == 200, winner.text
        assert delayed.status_code == 409, delayed.text
        assert delayed.json()["error"]["code"] == "QUESTION_ARCHIVED"
        assert db.surveys.count_documents({}) == 0
    else:
        assert winner.status_code == 201, winner.text
        assert delayed.status_code == 200, delayed.text
        saved = winner.json()
        assert write(api, owner, "POST", f"/api/surveys/{saved['_id']}/publish", {"revision": 1})["status"] == "published"
    assert db.questions.find_one({"_id": ObjectId(qid)})["archivedAt"] is not None


def test_legacy_survey_stays_fillable_and_requires_explicit_structure_migration(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_survey(api, owner, [ref(new_question(api, owner))])
    legacy_questions = [{k: v for k, v in item.items() if k not in ("questionId", "versionId", "versionNo")}
                        for item in saved["questions"]]
    db.surveys.update_one({"_id": ObjectId(saved["_id"])}, {"$set": {"schemaVersion": 1, "questions": legacy_questions}})
    read = api.get(f"/api/surveys/{saved['_id']}", headers=owner).json()
    assert read["schemaVersion"] == 1 and "versionId" not in read["questions"][0]
    edited = write(api, owner, "PATCH", f"/api/surveys/{saved['_id']}", {"revision": 1, "title": "旧卷标题可改"})
    before = database_snapshot(db)
    response = api.patch(f"/api/surveys/{saved['_id']}", headers={**owner, "Idempotency-Key": str(uuid4())},
                         json={"revision": edited["revision"], "questions": []})
    assert_api_error(response, 409, "LEGACY_SURVEY_REQUIRES_MIGRATION")
    assert database_snapshot(db) == before
    published = write(api, owner, "POST", f"/api/surveys/{saved['_id']}/publish", {"revision": edited["revision"]})
    assert published["schemaVersion"] == 1
    result = submit(api, filler, published, start(api, filler, published), [answer(23)])
    assert result.status_code == 201, result.text
    stats = api.get(f"/api/surveys/{saved['_id']}/statistics", headers=owner).json()
    assert stats["totalResponses"] == 1 and stats["questions"][0]["average"] == 23
