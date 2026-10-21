"""Cross-operation invariants on the same real, isolated MongoDB fixture.

Existing API cases exercise each failure class. These cases add a populated
database and canonical-retry combinations, so equality protects existing data
as well as proving that a rejected request did not create a new answer.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
import pytest

import test_api as helpers


def unrelated_records(snapshot, survey):
    """An operation on one survey cannot rewrite other surveys or any users."""
    survey_id = ObjectId(survey["_id"])
    return {
        **snapshot,
        "surveys": [row for row in snapshot["surveys"] if row["_id"] != survey_id],
        "responses": [row for row in snapshot["responses"] if row["surveyId"] != survey_id],
        "participations": [row for row in snapshot["participations"] if row["surveyId"] != survey_id],
    }


@pytest.mark.parametrize("headers,error_code", [({}, "AUTH_REQUIRED"), ({"Authorization": "Bearer invalid"}, "INVALID_TOKEN")])
def test_unauthenticated_operations_preserve_populated_database(api, db, headers, error_code):
    _, owner = helpers.register(api, "owner")
    _, filler = helpers.register(api, "filler")
    active = helpers.publish(api, owner, helpers.create_survey(api, owner))
    completed = helpers.start(api, filler, active)
    pending = helpers.start(api, filler, active)
    helpers.assert_submitted(helpers.submit(api, filler, active, completed, [helpers.answer("已存在的答卷")]))
    draft = helpers.create_survey(api, owner)
    baseline = helpers.database_snapshot(db)
    active_revision = db.surveys.find_one({"_id": ObjectId(active["_id"])})["revision"]

    operations = [
        ("get", "/api/surveys", None),
        ("post", "/api/surveys", helpers.survey_body()),
        ("get", f"/api/surveys/{active['_id']}", None),
        ("patch", f"/api/surveys/{draft['_id']}", {"revision": draft["revision"], "title": "不应保存"}),
        ("post", f"/api/surveys/{draft['_id']}/publish", {"revision": draft["revision"]}),
        ("post", f"/api/surveys/{active['_id']}/close", {"revision": active_revision}),
        ("get", f"/api/surveys/{active['_id']}/responses", None),
        ("get", f"/api/surveys/{active['_id']}/statistics", None),
        ("get", f"/api/public/surveys/{active['publicId']}", None),
        ("post", f"/api/public/surveys/{active['publicId']}/start", None),
        ("post", f"/api/public/surveys/{active['publicId']}/preview", {"answers": [helpers.answer()]}),
        ("post", f"/api/public/surveys/{active['publicId']}/submit", {**pending, "answers": [helpers.answer()]}),
    ]
    for method, url, body in operations:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        result = api.request(method, url, **kwargs)
        helpers.assert_api_error(result, 401, error_code)
        assert helpers.database_snapshot(db) == baseline, (method, url)
    helpers.survey_state(db, active, responses=1, participations=1, revision=active_revision)
    helpers.survey_state(db, draft, responses=0, participations=0, revision=draft["revision"])


@pytest.mark.parametrize("anonymous", [False, True])
def test_canonical_retry_preserves_original_ids_payload_key_and_revision(api, db, anonymous):
    _, owner = helpers.register(api, "owner")
    user, filler = helpers.register(api, "filler")
    canary = helpers.publish(api, owner, helpers.create_survey(api, owner))
    helpers.assert_submitted(helpers.submit(api, filler, canary, helpers.start(api, filler, canary), [helpers.answer("其他问卷的既有答卷")]))

    questions = [
        helpers.question("q1", "multiple", options=[
            {"id": "a", "label": "选项 A"}, {"id": "b", "label": "选项 B"}, {"id": "c", "label": "选项 C"},
        ], validation={"minSelections": 1, "maxSelections": 2}),
        helpers.question("q2", validation={"maxLength": 20}),
        helpers.question("q3", "number", required=False, validation={"integerOnly": False}),
    ]
    survey = helpers.publish(api, owner, helpers.create_survey(
        api, owner, anonymous=anonymous, allow_repeat=True, questions=questions,
    ))
    before = helpers.database_snapshot(db)
    before_survey = db.surveys.find_one({"_id": ObjectId(survey["_id"])})
    attempt = helpers.start(api, filler, survey)
    assert helpers.database_snapshot(db) == before

    # Answers may arrive out of question order. The optional number is omitted.
    initial = [helpers.answer("  规范答案  ", "q2"), helpers.answer(["b", "a"], "q1")]
    canonical = [helpers.answer(["a", "b"], "q1"), helpers.answer("规范答案", "q2"), helpers.answer(None, "q3")]
    helpers.assert_submitted(helpers.submit(api, filler, survey, attempt, initial))
    state = helpers.survey_state(db, survey, responses=1, participations=0, revision=survey["revision"] + 1)
    helpers.assert_stored_response(state["responses"][0], survey, attempt, canonical, None if anonymous else user["_id"])
    helpers.assert_submission_revision(db, before_survey, 1)
    committed = helpers.database_snapshot(db)
    assert unrelated_records(committed, survey) == unrelated_records(before, survey)

    for equivalent in (
        canonical,
        [helpers.answer(None, "q3"), helpers.answer("\n规范答案\t", "q2"), helpers.answer(["b", "a"], "q1")],
    ):
        helpers.assert_submitted(helpers.submit(api, filler, survey, attempt, equivalent))
        assert helpers.database_snapshot(db) == committed

    changed = [helpers.answer(["a", "b"], "q1"), helpers.answer("改写已提交答案", "q2"), helpers.answer(None, "q3")]
    helpers.assert_api_error(helpers.submit(api, filler, survey, attempt, changed), 409, "SUBMISSION_KEY_REUSED")
    assert helpers.database_snapshot(db) == committed
    helpers.survey_state(db, canary, responses=1, participations=1, revision=canary["revision"] + 1)


class _FirstCallGate:
    """Pause once before a real command, without holding the bookkeeping lock."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0

    def pause_first(self):
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        # Claim the first call before waiting, so transaction retries cannot wait.
        if first:
            self.entered.set()
            assert self.release.wait(timeout=5), "Race command was not released within 5 seconds"


class _RaceTrace:
    """Only stage/status/revision/error metadata may enter the JUnit evidence."""

    def __init__(self):
        self._lock = threading.Lock()
        self.events = []

    def add(self, stage, **fields):
        with self._lock:
            self.events.append({"stage": stage, **fields})

    def record(self, record_testsuite_property, prefix, anonymous, allow_repeat):
        name = f"race.{prefix}.anonymous_{str(anonymous).lower()}.repeat_{str(allow_repeat).lower()}"
        with self._lock:
            payload = {
                "anonymous": anonymous,
                "allowRepeat": allow_repeat,
                "events": list(self.events),
            }
        record_testsuite_property(name, json.dumps(payload, sort_keys=True))


def _target_collection(collection, db):
    return (
        collection.database.client is db.client
        and collection.database.name == db.name
        and collection.name == "surveys"
    )


def _seed_manual_close_race(api, db, anonymous, allow_repeat):
    _, owner = helpers.register(api, "owner")
    user, filler = helpers.register(api, "filler")
    # A populated unrelated survey protects all four collections, not just counts.
    canary = helpers.publish(api, owner, helpers.create_survey(api, owner))
    helpers.assert_submitted(helpers.submit(
        api, filler, canary, helpers.start(api, filler, canary), [helpers.answer("Canary answer")],
    ))
    survey = helpers.publish(api, owner, helpers.create_survey(
        api, owner, anonymous=anonymous, allow_repeat=allow_repeat,
    ))
    baseline = helpers.database_snapshot(db)
    attempt = helpers.start(api, filler, survey)
    assert helpers.database_snapshot(db) == baseline
    state = helpers.survey_state(
        db, survey, responses=0, participations=0, revision=survey["revision"],
    )
    assert state["survey"]["status"] == "published"
    assert state["survey"]["deadlineAt"] is None  # Only manual close is under test.
    return owner, user, filler, survey, attempt, baseline, state["survey"]


def _assert_manual_close(before, after):
    assert after == {
        **before,
        "status": "closed",
        "revision": before["revision"] + 1,
        "closedAt": after["closedAt"],
        "closeReason": "manual",
        "updatedAt": after["updatedAt"],
    }
    assert after["closedAt"] == after["updatedAt"]
    assert after["closedAt"] >= before["updatedAt"]


@pytest.mark.parametrize("anonymous", [False, True], ids=["named", "anonymous"])
@pytest.mark.parametrize("allow_repeat", [False, True], ids=["no-repeat", "repeat"])
def test_manual_close_wins_before_transaction_write(
    api, db, monkeypatch, record_testsuite_property, anonymous, allow_repeat,
):
    """A real stale-snapshot write conflicts; the retried callback sees manual close."""
    owner, _, filler, survey, attempt, baseline, before_survey = _seed_manual_close_race(
        api, db, anonymous, allow_repeat,
    )
    survey_id = ObjectId(survey["_id"])
    revision = survey["revision"]
    gate, trace = _FirstCallGate(), _RaceTrace()
    session_lock = threading.Lock()
    observed_session = None
    original_find_one = Collection.find_one
    original_update_one = Collection.update_one

    def observed_find_one(collection, filter=None, *args, **kwargs):
        nonlocal observed_session
        result = original_find_one(collection, filter, *args, **kwargs)
        session = kwargs.get("session")
        if _target_collection(collection, db) and filter == {"_id": survey_id} and session is not None:
            with session_lock:
                if observed_session is None:
                    observed_session = session
                assert session is observed_session
            trace.add(
                "transaction_read", status=result["status"], revision=result["revision"],
                closeReason=result["closeReason"], inTransaction=session.in_transaction,
            )
        return result

    def paused_update_one(collection, filter, update, *args, **kwargs):
        session = kwargs.get("session")
        if (
            _target_collection(collection, db)
            and filter == {"_id": survey_id, "revision": revision, "status": "published"}
            and session is not None
        ):
            with session_lock:
                assert session is observed_session
            assert update["$inc"] == {"revision": 1}
            assert set(update) == {"$inc", "$set"} and set(update["$set"]) == {"updatedAt"}
            trace.add("transaction_write_wait", revision=revision)
            gate.pause_first()
            try:
                result = original_update_one(collection, filter, update, *args, **kwargs)
            except PyMongoError as error:
                trace.add(
                    "real_transaction_write_error", mongoCode=getattr(error, "code", None),
                    transientTransactionError=error.has_error_label("TransientTransactionError"),
                )
                # with_transaction must receive the original labelled Mongo error.
                raise
            trace.add("transaction_write_result", matchedCount=result.matched_count)
            return result
        return original_update_one(collection, filter, update, *args, **kwargs)

    try:
        with monkeypatch.context() as patches:
            patches.setattr(Collection, "find_one", observed_find_one)
            patches.setattr(Collection, "update_one", paused_update_one)
            # Exiting the executor joins its worker before patches are restored.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_submit = pool.submit(
                    helpers.submit, api, filler, survey, attempt, [helpers.answer("Race answer")],
                )
                try:
                    assert gate.entered.wait(timeout=5), "Submission did not reach its transaction write"
                    assert not pending_submit.done()
                    close = api.post(
                        f"/api/surveys/{survey['_id']}/close", headers=owner, json={"revision": revision},
                    )
                    assert close.status_code == 200, close.text
                    assert close.json()["status"] == "closed" and close.json()["revision"] == revision + 1
                    closed_state = helpers.survey_state(
                        db, survey, responses=0, participations=0, revision=revision + 1,
                    )
                    _assert_manual_close(before_survey, closed_state["survey"])
                    closed_snapshot = helpers.database_snapshot(db)
                    assert unrelated_records(closed_snapshot, survey) == unrelated_records(baseline, survey)
                    trace.add("manual_close_committed", httpStatus=200, status="closed", revision=revision + 1)
                    assert not pending_submit.done()
                finally:
                    gate.release.set()
                    submit = pending_submit.result(timeout=20)

            helpers.assert_api_error(submit, 410, "SURVEY_CLOSED")
            trace.add("submit_rejected", httpStatus=410, errorCode="SURVEY_CLOSED")
            assert gate.calls == 1
            events = trace.events
            reads = [event for event in events if event["stage"] == "transaction_read"]
            assert reads[0] == {
                "stage": "transaction_read", "status": "published", "revision": revision,
                "closeReason": None, "inTransaction": True,
            }
            errors = [event for event in events if event["stage"] == "real_transaction_write_error"]
            assert errors == [{
                "stage": "real_transaction_write_error", "mongoCode": 112,
                "transientTransactionError": True,
            }]
            assert len(reads) >= 2
            assert all(event["status"] == "closed" and event["revision"] == revision + 1
                       and event["closeReason"] == "manual" and event["inTransaction"] for event in reads[1:])
            assert events.index(errors[0]) < events.index(reads[1])
            assert helpers.database_snapshot(db) == closed_snapshot
            assert helpers.survey_state(
                db, survey, responses=0, participations=0, revision=revision + 1,
            ) == closed_state
            trace.add("closed_snapshot_preserved", responses=0, participations=0, revision=revision + 1)
    finally:
        gate.release.set()
        trace.record(record_testsuite_property, "close_wins", anonymous, allow_repeat)


@pytest.mark.parametrize("anonymous", [False, True], ids=["named", "anonymous"])
@pytest.mark.parametrize("allow_repeat", [False, True], ids=["no-repeat", "repeat"])
def test_committed_submission_makes_paused_close_revision_stale(
    api, db, monkeypatch, record_testsuite_property, anonymous, allow_repeat,
):
    """The old close CAS loses; a fresh close preserves the committed submission."""
    owner, user, filler, survey, attempt, baseline, before_survey = _seed_manual_close_race(
        api, db, anonymous, allow_repeat,
    )
    survey_id = ObjectId(survey["_id"])
    revision = survey["revision"]
    gate, trace = _FirstCallGate(), _RaceTrace()
    original_find_one = Collection.find_one
    original_find_one_and_update = Collection.find_one_and_update

    def observed_find_one(collection, filter=None, *args, **kwargs):
        result = original_find_one(collection, filter, *args, **kwargs)
        if (
            _target_collection(collection, db)
            and filter == {"_id": survey_id, "ownerId": before_survey["ownerId"]}
            and kwargs.get("session") is None
        ):
            trace.add("close_owned_read", status=result["status"], revision=result["revision"])
        return result

    def paused_find_one_and_update(collection, filter, update, *args, **kwargs):
        if (
            _target_collection(collection, db)
            and filter == {
                "_id": survey_id, "ownerId": before_survey["ownerId"],
                "revision": revision, "status": "published",
            }
            and kwargs.get("session") is None
            and isinstance(update, dict)
            and update.get("$set", {}).get("closeReason") == "manual"
        ):
            assert update["$inc"] == {"revision": 1}
            assert set(update) == {"$inc", "$set"}
            assert set(update["$set"]) == {"status", "closedAt", "closeReason", "updatedAt"}
            assert update["$set"]["status"] == "closed"
            trace.add("manual_close_write_wait", revision=revision)
            gate.pause_first()
            result = original_find_one_and_update(collection, filter, update, *args, **kwargs)
            trace.add("real_manual_close_cas_result", matched=result is not None)
            return result
        return original_find_one_and_update(collection, filter, update, *args, **kwargs)

    try:
        with monkeypatch.context() as patches:
            patches.setattr(Collection, "find_one", observed_find_one)
            patches.setattr(Collection, "find_one_and_update", paused_find_one_and_update)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_close = pool.submit(
                    api.post, f"/api/surveys/{survey['_id']}/close", headers=owner, json={"revision": revision},
                )
                try:
                    assert gate.entered.wait(timeout=5), "Close did not reach its manual CAS"
                    assert not pending_close.done()
                    answers = [helpers.answer("Committed race answer")]
                    helpers.assert_submitted(helpers.submit(api, filler, survey, attempt, answers))
                    committed_state = helpers.survey_state(
                        db, survey, responses=1, participations=0 if allow_repeat else 1, revision=revision + 1,
                    )
                    assert committed_state["survey"]["status"] == "published"
                    helpers.assert_submission_revision(db, before_survey, 1)
                    helpers.assert_stored_response(
                        committed_state["responses"][0], survey, attempt, answers,
                        None if anonymous else user["_id"],
                    )
                    if not allow_repeat:
                        helpers.assert_participation(committed_state["participations"][0], survey, user)
                    committed_snapshot = helpers.database_snapshot(db)
                    assert unrelated_records(committed_snapshot, survey) == unrelated_records(baseline, survey)
                    trace.add("submit_committed", httpStatus=201, status="published", revision=revision + 1)
                    assert not pending_close.done()
                finally:
                    gate.release.set()
                    stale_close = pending_close.result(timeout=20)

            helpers.assert_api_error(stale_close, 409, "REVISION_CONFLICT")
            assert helpers.database_snapshot(db) == committed_snapshot
            assert gate.calls == 1
            reads = [event for event in trace.events if event["stage"] == "close_owned_read"]
            assert reads == [{"stage": "close_owned_read", "status": "published", "revision": revision}]
            assert [event for event in trace.events if event["stage"] == "real_manual_close_cas_result"] == [
                {"stage": "real_manual_close_cas_result", "matched": False},
            ]
            trace.add("stale_close_rejected", httpStatus=409, errorCode="REVISION_CONFLICT", revision=revision + 1)

            # Read a genuinely fresh revision rather than deriving one for the request.
            fresh = api.get(f"/api/surveys/{survey['_id']}", headers=owner)
            assert fresh.status_code == 200 and fresh.json()["revision"] == revision + 1
            assert helpers.database_snapshot(db) == committed_snapshot
            close = api.post(
                f"/api/surveys/{survey['_id']}/close", headers=owner,
                json={"revision": fresh.json()["revision"]},
            )
            assert close.status_code == 200, close.text
            assert close.json()["status"] == "closed" and close.json()["revision"] == revision + 2
            closed_state = helpers.survey_state(
                db, survey, responses=1, participations=0 if allow_repeat else 1, revision=revision + 2,
            )
            _assert_manual_close(committed_state["survey"], closed_state["survey"])
            assert closed_state["responses"] == committed_state["responses"]
            assert closed_state["participations"] == committed_state["participations"]
            closed_snapshot = helpers.database_snapshot(db)
            assert unrelated_records(closed_snapshot, survey) == unrelated_records(baseline, survey)
            trace.add("fresh_manual_close_committed", httpStatus=200, status="closed", revision=revision + 2)

            helpers.assert_submitted(helpers.submit(api, filler, survey, attempt, answers))
            assert helpers.database_snapshot(db) == closed_snapshot
            trace.add(
                "closed_same_key_retry_preserved", httpStatus=201, responses=1,
                participations=0 if allow_repeat else 1, revision=revision + 2,
            )
    finally:
        gate.release.set()
        trace.record(record_testsuite_property, "submit_wins", anonymous, allow_repeat)
