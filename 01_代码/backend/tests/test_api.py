"""End-to-end business checks against the HTTP API and real MongoDB."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import sys
import threading

from bson import ObjectId
import jwt
import pytest


PASSWORD = "correct-horse-battery-123!"


def register(api, username):
    result = api.post("/api/auth/register", json={"username": username, "password": PASSWORD})
    assert result.status_code == 201, result.text
    user = result.json()
    login = api.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return user, {"Authorization": "Bearer " + login.json()["access_token"]}


def question(qid="q1", kind="text", required=True, validation=None, **extra):
    return {
        "id": qid, "type": kind, "title": qid, "description": "",
        "required": required, "validation": validation or {}, "jumpRules": [], **extra,
    }


def survey_body(*, anonymous=True, allow_repeat=False, questions=None):
    return {
        "title": "课程体验调查", "description": "测试问卷",
        "settings": {"anonymous": anonymous, "allowRepeat": allow_repeat},
        "deadlineAt": None,
        "questions": questions if questions is not None else [question()],
    }


def create_survey(api, headers, **kwargs):
    response = api.post("/api/surveys", headers=headers, json=survey_body(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def publish(api, headers, survey):
    response = api.post(
        f"/api/surveys/{survey['_id']}/publish", headers=headers,
        json={"revision": survey["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def start(api, headers, survey):
    response = api.post(f"/api/public/surveys/{survey['publicId']}/start", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def submit(api, headers, survey, started, answers):
    return api.post(
        f"/api/public/surveys/{survey['publicId']}/submit", headers=headers,
        json={
            "submissionKey": started["submissionKey"],
            "submissionToken": started["submissionToken"], "answers": answers,
        },
    )


def answer(value="有效答案", qid="q1"):
    return {"questionId": qid, "value": value}


def assert_api_error(response, status, code):
    """Assert the public error contract, not just an HTTP status bucket."""
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error"}, payload
    error = payload["error"]
    assert set(error).issubset({"code", "message", "details"}), error
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"], error
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def assert_submitted(response):
    assert response.status_code == 201, response.text
    assert response.json() == {"submitted": True}


def database_snapshot(db):
    """Keep raw BSON fields, IDs and times so unchanged means every field."""
    return {
        name: list(db[name].find({}).sort("_id", 1))
        for name in sorted(db.list_collection_names())
    }


def survey_state(db, survey, *, responses, participations, revision):
    survey_id = ObjectId(survey["_id"])
    document = db.surveys.find_one({"_id": survey_id})
    saved_responses = list(db.responses.find({"surveyId": survey_id}).sort("_id", 1))
    saved_participations = list(db.participations.find({"surveyId": survey_id}).sort("_id", 1))
    assert document["revision"] == revision
    assert len(saved_responses) == responses
    assert len(saved_participations) == participations
    return {"survey": document, "responses": saved_responses, "participations": saved_participations}


def assert_submission_revision(db, before_survey, increment):
    """Submitting may change revision/update time, not questionnaire content."""
    after = db.surveys.find_one({"_id": before_survey["_id"]})
    assert after == {
        **before_survey,
        "revision": before_survey["revision"] + increment,
        "updatedAt": after["updatedAt"],
    }
    assert after["updatedAt"] >= before_survey["updatedAt"]


def assert_stored_response(document, survey, started, answers, respondent_id=None):
    expected_fields = {"_id", "surveyId", "submissionKey", "answers", "submittedAt", "schemaVersion"}
    if respondent_id is not None:
        expected_fields.add("respondentId")
        assert document["respondentId"] == ObjectId(respondent_id)
    assert set(document) == expected_fields
    assert isinstance(document["_id"], ObjectId)
    assert document["surveyId"] == ObjectId(survey["_id"])
    assert document["submissionKey"] == started["submissionKey"]
    assert document["answers"] == answers
    assert document["schemaVersion"] == 1
    assert isinstance(document["submittedAt"], datetime) and document["submittedAt"].tzinfo is not None


def assert_participation(document, survey, user):
    assert set(document) == {"_id", "surveyId", "userId"}
    assert isinstance(document["_id"], ObjectId)
    assert document["surveyId"] == ObjectId(survey["_id"])
    assert document["userId"] == ObjectId(user["_id"])


def branching_questions():
    return [
        question("q1", "single", options=[{"id": "yes", "label": "参加"}, {"id": "no", "label": "未参加"}], jumpRules=[{
            "id": "skip", "condition": {"operator": "eq", "value": "no"},
            "target": {"kind": "question", "questionId": "q3"},
        }]),
        question("q2", "multiple", validation={"minSelections": 1, "maxSelections": 2}, options=[
            {"id": "red", "label": "红"}, {"id": "blue", "label": "蓝"}, {"id": "green", "label": "绿"},
        ]),
        question("q3", "number", validation={"min": 0, "max": 100, "integerOnly": True}),
        question("q4", required=False, validation={"minLength": 2, "maxLength": 10}),
    ]


def test_auth_normalization_credentials_and_password_storage(api, db):
    user, headers = register(api, "Ａlice")
    assert "passwordHash" not in user and "password" not in user
    raw = db.users.find_one({"_id": ObjectId(user["_id"])})
    assert raw["usernameKey"] == "alice"
    assert raw["passwordHash"] != PASSWORD
    before = database_snapshot(db)
    duplicate = api.post("/api/auth/register", json={"username": " alice ", "password": PASSWORD})
    assert_api_error(duplicate, 409, "USERNAME_TAKEN")
    assert database_snapshot(db) == before
    normalized_login = api.post("/api/auth/login", json={"username": " ALICE ", "password": PASSWORD})
    assert normalized_login.status_code == 200
    assert database_snapshot(db) == before
    assert_api_error(api.post("/api/auth/login", json={"username": "Alice", "password": "wrong-password"}), 401, "INVALID_CREDENTIALS")
    assert api.get("/api/auth/me", headers=headers).json()["_id"] == user["_id"]
    assert_api_error(api.get("/api/auth/me"), 401, "AUTH_REQUIRED")
    assert_api_error(api.get("/api/auth/me", headers={"Authorization": "Bearer invalid"}), 401, "INVALID_TOKEN")
    assert database_snapshot(db) == before


def test_ownership_pagination_draft_visibility_and_revision(api, db):
    _, owner = register(api, "owner")
    _, other = register(api, "other")
    survey = create_survey(api, owner)
    sid = survey["_id"]
    before = database_snapshot(db)
    listing = api.get("/api/surveys", headers=owner).json()
    assert listing["total"] == 1 and listing["items"][0]["_id"] == sid
    assert listing["page"] == 1 and listing["pageSize"] > 0
    assert api.get("/api/surveys", headers=other).json()["total"] == 0
    assert database_snapshot(db) == before
    for suffix in ("", "/responses", "/statistics"):
        assert_api_error(api.get(f"/api/surveys/{sid}{suffix}", headers=other), 404, "NOT_FOUND")
        assert database_snapshot(db) == before
    assert_api_error(api.patch(f"/api/surveys/{sid}", headers=other, json={"revision": 1, "title": "stolen"}), 404, "NOT_FOUND")
    for action in ("publish", "close"):
        assert_api_error(api.post(f"/api/surveys/{sid}/{action}", headers=other, json={"revision": 1}), 404, "NOT_FOUND")
        assert database_snapshot(db) == before
    assert_api_error(api.get(f"/api/public/surveys/{survey['publicId']}", headers=other), 404, "NOT_FOUND")
    assert database_snapshot(db) == before
    edit = api.patch(f"/api/surveys/{sid}", headers=owner, json={"revision": survey["revision"], "title": "修改后的标题"})
    assert edit.status_code == 200
    assert edit.json()["revision"] == survey["revision"] + 1
    stored = db.surveys.find_one({"_id": ObjectId(sid)})
    assert stored == {**before["surveys"][0], "title": "修改后的标题", "revision": survey["revision"] + 1, "updatedAt": stored["updatedAt"]}
    after_edit = database_snapshot(db)
    assert_api_error(api.patch(f"/api/surveys/{sid}", headers=owner, json={"revision": survey["revision"], "title": "覆盖旧版本"}), 409, "REVISION_CONFLICT")
    assert_api_error(api.get("/api/surveys/not-an-object-id", headers=owner), 404, "NOT_FOUND")
    assert database_snapshot(db) == after_edit
    survey_state(db, survey, responses=0, participations=0, revision=survey["revision"] + 1)


def test_publish_requirements_and_structure_lock(api, db):
    _, owner = register(api, "owner")
    empty = create_survey(api, owner, questions=[])
    before_empty_publish = database_snapshot(db)
    assert_api_error(api.post(f"/api/surveys/{empty['_id']}/publish", headers=owner, json={"revision": empty["revision"]}), 422, "VALIDATION_ERROR")
    assert database_snapshot(db) == before_empty_publish
    added = api.patch(f"/api/surveys/{empty['_id']}", headers=owner, json={
        "revision": empty["revision"], "questions": [question("q1"), question("q2")],
    })
    assert added.status_code == 200 and len(added.json()["questions"]) == 2
    assert publish(api, owner, added.json())["status"] == "published"
    survey = publish(api, owner, create_survey(api, owner))
    sid = survey["_id"]
    frozen = database_snapshot(db)
    for fields in (
        {"questions": [question("changed")]},
        {"questions": survey["questions"]},
        {"settings": {"anonymous": False, "allowRepeat": False}},
        {"settings": {"anonymous": True, "allowRepeat": True}},
    ):
        response = api.patch(f"/api/surveys/{sid}", headers=owner, json={"revision": survey["revision"], **fields})
        assert_api_error(response, 409, "SURVEY_LOCKED")
        assert database_snapshot(db) == frozen
        survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])
    edited = api.patch(f"/api/surveys/{sid}", headers=owner, json={"revision": survey["revision"], "description": "可修改说明"})
    assert edited.status_code == 200


def test_draft_settings_patch_stores_nested_defaults(api, db):
    _, owner = register(api, "owner")
    survey = create_survey(api, owner, anonymous=False, allow_repeat=True)
    response = api.patch(f"/api/surveys/{survey['_id']}", headers=owner, json={
        "revision": survey["revision"], "settings": {"anonymous": True},
    })
    assert response.status_code == 200, response.text
    assert response.json()["settings"] == {"anonymous": True, "allowRepeat": False}
    assert db.surveys.find_one({"_id": ObjectId(survey["_id"])})["settings"] == response.json()["settings"]


@pytest.mark.parametrize("mutate,error_code", [
    (lambda qs: qs.append(deepcopy(qs[0])), "VALIDATION_ERROR"),
    (lambda qs: qs[0]["options"].append({"id": "yes", "label": "重复ID"}), "VALIDATION_ERROR"),
    (lambda qs: qs[0]["jumpRules"][0]["target"].update(questionId="q1"), "VALIDATION_ERROR"),
    (lambda qs: qs[0]["jumpRules"][0]["target"].update(questionId="missing"), "VALIDATION_ERROR"),
    (lambda qs: qs[1]["validation"].update(minSelections=3, maxSelections=1), "VALIDATION_ERROR"),
    (lambda qs: qs[2]["validation"].update(min=101, max=100), "VALIDATION_ERROR"),
    (lambda qs: qs[3]["validation"].update(maxLength=10001), "INVALID_REQUEST"),
])
def test_invalid_survey_configuration_is_rejected(api, db, mutate, error_code):
    _, owner = register(api, "owner")
    questions = branching_questions()
    mutate(questions)
    before = database_snapshot(db)
    response = api.post("/api/surveys", headers=owner, json=survey_body(questions=questions))
    assert_api_error(response, 422, error_code)
    assert database_snapshot(db) == before


def test_preview_path_skips_required_and_clears_after_changed_answer(api, db):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, questions=branching_questions()))
    before = database_snapshot(db)
    started = start(api, filler, survey)
    another_attempt = start(api, filler, survey)
    assert started["submissionKey"] != another_attempt["submissionKey"]
    assert database_snapshot(db) == before
    url = f"/api/public/surveys/{survey['publicId']}/preview"
    partial = api.post(url, headers=filler, json={"answers": [answer("no")]})
    assert partial.status_code == 200, partial.text
    assert partial.json()["nextQuestionId"] == "q3"
    assert partial.json()["complete"] is False
    assert partial.json()["answers"] == [answer("no")]
    assert database_snapshot(db) == before
    changed = api.post(url, headers=filler, json={
        "changedQuestionId": "q1",
        "answers": [answer("no"), answer(["red"], "q2"), answer(20, "q3"), answer("说明", "q4")],
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["answers"] == [answer("no")]
    assert changed.json()["nextQuestionId"] == "q3"
    assert changed.json()["clearedQuestionIds"] == ["q2", "q3", "q4"]
    assert database_snapshot(db) == before
    valid_answers = [answer("no"), answer(20, "q3"), answer(None, "q4")]
    complete = api.post(url, headers=filler, json={"answers": valid_answers})
    assert complete.status_code == 200 and complete.json()["complete"] is True
    assert complete.json()["answers"] == valid_answers
    assert database_snapshot(db) == before
    assert_submitted(submit(api, filler, survey, started, valid_answers))
    state = survey_state(db, survey, responses=1, participations=1, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, started, valid_answers)
    assert_participation(state["participations"][0], survey, user)
    assert_submission_revision(db, before["surveys"][0], 1)


@pytest.mark.parametrize("answers,error_code", [
    ([answer("yes"), answer([], "q2"), answer(1, "q3")], "VALIDATION_ERROR"),
    ([answer("yes"), answer(["red", "red"], "q2"), answer(1, "q3")], "VALIDATION_ERROR"),
    ([answer("yes"), answer(["invalid"], "q2"), answer(1, "q3")], "VALIDATION_ERROR"),
    ([answer("no"), answer(1.5, "q3")], "VALIDATION_ERROR"),
    ([answer("no"), answer(True, "q3")], "INVALID_REQUEST"),
    ([answer("no"), answer(101, "q3")], "VALIDATION_ERROR"),
    ([answer("no"), answer(1, "q3"), answer("x", "q4")], "VALIDATION_ERROR"),
    ([answer("no"), answer(1, "q3"), answer(["red"], "q2")], "VALIDATION_ERROR"),
    ([answer("no"), answer(1, "q3"), answer(2, "q3")], "VALIDATION_ERROR"),
    ([answer("no")], "VALIDATION_ERROR"),
])
def test_invalid_submissions_do_not_create_answers_or_participation(api, db, answers, error_code):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, questions=branching_questions()))
    before = database_snapshot(db)
    started = start(api, filler, survey)
    assert database_snapshot(db) == before
    response = submit(api, filler, survey, started, answers)
    assert_api_error(response, 422, error_code)
    assert database_snapshot(db) == before
    survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


def test_anonymous_submission_is_separate_idempotent_and_user_bound(api, db):
    _, owner = register(api, "owner")
    filler_user, filler = register(api, "filler")
    _, other = register(api, "other")
    survey = publish(api, owner, create_survey(api, owner))
    before = database_snapshot(db)
    started = start(api, filler, survey)
    assert database_snapshot(db) == before
    cross_user = submit(api, other, survey, started, [answer()])
    assert_api_error(cross_user, 403, "SUBMISSION_TOKEN_MISMATCH")
    assert database_snapshot(db) == before
    result = submit(api, filler, survey, started, [answer("  匿名答案  ")])
    assert_submitted(result)
    state = survey_state(db, survey, responses=1, participations=1, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, started, [answer("匿名答案")])
    assert_participation(state["participations"][0], survey, filler_user)
    assert_submission_revision(db, before["surveys"][0], 1)
    committed = database_snapshot(db)
    retry = submit(api, filler, survey, started, [answer("匿名答案")])
    assert_submitted(retry)
    assert database_snapshot(db) == committed
    assert_api_error(submit(api, filler, survey, started, [answer("不同答案")]), 409, "SUBMISSION_KEY_REUSED")
    assert database_snapshot(db) == committed
    assert_api_error(submit(api, other, survey, started, [answer("匿名答案")]), 403, "SUBMISSION_TOKEN_MISMATCH")
    assert database_snapshot(db) == committed
    assert_api_error(api.post(f"/api/public/surveys/{survey['publicId']}/start", headers=filler), 409, "ALREADY_SUBMITTED")
    assert database_snapshot(db) == committed
    assert set(db.list_collection_names()) == {"users", "surveys", "responses", "participations"}
    listing = api.get(f"/api/surveys/{survey['_id']}/responses", headers=owner).json()
    assert listing["total"] == 1
    assert listing["items"][0]["_id"] == str(state["responses"][0]["_id"])
    assert listing["items"][0]["answers"] == [answer("匿名答案")]
    assert "respondentId" not in listing["items"][0]
    assert "submissionKey" not in listing["items"][0]
    assert database_snapshot(db) == committed


@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("allow_repeat", [False, True])
def test_anonymity_and_repeat_policy_keep_records_consistent(api, db, anonymous, allow_repeat):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, anonymous=anonymous, allow_repeat=allow_repeat))
    before = database_snapshot(db)
    attempts = [start(api, filler, survey), start(api, filler, survey)]
    assert attempts[0]["submissionKey"] != attempts[1]["submissionKey"]
    assert database_snapshot(db) == before
    assert_submitted(submit(api, filler, survey, attempts[0], [answer("  第一次  ")]))
    state = survey_state(db, survey, responses=1, participations=0 if allow_repeat else 1, revision=survey["revision"] + 1)
    identity = None if anonymous else user["_id"]
    first_response = state["responses"][0]
    assert_stored_response(first_response, survey, attempts[0], [answer("第一次")], identity)
    if not allow_repeat:
        assert_participation(state["participations"][0], survey, user)
    assert_submission_revision(db, before["surveys"][0], 1)
    committed_once = database_snapshot(db)
    assert_submitted(submit(api, filler, survey, attempts[0], [answer("第一次")]))
    assert database_snapshot(db) == committed_once
    second = submit(api, filler, survey, attempts[1], [answer("第二次")])
    if allow_repeat:
        assert_submitted(second)
        state = survey_state(db, survey, responses=2, participations=0, revision=survey["revision"] + 2)
        by_key = {item["submissionKey"]: item for item in state["responses"]}
        assert by_key[attempts[0]["submissionKey"]] == first_response
        assert_stored_response(by_key[attempts[1]["submissionKey"]], survey, attempts[1], [answer("第二次")], identity)
        assert_submission_revision(db, before["surveys"][0], 2)
        committed_twice = database_snapshot(db)
        assert_submitted(submit(api, filler, survey, attempts[1], [answer("  第二次  ")]))
        assert database_snapshot(db) == committed_twice
    else:
        assert_api_error(second, 409, "ALREADY_SUBMITTED")
        assert database_snapshot(db) == committed_once
        assert_api_error(api.post(f"/api/public/surveys/{survey['publicId']}/start", headers=filler), 409, "ALREADY_SUBMITTED")
        assert database_snapshot(db) == committed_once


@pytest.mark.parametrize("anonymous", [False, True])
def test_concurrent_submissions_cannot_bypass_no_repeat(api, db, anonymous):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, anonymous=anonymous))
    before = database_snapshot(db)
    starts = [start(api, filler, survey), start(api, filler, survey)]
    assert database_snapshot(db) == before
    gate = threading.Barrier(2)

    def send(index):
        gate.wait(timeout=10)
        return submit(api, filler, survey, starts[index], [answer(f"并发答案 {index}")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert sorted(result.status_code for result in results) == [201, 409]
    winner = next(index for index, result in enumerate(results) if result.status_code == 201)
    loser = 1 - winner
    assert_submitted(results[winner])
    assert_api_error(results[loser], 409, "ALREADY_SUBMITTED")
    state = survey_state(db, survey, responses=1, participations=1, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, starts[winner], [answer(f"并发答案 {winner}")], None if anonymous else user["_id"])
    assert_participation(state["participations"][0], survey, user)
    assert_submission_revision(db, before["surveys"][0], 1)
    assert db.responses.find_one({"surveyId": ObjectId(survey["_id"]), "submissionKey": starts[loser]["submissionKey"]}) is None
    committed = database_snapshot(db)
    assert_submitted(submit(api, filler, survey, starts[winner], [answer(f"并发答案 {winner}")]))
    assert database_snapshot(db) == committed
    assert_api_error(submit(api, filler, survey, starts[loser], [answer(f"并发答案 {loser}")]), 409, "ALREADY_SUBMITTED")
    assert database_snapshot(db) == committed


@pytest.mark.parametrize("allow_repeat", [False, True])
def test_concurrent_retries_of_same_submission_are_idempotent(api, db, allow_repeat):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, allow_repeat=allow_repeat))
    before = database_snapshot(db)
    started = start(api, filler, survey)
    assert database_snapshot(db) == before
    gate = threading.Barrier(2)

    def send(index):
        gate.wait(timeout=10)
        value = "  并发规范答案  " if index == 0 else "并发规范答案"
        return submit(api, filler, survey, started, [answer(value)])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    for result in results:
        assert_submitted(result)
    state = survey_state(db, survey, responses=1, participations=0 if allow_repeat else 1, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, started, [answer("并发规范答案")])
    if not allow_repeat:
        assert_participation(state["participations"][0], survey, user)
    assert_submission_revision(db, before["surveys"][0], 1)
    committed = database_snapshot(db)
    assert_submitted(submit(api, filler, survey, started, [answer("并发规范答案")]))
    assert database_snapshot(db) == committed


def test_response_insert_failure_rolls_back_participation_and_revision(api, db):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner))
    started = start(api, filler, survey)
    before = database_snapshot(db)
    old_validator = db.command("listCollections", filter={"name": "responses"})["cursor"]["firstBatch"][0]["options"]["validator"]
    db.command({"collMod": "responses", "validator": {"_id": {"$exists": False}}})
    try:
        response = submit(api, filler, survey, started, [answer()])
        assert_api_error(response, 503, "DATABASE_ERROR")
        assert database_snapshot(db) == before
        survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])
    finally:
        db.command({"collMod": "responses", "validator": old_validator})
    assert_submitted(submit(api, filler, survey, started, [answer()]))
    state = survey_state(db, survey, responses=1, participations=1, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, started, [answer()])
    assert_participation(state["participations"][0], survey, user)
    assert_submission_revision(db, before["surveys"][0], 1)
    committed = database_snapshot(db)
    assert_submitted(submit(api, filler, survey, started, [answer()]))
    assert database_snapshot(db) == committed


def test_manual_close_rejects_start_and_submission_but_allows_success_retry(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, allow_repeat=True))
    successful = start(api, filler, survey)
    pending = start(api, filler, survey)
    initial = db.surveys.find_one({"_id": ObjectId(survey["_id"])})
    assert_submitted(submit(api, filler, survey, successful, [answer()]))
    state = survey_state(db, survey, responses=1, participations=0, revision=survey["revision"] + 1)
    assert_stored_response(state["responses"][0], survey, successful, [answer()])
    assert_submission_revision(db, initial, 1)
    current = api.get(f"/api/surveys/{survey['_id']}", headers=owner).json()
    closed = api.post(f"/api/surveys/{survey['_id']}/close", headers=owner, json={"revision": current["revision"]})
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed" and closed.json()["closeReason"] == "manual"
    closed_state = survey_state(db, survey, responses=1, participations=0, revision=survey["revision"] + 2)
    closed_document = closed_state["survey"]
    assert closed_document["closedAt"] is not None
    assert closed_document == {
        **state["survey"], "status": "closed", "closeReason": "manual",
        "closedAt": closed_document["closedAt"], "updatedAt": closed_document["updatedAt"],
        "revision": state["survey"]["revision"] + 1,
    }
    assert closed_state["responses"] == state["responses"]
    frozen = database_snapshot(db)
    for request in (
        lambda: api.get(f"/api/public/surveys/{survey['publicId']}", headers=filler),
        lambda: api.post(f"/api/public/surveys/{survey['publicId']}/start", headers=filler),
        lambda: api.post(f"/api/public/surveys/{survey['publicId']}/preview", headers=filler, json={"answers": [answer()]}),
        lambda: submit(api, filler, survey, pending, [answer()]),
    ):
        assert_api_error(request(), 410, "SURVEY_CLOSED")
        assert database_snapshot(db) == frozen
    assert_submitted(submit(api, filler, survey, successful, [answer()]))
    assert database_snapshot(db) == frozen
    assert_api_error(api.post(f"/api/surveys/{survey['_id']}/close", headers=owner, json={"revision": closed.json()["revision"]}), 409, "INVALID_STATE")
    assert_api_error(api.patch(f"/api/surveys/{survey['_id']}", headers=owner, json={"revision": closed.json()["revision"], "title": "不应修改"}), 409, "SURVEY_CLOSED")
    assert database_snapshot(db) == frozen


def test_deadline_checked_even_when_stored_status_remains_published(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner))
    started = start(api, filler, survey)
    db.surveys.update_one({"_id": ObjectId(survey["_id"])}, {"$set": {"deadlineAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    expired_but_published = database_snapshot(db)
    assert_api_error(submit(api, filler, survey, started, [answer()]), 410, "SURVEY_CLOSED")
    # Submission checks the clock without needing a lazy-expiry write first.
    assert database_snapshot(db) == expired_but_published
    state = survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])
    assert state["survey"]["status"] == "published"


def test_lazy_expiry_uses_current_deadline_when_read_was_stale(api, db):
    _, owner = register(api, "owner")
    survey = publish(api, owner, create_survey(api, owner))
    survey_id = ObjectId(survey["_id"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deadline_a, deadline_b = now - timedelta(seconds=1), now - timedelta(seconds=2)
    db.surveys.update_one({"_id": survey_id}, {"$set": {"deadlineAt": deadline_a}})
    stale = db.surveys.find_one({"_id": survey_id})
    # Emulate a deadline edit committed after the expiry worker's read.
    db.surveys.update_one({"_id": survey_id}, {"$set": {"deadlineAt": deadline_b}})
    current = db.surveys.find_one({"_id": survey_id})
    before = database_snapshot(db)
    expired = api.app.state.service._expire(stale, now)
    assert expired["status"] == "closed"
    assert expired["closeReason"] == "deadline"
    assert expired["closedAt"] == deadline_b
    assert expired["deadlineAt"] == deadline_b
    assert expired == {
        **current, "status": "closed", "closeReason": "deadline", "closedAt": deadline_b,
        "updatedAt": now, "revision": current["revision"] + 1,
    }
    state = survey_state(db, survey, responses=0, participations=0, revision=current["revision"] + 1)
    assert state["survey"] == expired
    after = database_snapshot(db)
    assert after["users"] == before["users"]
    assert after["responses"] == before["responses"]
    assert after["participations"] == before["participations"]
    assert api.app.state.service._expire(expired, now) == expired
    assert database_snapshot(db) == after


@pytest.mark.parametrize("offset_ms, expected", [(-1, 201), (0, 410), (1, 410)])
def test_deadline_uses_request_arrival_time_and_accepts_inflight_before_expiry(api, db, monkeypatch, offset_ms, expected):
    _, owner = register(api, "owner")
    user, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner))
    started = start(api, filler, survey)
    deadline = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
    # Simulate expiry persisted while the request is in flight, without sleeps.
    db.surveys.update_one({"_id": ObjectId(survey["_id"])}, {"$set": {
        "deadlineAt": deadline, "status": "closed", "closedAt": deadline,
        "closeReason": "deadline",
    }})
    received = deadline + timedelta(milliseconds=offset_ms)
    monkeypatch.setattr("app.main.utcnow", lambda: received)
    before = database_snapshot(db)
    result = submit(api, filler, survey, started, [answer()])
    if expected == 201:
        assert_submitted(result)
        state = survey_state(db, survey, responses=1, participations=1, revision=survey["revision"] + 1)
        assert_stored_response(state["responses"][0], survey, started, [answer()])
        assert state["responses"][0]["submittedAt"] == received
        assert_participation(state["participations"][0], survey, user)
        assert_submission_revision(db, before["surveys"][0], 1)
        committed = database_snapshot(db)
        assert_submitted(submit(api, filler, survey, started, [answer()]))
        assert database_snapshot(db) == committed
    else:
        assert_api_error(result, 410, "SURVEY_CLOSED")
        assert database_snapshot(db) == before
        survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


def test_submission_token_cannot_be_reused_for_another_survey(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    first = publish(api, owner, create_survey(api, owner))
    second = publish(api, owner, create_survey(api, owner))
    before = database_snapshot(db)
    result = submit(api, filler, second, start(api, filler, first), [answer()])
    assert_api_error(result, 403, "SUBMISSION_TOKEN_MISMATCH")
    assert database_snapshot(db) == before
    for survey in (first, second):
        survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


def test_tokens_enforce_expiry_purpose_and_submission_key_binding(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner))
    started = start(api, filler, survey)
    before = database_snapshot(db)
    secret = api.app.state.settings.secret_key
    access = filler["Authorization"].split(" ", 1)[1]
    wrong_key = ("0" if started["submissionKey"][0] != "0" else "1") + started["submissionKey"][1:]

    def expired(token):
        claims = jwt.decode(token, options={"verify_signature": False})
        claims["exp"] = int(datetime.now(timezone.utc).timestamp()) - 60
        return jwt.encode(claims, secret, algorithm="HS256")

    for request, status, code in (
        (lambda: api.get("/api/auth/me", headers={"Authorization": "Bearer " + expired(access)}), 401, "INVALID_TOKEN"),
        (lambda: submit(api, filler, survey, {**started, "submissionToken": expired(started["submissionToken"])}, [answer()]), 401, "INVALID_SUBMISSION_TOKEN"),
        (lambda: api.get("/api/auth/me", headers={"Authorization": "Bearer " + started["submissionToken"]}), 401, "INVALID_TOKEN"),
        (lambda: submit(api, filler, survey, {**started, "submissionToken": access}, [answer()]), 401, "INVALID_SUBMISSION_TOKEN"),
        (lambda: submit(api, filler, survey, {**started, "submissionKey": wrong_key}, [answer()]), 403, "SUBMISSION_TOKEN_MISMATCH"),
    ):
        assert_api_error(request(), status, code)
        assert database_snapshot(db) == before
    survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


def test_request_body_limit_rejects_declared_and_actual_oversize(api):
    settings = api.app.state.settings
    old_limit = settings.max_request_bytes
    settings.max_request_bytes = 1024
    try:
        assert api.post("/api/auth/register", content=b"x" * 1025).status_code == 413
        # An untrusted small header must not bypass the measured body limit.
        assert api.post("/api/auth/register", content=b"x" * 1025, headers={"Content-Length": "1"}).status_code == 413
        for length in ("not-a-number", "-1"):
            assert api.post("/api/auth/register", content=b"{}", headers={"Content-Length": length}).status_code == 413
    finally:
        settings.max_request_bytes = old_limit


def test_cors_uses_explicit_lifespan_settings(api):
    from fastapi.testclient import TestClient
    from app.main import create_app

    allowed = "https://survey.example.test"
    settings = api.app.state.settings.model_copy(update={"cors_origins": [allowed]})
    with TestClient(create_app(settings=settings)) as client:
        response = client.options("/api/surveys", headers={
            "Origin": allowed, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == allowed
        actual = client.get("/health", headers={"Origin": allowed})
        assert actual.status_code == 200 and actual.headers["access-control-allow-origin"] == allowed
        denied = client.options("/api/surveys", headers={
            "Origin": "https://untrusted.example.test", "Access-Control-Request-Method": "POST",
        })
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers


def test_unknown_fields_and_client_supplied_owner_are_rejected(api, db):
    _, owner = register(api, "owner")
    body = survey_body()
    body["ownerId"] = str(ObjectId())
    before = database_snapshot(db)
    assert_api_error(api.post("/api/surveys", headers=owner, json=body), 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before
    survey = publish(api, owner, create_survey(api, owner))
    started = start(api, owner, survey)
    before = database_snapshot(db)
    assert_api_error(submit(api, owner, survey, {**started, "submissionToken": started["submissionToken"] + "tampered"}, [answer()]), 401, "INVALID_SUBMISSION_TOKEN")
    assert database_snapshot(db) == before
    survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


def test_statistics_counts_only_valid_answers_and_supports_question_values(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(api, owner, allow_repeat=True, questions=branching_questions()))
    payloads = [
        [answer("yes"), answer(["red", "blue"], "q2"), answer(10, "q3"), answer("  很好  ", "q4")],
        [answer("no"), answer(30, "q3"), answer(None, "q4")],
    ]
    attempts = []
    for answers in payloads:
        attempt = start(api, filler, survey)
        attempts.append(attempt)
        assert_submitted(submit(api, filler, survey, attempt, answers))
    state = survey_state(db, survey, responses=2, participations=0, revision=survey["revision"] + 2)
    by_key = {row["submissionKey"]: row for row in state["responses"]}
    normalized = deepcopy(payloads)
    normalized[0][-1]["value"] = "很好"
    for attempt, answers in zip(attempts, normalized):
        assert_stored_response(by_key[attempt["submissionKey"]], survey, attempt, answers)
    before_queries = database_snapshot(db)
    endpoint = f"/api/surveys/{survey['_id']}/statistics"
    response = api.get(endpoint, headers=owner)
    assert response.status_code == 200, response.text
    statistics = response.json()
    assert statistics["surveyId"] == survey["_id"]
    assert statistics["totalResponses"] == 2
    questions = {item["questionId"]: item for item in statistics["questions"]}
    assert questions["q1"]["answeredCount"] == 2
    assert {o["optionId"]: o["count"] for o in questions["q1"]["options"]} == {"yes": 1, "no": 1}
    assert questions["q2"]["answeredCount"] == 1
    assert {o["optionId"]: o["count"] for o in questions["q2"]["options"]} == {"red": 1, "blue": 1, "green": 0}
    assert questions["q3"]["answeredCount"] == 2
    assert questions["q3"]["average"] == 20
    assert questions["q4"]["answeredCount"] == 1
    text = api.get(endpoint, headers=owner, params={"questionId": "q4"}).json()
    assert len(text["questions"]) == 1
    assert text["questions"][0]["values"] == ["很好"]
    assert text["questions"][0]["valueTotal"] == 1
    numeric = api.get(endpoint, headers=owner, params={"questionId": "q3", "pageSize": 1}).json()
    assert numeric["questions"][0]["average"] == 20
    assert numeric["questions"][0]["valueTotal"] == 2
    assert len(numeric["questions"][0]["values"]) == 1
    assert_api_error(api.get(endpoint, headers=owner, params={"questionId": "missing"}), 404, "NOT_FOUND")
    assert database_snapshot(db) == before_queries


def test_empty_numeric_statistics_have_null_average(api, db):
    _, owner = register(api, "owner")
    survey = create_survey(api, owner, questions=[question("q1", "number", validation={"integerOnly": False})])
    before = database_snapshot(db)
    response = api.get(f"/api/surveys/{survey['_id']}/statistics", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["totalResponses"] == 0
    assert response.json()["questions"][0]["average"] is None
    assert database_snapshot(db) == before
    survey_state(db, survey, responses=0, participations=0, revision=survey["revision"])


@pytest.mark.parametrize("value", [1e308, sys.float_info.max])
def test_numeric_average_remains_finite_for_large_valid_values(api, value):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    survey = publish(api, owner, create_survey(
        api, owner, allow_repeat=True,
        questions=[question("q1", "number", validation={"integerOnly": False})],
    ))
    for _ in range(2):
        response = submit(api, filler, survey, start(api, filler, survey), [answer(value)])
        assert response.status_code == 201, response.text
    result = api.get(f"/api/surveys/{survey['_id']}/statistics", headers=owner)
    assert result.status_code == 200, result.text
    average = result.json()["questions"][0]["average"]
    assert math.isfinite(average)
    assert math.isclose(average, value, rel_tol=1e-14)
