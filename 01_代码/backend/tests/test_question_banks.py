"""Owned banks and permission-safe classification on the isolated replica set."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
from uuid import uuid4

from bson import ObjectId
from pydantic import TypeAdapter
from pymongo.collection import Collection
import pytest

from app.bank_contracts import BankItemPage, BankPage, BankView
from test_api import assert_api_error, database_snapshot, register
from test_library_flow import new_question, new_survey, ref, write


def request(api, owner, method, path, body=None, *, key=None):
    kwargs = {"headers": {**owner, "Idempotency-Key": key or str(uuid4())}}
    if body is not None:
        kwargs["json"] = body
    return api.request(method, path, **kwargs)


def new_bank(api, owner, name="个人题库", description=""):
    response = request(api, owner, "POST", "/api/question-banks", {"name": name, "description": description})
    assert response.status_code == 201, response.text
    TypeAdapter(BankView).validate_python(response.json())
    return response.json()


def add_item(api, owner, bank, question, status=201, key=None):
    response = request(api, owner, "PUT", f"/api/question-banks/{bank['_id']}/items/{question['question']['_id']}", key=key)
    assert response.status_code == status, response.text
    return response


def grant(api, owner, question, username):
    qid = question["question"]["_id"]
    revision = api.get(f"/api/questions/{qid}", headers=owner).json()["revision"]
    return write(api, owner, "POST", f"/api/questions/{qid}/shares", {
        "revision": revision, "recipientUsername": username,
    }, 201)


def revoke(api, owner, question, shared):
    qid = question["question"]["_id"]
    revision = api.get(f"/api/questions/{qid}", headers=owner).json()["revision"]
    return write(api, owner, "POST", f"/api/questions/{qid}/shares/{shared['share']['_id']}/revoke", {"revision": revision})


def test_bank_metadata_is_owned_trimmed_and_paginated(api, db):
    _, owner = register(api, "bank-owner")
    _, outsider = register(api, "bank-outsider")
    bank = new_bank(api, owner, "  课程题库  ", "  个人整理  ")
    assert bank["name"] == "课程题库" and bank["description"] == "个人整理"
    assert bank["revision"] == 1 and bank["itemCount"] == 0
    assert set(bank) == {"_id", "ownerId", "name", "description", "revision", "itemCount", "createdAt", "updatedAt"}
    another = new_bank(api, owner, "课程题库")
    new_bank(api, outsider, "其他人的题库")
    page = api.get("/api/question-banks?pageSize=1", headers=owner)
    assert page.status_code == 200, page.text
    TypeAdapter(BankPage).validate_python(page.json())
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    second = api.get("/api/question-banks?pageSize=1&page=2", headers=owner).json()
    assert {page.json()["items"][0]["_id"], second["items"][0]["_id"]} == {bank["_id"], another["_id"]}
    current = api.get(f"/api/question-banks/{bank['_id']}", headers=owner).json()
    assert BankView.model_validate(current) == BankView.model_validate(bank)
    before = database_snapshot(db)
    for suffix in ("", "/items"):
        assert_api_error(api.get(f"/api/question-banks/{bank['_id']}" + suffix, headers=outsider), 404, "NOT_FOUND")
        assert database_snapshot(db) == before


def test_bank_metadata_revision_changes_only_for_actual_metadata_changes(api, db):
    _, owner = register(api, "bank-owner")
    bank = new_bank(api, owner)
    q = new_question(api, owner)
    path = f"/api/question-banks/{bank['_id']}"
    first_updated = bank["updatedAt"]
    add_item(api, owner, bank, q)
    add_item(api, owner, bank, q, 200)
    current = api.get(path, headers=owner).json()
    assert current["revision"] == 1 and current["itemCount"] == 1
    assert datetime.fromisoformat(current["updatedAt"]) == datetime.fromisoformat(first_updated)
    same = request(api, owner, "PATCH", path, {"revision": 1, "name": "  个人题库  "})
    assert same.status_code == 200
    assert BankView.model_validate(same.json()) == BankView.model_validate(current)
    updated = request(api, owner, "PATCH", path, {"revision": 1, "name": "新分类", "description": "说明"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2 and updated.json()["name"] == "新分类"
    before = database_snapshot(db)
    stale = request(api, owner, "PATCH", path, {"revision": 1, "description": "说明"})
    assert_api_error(stale, 409, "REVISION_CONFLICT")
    assert database_snapshot(db) == before
    stale_delete = request(api, owner, "DELETE", path + "?revision=1")
    assert_api_error(stale_delete, 409, "REVISION_CONFLICT")
    assert database_snapshot(db) == before
    assert db.question_banks.count_documents({}) == 1
    assert db.questions.find_one({"_id": ObjectId(q["question"]["_id"])})["revision"] == 1


@pytest.mark.parametrize("body", [
    {"name": " "}, {"name": "a" * 201}, {"name": 12}, {"name": "题库", "description": None},
    {"name": "题库", "description": "a" * 2001}, {"name": "题库", "ownerId": "0" * 24},
])
def test_bank_creation_rejects_invalid_or_forged_fields(api, db, body):
    _, owner = register(api, "bank-owner")
    before = database_snapshot(db)
    response = request(api, owner, "POST", "/api/question-banks", body)
    assert_api_error(response, 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before
    assert db.question_banks.count_documents({}) == 0
    assert db.mutation_receipts.count_documents({}) == 0


@pytest.mark.parametrize("changes", [{}, {"name": None}, {"description": None}, {"name": " "}, {"itemCount": 0}])
def test_bank_patch_requires_valid_metadata(api, db, changes):
    _, owner = register(api, "bank-owner")
    bank = new_bank(api, owner)
    before = database_snapshot(db)
    response = request(api, owner, "PATCH", f"/api/question-banks/{bank['_id']}", {"revision": 1, **changes})
    assert_api_error(response, 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before


def test_membership_requires_current_read_permission_and_bank_ownership(api, db):
    _, owner = register(api, "bank-owner")
    _, recipient = register(api, "bank-recipient")
    _, outsider = register(api, "bank-outsider")
    bank = new_bank(api, recipient)
    question = new_question(api, owner)
    qid = question["question"]["_id"]
    path = f"/api/question-banks/{bank['_id']}/items/{qid}"
    before = database_snapshot(db)
    for actor, method in ((recipient, "PUT"), (owner, "PUT"), (outsider, "DELETE")):
        assert_api_error(request(api, actor, method, path), 404, "NOT_FOUND")
        assert database_snapshot(db) == before
    assert db.question_bank_items.count_documents({}) == 0
    grant(api, owner, question, "bank-recipient")
    add_item(api, recipient, bank, question)
    page = api.get(f"/api/question-banks/{bank['_id']}/items", headers=recipient).json()
    TypeAdapter(BankItemPage).validate_python(page)
    assert page["total"] == 1
    assert page["items"][0]["available"] is True
    assert page["items"][0]["question"]["permissions"] == {"canManage": False, "canUse": True}
    assert "origin" not in page["items"][0]["question"]


def test_revocation_keeps_private_placeholder_paging_and_allows_cleanup(api, db):
    _, owner = register(api, "bank-owner")
    _, recipient = register(api, "bank-recipient")
    bank = new_bank(api, recipient)
    shared_question = new_question(api, owner)
    own_question = new_question(api, recipient)
    shared = grant(api, owner, shared_question, "bank-recipient")
    add_item(api, recipient, bank, shared_question)
    add_item(api, recipient, bank, own_question)
    revoke(api, owner, shared_question, shared)
    qid = shared_question["question"]["_id"]
    first = api.get(f"/api/question-banks/{bank['_id']}/items?pageSize=1", headers=recipient).json()
    second = api.get(f"/api/question-banks/{bank['_id']}/items?pageSize=1&page=2", headers=recipient).json()
    assert first["total"] == second["total"] == 2
    combined = first["items"] + second["items"]
    assert next(item for item in combined if item["questionId"] == qid) == {"questionId": qid, "available": False}
    assert api.get(f"/api/question-banks/{bank['_id']}", headers=recipient).json()["itemCount"] == 2
    repeat = add_item(api, recipient, bank, shared_question, 200)
    assert repeat.json() == {"bankId": bank["_id"], "questionId": qid, "created": False}
    path = f"/api/question-banks/{bank['_id']}/items/{qid}"
    removed = request(api, recipient, "DELETE", path)
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert request(api, recipient, "DELETE", path).json()["removed"] is False
    before = database_snapshot(db)
    assert_api_error(request(api, recipient, "PUT", path), 404, "NOT_FOUND")
    assert database_snapshot(db) == before
    assert db.question_bank_items.count_documents({}) == 1


def test_archived_questions_can_be_classified_in_multiple_banks_without_copying(api, db):
    _, owner = register(api, "bank-owner")
    question = new_question(api, owner)
    qid, vid = question["question"]["_id"], question["version"]["_id"]
    write(api, owner, "PATCH", f"/api/questions/{qid}", {"revision": 1, "archived": True})
    write(api, owner, "PATCH", f"/api/questions/{qid}/versions/{vid}", {"revision": 2, "archived": True})
    banks = [new_bank(api, owner), new_bank(api, owner)]
    for bank in banks:
        add_item(api, owner, bank, question)
        item = api.get(f"/api/question-banks/{bank['_id']}/items", headers=owner).json()["items"][0]
        assert item["available"] is True and item["question"]["permissions"]["canUse"] is False
    assert db.questions.count_documents({}) == 1
    assert db.question_versions.count_documents({}) == 1
    assert db.question_bank_items.count_documents({}) == 2


def test_deleting_bank_only_removes_container_and_memberships(api, db):
    _, owner = register(api, "bank-owner")
    _, recipient = register(api, "bank-recipient")
    question = new_question(api, owner)
    survey = new_survey(api, owner, [ref(question)])
    grant(api, owner, question, "bank-recipient")
    first, second = new_bank(api, owner), new_bank(api, owner)
    add_item(api, owner, first, question)
    add_item(api, owner, second, question)
    before = {name: list(db[name].find({})) for name in ("questions", "question_versions", "question_shares", "surveys")}
    deleted = request(api, owner, "DELETE", f"/api/question-banks/{first['_id']}?revision=1")
    assert deleted.status_code == 200 and deleted.json() == {"bankId": first["_id"], "deleted": True}
    after_delete = database_snapshot(db)
    assert_api_error(api.get(f"/api/question-banks/{first['_id']}", headers=owner), 404, "NOT_FOUND")
    assert database_snapshot(db) == after_delete
    assert db.question_bank_items.count_documents({"bankId": ObjectId(first["_id"])}) == 0
    assert db.question_bank_items.count_documents({"bankId": ObjectId(second["_id"])}) == 1
    assert {name: list(db[name].find({})) for name in before} == before
    published = write(api, owner, "POST", f"/api/surveys/{survey['_id']}/publish", {"revision": 1})
    assert published["status"] == "published"
    assert api.get(f"/api/questions/{question['question']['_id']}", headers=recipient).status_code == 200


def test_bank_add_remove_delete_replay_preserves_original_result_without_resurrection(api, db):
    _, owner = register(api, "bank-owner")
    question = new_question(api, owner)
    bank = new_bank(api, owner)
    path = f"/api/question-banks/{bank['_id']}"
    item_path = path + "/items/" + question["question"]["_id"]
    add_key, remove_key, delete_key = str(uuid4()), str(uuid4()), str(uuid4())
    added = request(api, owner, "PUT", item_path, key=add_key)
    assert added.status_code == 201, added.text
    removed = request(api, owner, "DELETE", item_path, key=remove_key)
    assert removed.status_code == 200 and removed.json()["removed"] is True
    add_replay = request(api, owner, "PUT", item_path, key=add_key)
    assert add_replay.status_code == 201 and add_replay.content == added.content
    assert add_replay.headers["idempotency-replayed"] == "true"
    assert db.question_bank_items.count_documents({}) == 0
    deleted = request(api, owner, "DELETE", path + "?revision=1", key=delete_key)
    assert deleted.status_code == 200, deleted.text
    for method, url, key, original in [
        ("PUT", item_path, add_key, added),
        ("DELETE", item_path, remove_key, removed),
        ("DELETE", path + "?revision=1", delete_key, deleted),
    ]:
        replay = request(api, owner, method, url, key=key)
        assert replay.status_code == original.status_code and replay.content == original.content
        assert replay.headers["idempotency-replayed"] == "true"
    before_reused_key = database_snapshot(db)
    reused = request(api, owner, "DELETE", path + "?revision=2", key=delete_key)
    assert_api_error(reused, 409, "IDEMPOTENCY_KEY_REUSED")
    assert database_snapshot(db) == before_reused_key
    assert db.question_banks.count_documents({}) == db.question_bank_items.count_documents({}) == 0


def test_bank_create_and_patch_replay_precede_current_state_checks(api, db):
    _, owner = register(api, "bank-owner")
    create_key, patch_key = str(uuid4()), str(uuid4())
    created = request(api, owner, "POST", "/api/question-banks", {"name": "  课程分类  "}, key=create_key)
    assert created.status_code == 201, created.text
    bank = created.json()
    path = f"/api/question-banks/{bank['_id']}"
    patch_body = {"revision": 1, "description": "  最新说明  "}
    patched = request(api, owner, "PATCH", path, patch_body, key=patch_key)
    assert patched.status_code == 200 and patched.json()["revision"] == 2
    replay = request(api, owner, "PATCH", path, {"revision": 1, "description": "最新说明"}, key=patch_key)
    assert replay.status_code == 200 and replay.content == patched.content
    deleted = request(api, owner, "DELETE", path + "?revision=2")
    assert deleted.status_code == 200, deleted.text
    replay = request(api, owner, "POST", "/api/question-banks", {"name": "课程分类", "description": ""}, key=create_key)
    assert replay.status_code == 201 and replay.content == created.content
    assert replay.headers["idempotency-replayed"] == "true"
    assert db.question_banks.count_documents({}) == 0


def test_every_bank_mutation_requires_key_and_bodyless_routes_reject_payload(api, db):
    _, owner = register(api, "bank-owner")
    bank = new_bank(api, owner)
    question = new_question(api, owner)
    path = f"/api/question-banks/{bank['_id']}"
    item_path = path + "/items/" + question["question"]["_id"]
    before = database_snapshot(db)
    for method, url, body in [
        ("POST", "/api/question-banks", {"name": "新题库"}),
        ("PATCH", path, {"revision": 1, "name": "新名称"}),
        ("DELETE", path + "?revision=1", None),
        ("PUT", item_path, None),
        ("DELETE", item_path, None),
    ]:
        kwargs = {"headers": owner}
        if body is not None:
            kwargs["json"] = body
        failed = api.request(method, url, **kwargs)
        assert_api_error(failed, 422, "INVALID_REQUEST")
        assert database_snapshot(db) == before
    receipt_count = db.mutation_receipts.count_documents({})
    for method, url in [("DELETE", path + "?revision=1"), ("PUT", item_path), ("DELETE", item_path)]:
        failed = request(api, owner, method, url, {"unexpected": "payload"})
        assert_api_error(failed, 422, "INVALID_REQUEST")
        assert database_snapshot(db) == before
    assert db.question_banks.count_documents({}) == 1
    assert db.question_bank_items.count_documents({}) == 0
    assert db.mutation_receipts.count_documents({}) == receipt_count


def test_concurrent_bank_add_and_delete_never_leave_orphan_memberships(api, db, monkeypatch):
    _, owner = register(api, "bank-owner")
    question = new_question(api, owner)
    bank = new_bank(api, owner)
    original = Collection.find_one
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    visited = 0

    def synchronize(self, *args, **kwargs):
        nonlocal visited
        result = original(self, *args, **kwargs)
        if self.name == "question_banks" and kwargs.get("session") is not None:
            with lock:
                wait_here = visited < 2
                visited += 1
            if wait_here:
                rendezvous.wait(timeout=10)
        return result

    monkeypatch.setattr(Collection, "find_one", synchronize)
    path = f"/api/question-banks/{bank['_id']}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(request, api, owner, "PUT", path + "/items/" + question["question"]["_id"])
        delete = pool.submit(request, api, owner, "DELETE", path + "?revision=1")
        added, deleted = add.result(timeout=20), delete.result(timeout=20)
    assert deleted.status_code == 200, deleted.text
    assert added.status_code in (201, 404), added.text
    if added.status_code == 404:
        assert_api_error(added, 404, "NOT_FOUND")
    # The concurrent delete is allowed to change the database. The rejected add
    # must leave no completed receipt while the successful delete keeps its one.
    assert db.mutation_receipts.count_documents({"operationId": "bank.item.add"}) == (1 if added.status_code == 201 else 0)
    assert db.question_banks.count_documents({}) == 0
    assert db.question_bank_items.count_documents({}) == 0


def test_bank_add_and_revoke_share_one_permission_coordination_point(api, db, monkeypatch):
    _, owner = register(api, "bank-owner")
    _, recipient = register(api, "bank-recipient")
    question = new_question(api, owner)
    shared = grant(api, owner, question, "bank-recipient")
    bank = new_bank(api, recipient)
    qid = question["question"]["_id"]
    original = Collection.find_one
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    visited = 0

    def synchronize(self, *args, **kwargs):
        nonlocal visited
        result = original(self, *args, **kwargs)
        if self.name == "questions" and kwargs.get("session") is not None:
            with lock:
                wait_here = visited < 2
                visited += 1
            if wait_here:
                rendezvous.wait(timeout=10)
        return result

    monkeypatch.setattr(Collection, "find_one", synchronize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        adding = pool.submit(request, api, recipient, "PUT", f"/api/question-banks/{bank['_id']}/items/{qid}")
        revoking = pool.submit(request, api, owner, "POST", f"/api/questions/{qid}/shares/{shared['share']['_id']}/revoke", {"revision": 2})
        added, revoked = adding.result(timeout=20), revoking.result(timeout=20)
    assert revoked.status_code == 200, revoked.text
    assert added.status_code in (201, 404), added.text
    if added.status_code == 404:
        assert_api_error(added, 404, "NOT_FOUND")
    assert db.question_bank_items.count_documents({}) == (1 if added.status_code == 201 else 0)
    assert db.mutation_receipts.count_documents({"operationId": "bank.item.add"}) == (1 if added.status_code == 201 else 0)
    after_race = database_snapshot(db)
    assert_api_error(api.get(f"/api/questions/{qid}", headers=recipient), 404, "NOT_FOUND")
    assert database_snapshot(db) == after_race
    items = api.get(f"/api/question-banks/{bank['_id']}/items", headers=recipient).json()["items"]
    assert items == ([{"questionId": qid, "available": False}] if added.status_code == 201 else [])
