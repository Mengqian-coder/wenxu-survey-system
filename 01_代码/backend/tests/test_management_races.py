"""Force actual snapshot/write conflicts to audit grant revocation ordering."""
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.errors import OperationFailure
import pytest

from test_api import register
from test_library_flow import new_question, ref, write
from test_question_sharing import grant


@pytest.mark.parametrize("new_use", ["binding", "copy"])
@pytest.mark.parametrize("revoke_first", [True, False])
def test_revoke_serializes_with_new_use_and_retries_actual_write_conflict(api, db, monkeypatch, new_use, revoke_first):
    _, owner = register(api, "owner")
    reader_user, reader = register(api, "reader")
    original = new_question(api, owner)
    qid, vid = original["question"]["_id"], original["version"]["_id"]
    shared = grant(api, owner, qid, "reader")
    sid = shared["share"]["_id"]
    entered, release, claimed = threading.Event(), threading.Event(), threading.Event()
    claim_lock = threading.Lock()
    conflicts = []
    real_write = Collection.find_one_and_update

    def gated_write(collection, query, update, *args, **kwargs):
        increments = update.get("$inc", {})
        is_new_use = increments == {"guardRevision": 1}
        is_revocation = "revision" in increments and "guardRevision" in increments
        selected = is_new_use if revoke_first else is_revocation
        if collection.database.name == db.name and collection.name == "questions" and query.get("_id") == ObjectId(qid):
            should_pause = False
            with claim_lock:
                if selected and not claimed.is_set():
                    claimed.set()
                    should_pause = True
            if should_pause:
                entered.set()
                assert release.wait(15), "Concurrent management request was not released"
            try:
                return real_write(collection, query, update, *args, **kwargs)
            except OperationFailure as error:
                if error.code == 112 or error.has_error_label("TransientTransactionError"):
                    conflicts.append(error.code)
                raise
        return real_write(collection, query, update, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", gated_write)

    def revoke_request():
        return api.post(f"/api/questions/{qid}/shares/{sid}/revoke", headers={**owner, "Idempotency-Key": str(uuid4())},
                        json={"revision": shared["questionRevision"]})

    def use_request():
        headers = {**reader, "Idempotency-Key": str(uuid4())}
        if new_use == "copy":
            return api.post(f"/api/questions/{qid}/copies", headers=headers, json={"versionId": vid})
        return api.post("/api/surveys", headers=headers, json={
            "title": "并发新增引用", "settings": {"anonymous": True}, "questions": [ref(original)],
        })

    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(use_request if revoke_first else revoke_request)
        assert entered.wait(15), "Expected master coordination write was not reached"
        try:
            winner = revoke_request() if revoke_first else use_request()
        finally:
            release.set()
        loser = delayed.result(20)
    assert conflicts, "The test must exercise a real MongoDB transaction conflict"
    if revoke_first:
        assert winner.status_code == 200, winner.text
        assert loser.status_code == 404, loser.text
        assert loser.json()["error"]["code"] == "NOT_FOUND"
        assert db.surveys.count_documents({}) == 0
        assert db.questions.count_documents({"ownerId": ObjectId(reader_user["_id"])}) == 0
    else:
        assert winner.status_code == 201, winner.text
        assert loser.status_code == 200, loser.text
        if new_use == "binding":
            saved = winner.json()
            assert write(api, reader, "POST", f"/api/surveys/{saved['_id']}/publish", {"revision": 1})["status"] == "published"
        else:
            saved = winner.json()
            assert api.get(f"/api/questions/{saved['question']['_id']}", headers=reader).status_code == 200
    assert db.question_shares.find_one({"_id": ObjectId(sid)})["revokedAt"] is not None
    assert api.get(f"/api/questions/{qid}", headers=reader).status_code == 404
