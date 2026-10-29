"""Whole-question grants, retained snapshots and privacy through the real API."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bson import ObjectId
import pytest

from test_api import answer, assert_api_error, database_snapshot, register, start, submit
from test_library_flow import content, new_question, new_survey, ref, write


def management(api, user, method, path, body=None, key=None):
    return api.request(method, path, headers={**user, "Idempotency-Key": key or str(uuid4())}, json=body)


def revision(api, owner, question_id):
    result = api.get(f"/api/questions/{question_id}", headers=owner)
    assert result.status_code == 200, result.text
    return result.json()["revision"]


def grant(api, owner, question_id, username, *, expected=201, expected_revision=None, key=None):
    body = {"revision": expected_revision if expected_revision is not None else revision(api, owner, question_id),
            "recipientUsername": username}
    result = management(api, owner, "POST", f"/api/questions/{question_id}/shares", body, key)
    assert result.status_code == expected, result.text
    return result.json()


def revoke(api, owner, question_id, share_id, *, expected_revision=None, key=None):
    body = {"revision": expected_revision if expected_revision is not None else revision(api, owner, question_id)}
    result = management(api, owner, "POST", f"/api/questions/{question_id}/shares/{share_id}/revoke", body, key)
    assert result.status_code == 200, result.text
    return result.json()


def assert_error(result, status, code="NOT_FOUND"):
    assert_api_error(result, status, code)


def test_grant_exact_registered_username_uses_existing_normalization_and_hides_internal_fields(api, db):
    _, owner = register(api, "owner")
    recipient, reader = register(api, "Student_A")
    original = new_question(api, owner)
    qid = original["question"]["_id"]
    shared = grant(api, owner, qid, "  ＳＴＵＤＥＮＴ＿Ａ  ")
    assert shared["questionRevision"] == 2
    assert set(shared["share"]) == {"_id", "recipient", "grantedAt", "revokedAt", "createdAt", "updatedAt"}
    assert shared["share"]["recipient"] == {"_id": recipient["_id"], "username": "Student_A"}
    assert shared["share"]["revokedAt"] is None
    document = db.question_shares.find_one({"_id": ObjectId(shared["share"]["_id"])})
    assert document["recipientId"] == ObjectId(recipient["_id"])
    assert document["revision"] == 1
    result = api.get(f"/api/questions/{qid}", headers=reader)
    assert result.status_code == 200, result.text
    assert result.json()["permissions"] == {"canManage": False, "canUse": True}
    assert api.get("/api/questions?scope=owned", headers=reader).json()["total"] == 0
    assert api.get("/api/questions?scope=shared", headers=reader).json()["total"] == 1
    assert api.get("/api/questions?scope=all", headers=reader).json()["total"] == 1


def test_registered_unicode_username_with_expanding_lowercase_can_receive_grant_and_replay(api, db):
    _, owner = register(api, "owner")
    display = "İ" * 32
    recipient, reader = register(api, display)
    qid = new_question(api, owner)["question"]["_id"]
    key = str(uuid4())
    path = f"/api/questions/{qid}/shares"
    succeeded = management(api, owner, "POST", path, {"revision": 1, "recipientUsername": display}, key)
    assert succeeded.status_code == 201, succeeded.text
    assert succeeded.json()["share"]["recipient"] == {"_id": recipient["_id"], "username": display}
    normalized_key = "i\u0307" * 32
    assert len(normalized_key) == 64
    replay = management(api, owner, "POST", path, {"revision": 1, "recipientUsername": normalized_key}, key)
    assert replay.status_code == 201 and replay.content == succeeded.content
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert api.get(f"/api/questions/{qid}", headers=reader).status_code == 200
    assert db.question_shares.count_documents({}) == 1 and revision(api, owner, qid) == 2


@pytest.mark.parametrize("username,code", [("missing_user", "SHARE_RECIPIENT_NOT_FOUND"),
                                           ("  ＯＷＮＥＲ  ", "SELF_SHARE_NOT_ALLOWED")])
def test_unknown_and_self_share_roll_back_revision_and_receipt(api, db, username, code):
    _, owner = register(api, "owner")
    qid = new_question(api, owner)["question"]["_id"]
    before = database_snapshot(db)
    result = management(api, owner, "POST", f"/api/questions/{qid}/shares", {"revision": 1, "recipientUsername": username})
    assert_error(result, 422, code)
    assert database_snapshot(db) == before
    assert revision(api, owner, qid) == 1
    assert db.question_shares.count_documents({}) == 0


def test_grant_revoke_regrant_reuses_relation_and_checks_revision_even_for_noop(api, db):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    qid = new_question(api, owner)["question"]["_id"]
    initial = grant(api, owner, qid, "reader")
    share_id = initial["share"]["_id"]
    same = grant(api, owner, qid, "READER", expected=200)
    assert same == initial
    before_stale = database_snapshot(db)
    stale = management(api, owner, "POST", f"/api/questions/{qid}/shares", {"revision": 1, "recipientUsername": "reader"})
    assert_error(stale, 409, "REVISION_CONFLICT")
    assert database_snapshot(db) == before_stale
    stopped = revoke(api, owner, qid, share_id)
    assert stopped["questionRevision"] == 3
    assert stopped["share"]["revokedAt"] is not None
    assert revoke(api, owner, qid, share_id) == stopped
    after_revoke = database_snapshot(db)
    assert_error(api.get(f"/api/questions/{qid}", headers=reader), 404)
    assert database_snapshot(db) == after_revoke
    fresh = grant(api, owner, qid, "reader", expected=200)
    assert fresh["questionRevision"] == 4
    assert fresh["share"]["_id"] == share_id
    assert fresh["share"]["createdAt"] == initial["share"]["createdAt"]
    assert fresh["share"]["revokedAt"] is None
    assert api.get(f"/api/questions/{qid}", headers=reader).status_code == 200
    assert db.question_shares.count_documents({}) == 1


def test_share_list_states_pagination_and_current_parent_revision(api, db):
    _, owner = register(api, "owner")
    for username in ("reader_a", "reader_b", "reader_c"):
        register(api, username)
    qid = new_question(api, owner)["question"]["_id"]
    first = grant(api, owner, qid, "reader_a")
    grant(api, owner, qid, "reader_b")
    grant(api, owner, qid, "reader_c")
    revoke(api, owner, qid, first["share"]["_id"])
    base = f"/api/questions/{qid}/shares"
    all_items = api.get(base + "?state=all&pageSize=1", headers=owner).json()
    second = api.get(base + "?state=all&pageSize=1&page=2", headers=owner).json()
    assert all_items["questionRevision"] == second["questionRevision"] == 5
    assert all_items["total"] == second["total"] == 3
    assert len(all_items["items"]) == len(second["items"]) == 1
    assert all_items["items"][0]["_id"] != second["items"][0]["_id"]
    active = api.get(base + "?state=active", headers=owner).json()
    ended = api.get(base + "?state=revoked", headers=owner).json()
    assert active["total"] == 2 and all(row["revokedAt"] is None for row in active["items"])
    assert ended["total"] == 1 and ended["items"][0]["_id"] == first["share"]["_id"]
    before = database_snapshot(db)
    for query in ("state=other", "page=0", "pageSize=101"):
        assert_error(api.get(base + "?" + query, headers=owner), 422, "INVALID_REQUEST")
        assert database_snapshot(db) == before


def test_management_permissions_and_share_membership_do_not_leak_to_recipients(api, db):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    _, stranger = register(api, "stranger")
    original, other = new_question(api, owner), new_question(api, owner)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    shared = grant(api, owner, qid, "reader")
    sid = shared["share"]["_id"]
    before = database_snapshot(db)
    for who in (reader, stranger):
        for path in ("shares", "usage"):
            assert_error(api.get(f"/api/questions/{qid}/{path}", headers=who), 404)
            assert database_snapshot(db) == before
        for method, suffix, body in (
            ("POST", "/shares", {"revision": 2, "recipientUsername": "stranger"}),
            ("POST", f"/shares/{sid}/revoke", {"revision": 2}),
            ("POST", "/versions", {"revision": 2, "baseVersionId": vid, "content": content("伪改")}),
            ("POST", "/restore", {"revision": 2, "sourceVersionId": vid}),
            ("PATCH", "", {"revision": 2, "archived": True}),
            ("PATCH", f"/versions/{vid}", {"revision": 2, "archived": True}),
        ):
            assert_error(management(api, who, method, f"/api/questions/{qid}" + suffix, body), 404)
            assert database_snapshot(db) == before
    assert_error(management(api, owner, "POST", f"/api/questions/{other['question']['_id']}/shares/{sid}/revoke",
                            {"revision": 1}), 404)
    assert database_snapshot(db) == before
    assert_error(management(api, owner, "POST", f"/api/questions/{qid}/shares/{ObjectId()}/revoke",
                            {"revision": 2}), 404)
    assert database_snapshot(db) == before
    assert revision(api, owner, qid) == 2


def test_whole_question_grant_covers_old_and_future_versions_but_copy_can_be_shared_independently(api, db):
    owner_user, owner = register(api, "owner")
    reader_user, reader = register(api, "reader")
    _, colleague = register(api, "colleague")
    original = new_question(api, owner)
    qid, v1 = original["question"]["_id"], original["version"]["_id"]
    second = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": v1, "content": content("第二版"),
    }, 201)
    sharing = grant(api, owner, qid, "reader")
    third = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": sharing["questionRevision"], "baseVersionId": second["version"]["_id"], "content": content("第三版"),
    }, 201)
    listing = api.get(f"/api/questions/{qid}/versions?archive=include", headers=reader).json()
    assert [item["versionNo"] for item in listing["items"]] == [3, 2, 1]
    for saved in (original, second, third):
        assert api.get(f"/api/questions/{qid}/versions/{saved['version']['_id']}", headers=reader).status_code == 200
        assert new_survey(api, reader, [ref(saved)])["questions"][0]["title"] == saved["version"]["content"]["title"]
    copied = write(api, reader, "POST", f"/api/questions/{qid}/copies", {"versionId": v1}, 201)
    assert copied["question"]["ownerId"] == reader_user["_id"]
    assert copied["question"]["origin"] == {"kind": "copy", "questionId": qid, "versionId": v1}
    assert copied["question"]["ownerId"] != owner_user["_id"]
    grant(api, reader, copied["question"]["_id"], "colleague")
    assert api.get(f"/api/questions/{copied['question']['_id']}", headers=colleague).status_code == 200
    before_denied_read = database_snapshot(db)
    assert_error(api.get(f"/api/questions/{qid}", headers=colleague), 404)
    assert database_snapshot(db) == before_denied_read
    revoke(api, owner, qid, sharing["share"]["_id"])
    assert api.get(f"/api/questions/{copied['question']['_id']}", headers=reader).status_code == 200
    assert api.get(f"/api/questions/{copied['question']['_id']}", headers=colleague).status_code == 200
    assert db.questions.count_documents({}) == 2


def test_revoked_saved_draft_keeps_required_and_jump_rules_independent_and_can_publish_fill(api):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    _, filler = register(api, "filler")
    original = new_question(api, owner, content("共享选择题", "single"))
    own_tail = new_question(api, reader, content("自己的后续题", "text"))
    qid = original["question"]["_id"]
    sharing = grant(api, owner, qid, "reader")
    owner_survey = new_survey(api, owner, [ref(original, required=True)])
    draft = new_survey(api, reader, [ref(original), ref(own_tail, "q2")])
    revoke(api, owner, qid, sharing["share"]["_id"])
    jump = [{"id": "rule1", "condition": {"operator": "eq", "value": "yes"}, "target": {"kind": "end"}}]
    updated = write(api, reader, "PATCH", f"/api/surveys/{draft['_id']}", {
        "revision": draft["revision"], "questions": [ref(original, required=False, jumpRules=jump), ref(own_tail, "q2")],
    })
    assert updated["questions"][0]["required"] is False
    assert updated["questions"][0]["jumpRules"] == jump
    untouched = api.get(f"/api/surveys/{owner_survey['_id']}", headers=owner).json()
    assert untouched["questions"][0]["required"] is True and untouched["questions"][0]["jumpRules"] == []
    published = write(api, reader, "POST", f"/api/surveys/{draft['_id']}/publish", {"revision": updated["revision"]})
    result = submit(api, filler, published, start(api, filler, published), [answer("yes")])
    assert result.status_code == 201, result.text
    assert api.get(f"/api/surveys/{draft['_id']}/statistics", headers=reader).json()["totalResponses"] == 1


@pytest.mark.parametrize("change", ["version", "instance", "remove_and_readd"])
def test_revocation_blocks_every_new_use_even_from_existing_draft(api, db, change):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    original = new_question(api, owner)
    qid = original["question"]["_id"]
    sharing = grant(api, owner, qid, "reader")
    newer = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": sharing["questionRevision"], "baseVersionId": original["version"]["_id"], "content": content("新版本"),
    }, 201)
    draft = new_survey(api, reader, [ref(original)])
    revoke(api, owner, qid, sharing["share"]["_id"])
    changed_ref = ref(newer) if change == "version" else ref(original, "renamed") if change == "instance" else ref(original)
    if change == "remove_and_readd":
        draft = write(api, reader, "PATCH", f"/api/surveys/{draft['_id']}", {"revision": draft["revision"], "questions": []})
    before = database_snapshot(db)
    result = management(api, reader, "PATCH", f"/api/surveys/{draft['_id']}", {"revision": draft["revision"], "questions": [changed_ref]})
    assert_error(result, 404)
    assert database_snapshot(db) == before
    assert api.get(f"/api/surveys/{draft['_id']}", headers=reader).json()["revision"] == draft["revision"]
    assert db.surveys.count_documents({}) == 1


def test_successful_copy_replays_original_receipt_after_revoke_without_new_source_use(api, db):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    original = new_question(api, owner)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    sharing = grant(api, owner, qid, "reader")
    key = str(uuid4())
    body = {"versionId": vid}
    saved = management(api, reader, "POST", f"/api/questions/{qid}/copies", body, key)
    assert saved.status_code == 201, saved.text
    revoke(api, owner, qid, sharing["share"]["_id"])
    before = db.questions.find_one({"_id": ObjectId(qid)})["guardRevision"]
    replay = management(api, reader, "POST", f"/api/questions/{qid}/copies", body, key)
    assert replay.status_code == 201 and replay.content == saved.content
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert db.questions.count_documents({}) == 2
    assert db.questions.find_one({"_id": ObjectId(qid)})["guardRevision"] == before
    before_denied_copy = database_snapshot(db)
    assert_error(management(api, reader, "POST", f"/api/questions/{qid}/copies", body), 404)
    assert database_snapshot(db) == before_denied_copy


def test_usage_counts_distinct_current_surveys_and_exposes_only_owner_details(api, db):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    _, filler = register(api, "filler")
    original = new_question(api, owner)
    qid = original["question"]["_id"]
    sharing = grant(api, owner, qid, "reader")
    own = new_survey(api, owner, [ref(original, "owner_instance")])
    other = new_survey(api, reader, [ref(original, "private_instance")])
    other = write(api, reader, "PATCH", f"/api/surveys/{other['_id']}", {"revision": 1, "title": "不应泄露的标题"})
    other = write(api, reader, "POST", f"/api/surveys/{other['_id']}/publish", {"revision": other["revision"]})
    assert submit(api, filler, other, start(api, filler, other), [answer(12, "private_instance")]).status_code == 201
    copied = write(api, reader, "POST", f"/api/questions/{qid}/copies", {"versionId": original["version"]["_id"]}, 201)
    new_survey(api, reader, [ref(copied)])
    before_queries = database_snapshot(db)
    for suffix in ("", "/responses", "/statistics"):
        assert_error(api.get(f"/api/surveys/{other['_id']}" + suffix, headers=owner), 404)
        assert database_snapshot(db) == before_queries
    usage = api.get(f"/api/questions/{qid}/usage?pageSize=1", headers=owner).json()
    assert usage["totalSurveyCount"] == 2
    assert usage["visibleSurveyCount"] == usage["otherSurveyCount"] == usage["total"] == 1
    assert usage["items"] == [{"surveyId": own["_id"], "title": own["title"], "status": "draft",
                               "instanceId": "owner_instance", "versionId": original["version"]["_id"], "versionNo": 1}]
    assert "不应泄露的标题" not in str(usage) and other["_id"] not in str(usage)
    page2 = api.get(f"/api/questions/{qid}/usage?pageSize=1&page=2", headers=owner).json()
    assert page2["items"] == [] and page2["totalSurveyCount"] == 2 and page2["total"] == 1
    assert_error(api.get(f"/api/questions/{qid}/usage", headers=reader), 404)
    assert database_snapshot(db) == before_queries
    revoke(api, owner, qid, sharing["share"]["_id"])
    assert api.get(f"/api/questions/{qid}/usage", headers=owner).json()["otherSurveyCount"] == 1


@pytest.mark.parametrize("body", [
    {"revision": 1, "recipientUsername": "reader", "versionIds": []},
    {"revision": 1, "recipientUsername": "reader", "canManage": True},
    {"revision": 1, "recipientUsername": "reader", "recipientId": "000000000000000000000001"},
])
def test_grants_reject_client_selected_permissions_or_user_ids(api, db, body):
    _, owner = register(api, "owner")
    register(api, "reader")
    qid = new_question(api, owner)["question"]["_id"]
    before = database_snapshot(db)
    assert_error(management(api, owner, "POST", f"/api/questions/{qid}/shares", body), 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before
    assert revision(api, owner, qid) == 1 and db.question_shares.count_documents({}) == 0


@pytest.mark.parametrize("replayed_operation", ["grant", "revoke"])
def test_share_receipt_replays_original_result_without_overwriting_later_access_state(api, db, replayed_operation):
    _, owner = register(api, "owner")
    _, reader = register(api, "reader")
    qid = new_question(api, owner)["question"]["_id"]
    key = str(uuid4())
    body = {"revision": 1, "recipientUsername": "reader"}
    path = f"/api/questions/{qid}/shares"
    first = management(api, owner, "POST", path, body, key)
    assert first.status_code == 201, first.text
    sid = first.json()["share"]["_id"]
    if replayed_operation == "grant":
        revoke(api, owner, qid, sid)
        expected_revoked = True
    else:
        key, body = str(uuid4()), {"revision": 2}
        path = f"/api/questions/{qid}/shares/{sid}/revoke"
        first = management(api, owner, "POST", path, body, key)
        assert first.status_code == 200, first.text
        grant(api, owner, qid, "reader", expected=200)
        expected_revoked = False
    before = db.questions.find_one({"_id": ObjectId(qid)})
    replay = management(api, owner, "POST", path, body, key)
    assert replay.status_code == first.status_code and replay.content == first.content
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert db.questions.find_one({"_id": ObjectId(qid)}) == before
    stored = db.question_shares.find_one({"_id": ObjectId(sid)})
    assert (stored["revokedAt"] is not None) is expected_revoked
    assert db.question_shares.count_documents({}) == 1
    before_read = database_snapshot(db)
    access = api.get(f"/api/questions/{qid}", headers=reader)
    if expected_revoked:
        assert_error(access, 404)
    else:
        assert access.status_code == 200, access.text
    assert database_snapshot(db) == before_read


def test_failed_grant_key_is_retryable_after_recipient_registers_and_normalized_replay_matches(api, db):
    _, owner = register(api, "owner")
    qid = new_question(api, owner)["question"]["_id"]
    key, body = str(uuid4()), {"revision": 1, "recipientUsername": "ＦＵＴＵＲＥ＿ＵＳＥＲ"}
    path = f"/api/questions/{qid}/shares"
    before_failed_grant = database_snapshot(db)
    assert_error(management(api, owner, "POST", path, body, key), 422, "SHARE_RECIPIENT_NOT_FOUND")
    assert database_snapshot(db) == before_failed_grant
    register(api, "future_user")
    succeeded = management(api, owner, "POST", path, body, key)
    assert succeeded.status_code == 201, succeeded.text
    replay = management(api, owner, "POST", path, {**body, "recipientUsername": "  Future_User  "}, key)
    assert replay.status_code == 201 and replay.content == succeeded.content
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert db.question_shares.count_documents({}) == 1 and revision(api, owner, qid) == 2


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_shared_management_requires_idempotency_key_without_state_change(api, db, operation):
    _, owner = register(api, "owner")
    register(api, "reader")
    qid = new_question(api, owner)["question"]["_id"]
    path, body = f"/api/questions/{qid}/shares", {"revision": 1, "recipientUsername": "reader"}
    if operation == "revoke":
        shared = grant(api, owner, qid, "reader")
        path += f"/{shared['share']['_id']}/revoke"
        body = {"revision": 2}
    before = database_snapshot(db)
    assert_error(api.post(path, headers=owner, json=body), 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before


def test_usage_covers_all_saved_states_and_expired_status_without_mutating_survey(api, db):
    _, owner = register(api, "owner")
    original = new_question(api, owner)
    qid = original["question"]["_id"]
    draft = new_survey(api, owner, [ref(original)])
    published = new_survey(api, owner, [ref(original)])
    published = write(api, owner, "POST", f"/api/surveys/{published['_id']}/publish", {"revision": 1})
    closed = new_survey(api, owner, [ref(original)])
    closed = write(api, owner, "POST", f"/api/surveys/{closed['_id']}/publish", {"revision": 1})
    closed = write(api, owner, "POST", f"/api/surveys/{closed['_id']}/close", {"revision": closed["revision"]})
    expired = new_survey(api, owner, [ref(original)])
    expired = write(api, owner, "POST", f"/api/surveys/{expired['_id']}/publish", {"revision": 1})
    db.surveys.update_one({"_id": ObjectId(expired["_id"])}, {"$set": {"deadlineAt": datetime.now(timezone.utc) - timedelta(hours=1)}})
    before = db.surveys.find_one({"_id": ObjectId(expired["_id"])})
    write(api, owner, "PATCH", f"/api/questions/{qid}", {"revision": 1, "archived": True})
    usage = api.get(f"/api/questions/{qid}/usage", headers=owner).json()
    assert usage["totalSurveyCount"] == usage["visibleSurveyCount"] == usage["total"] == 4
    assert usage["otherSurveyCount"] == 0
    assert {row["surveyId"]: row["status"] for row in usage["items"]} == {
        draft["_id"]: "draft", published["_id"]: "published", closed["_id"]: "closed", expired["_id"]: "closed",
    }
    assert db.surveys.find_one({"_id": ObjectId(expired["_id"])}) == before
    write(api, owner, "PATCH", f"/api/surveys/{draft['_id']}", {"revision": 1, "questions": []})
    assert api.get(f"/api/questions/{qid}/usage", headers=owner).json()["totalSurveyCount"] == 3
    unused = new_question(api, owner)
    empty = api.get(f"/api/questions/{unused['question']['_id']}/usage", headers=owner).json()
    assert empty["totalSurveyCount"] == empty["visibleSurveyCount"] == empty["otherSurveyCount"] == empty["total"] == 0
    assert empty["items"] == []
