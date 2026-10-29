"""Cross-survey statistics contract on real, isolated MongoDB and HTTP flows.

Library grants authorize future reuse, while statistics authorize against the
caller's saved survey snapshots.  These tests keep those two boundaries separate.
"""

from datetime import datetime, timedelta, timezone
import math

from bson import BSON, ObjectId
import pytest

from test_api import answer, assert_api_error, database_snapshot, register, start, submit
from test_library_flow import content, new_question, new_survey, ref, write


ROOT = "/api/question-statistics"
COMMON = {
    "questionId", "versionId", "versionNo", "title", "type", "surveyCount",
    "totalResponses", "answeredCount", "unansweredCount",
}


def ids(saved):
    return saved["question"]["_id"], saved["version"]["_id"]


def version_path(saved):
    qid, vid = ids(saved)
    return f"{ROOT}/{qid}/versions/{vid}"


def get_stats(api, user, saved):
    result = api.get(version_path(saved), headers=user)
    assert result.status_code == 200, result.text
    data = result.json()
    expected = COMMON.copy()
    if data["type"] in {"single", "multiple"}:
        expected |= {"options"}
        for option in data["options"]:
            assert set(option) == {"optionId", "label", "count", "ratio"}
    elif data["type"] == "number":
        expected |= {"minimum", "maximum", "average"}
    assert set(data) == expected
    assert (data["questionId"], data["versionId"]) == ids(saved)
    assert data["unansweredCount"] == data["totalResponses"] - data["answeredCount"]
    return data


def collect(api, filler, survey, values):
    begun = start(api, filler, survey)
    result = submit(api, filler, survey, begun, [answer(value, instance) for instance, value in values.items()])
    assert result.status_code == 201, result.text
    return begun


def publish(api, user, survey):
    return write(api, user, "POST", f"/api/surveys/{survey['_id']}/publish", {"revision": survey["revision"]})


def choice_content(kind="multiple", title="共同的选择题"):
    return content(title, kind)


def unrestricted_number(title="数值"):
    return {"type": "number", "title": title, "description": "", "validation": {"integerOnly": False}}


def test_exact_version_merges_local_ids_and_counts_same_person_per_submission(api):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner)
    a = publish(api, owner, new_survey(api, owner, [ref(saved, "surveyA")]))
    b = publish(api, owner, new_survey(api, owner, [ref(saved, "surveyB")]))
    draft = new_survey(api, owner, [ref(saved, "notPublished")])
    collect(api, filler, a, {"surveyA": 20})
    collect(api, filler, b, {"surveyB": 40})
    # Starting another fill produces no submitted response and must not count.
    start(api, filler, b)
    current = api.get(f"/api/surveys/{a['_id']}", headers=owner).json()
    write(api, owner, "POST", f"/api/surveys/{a['_id']}/close", {"revision": current["revision"]})
    stats = get_stats(api, owner, saved)
    assert stats["surveyCount"] == 3  # closed, published, and draft references
    assert (stats["totalResponses"], stats["answeredCount"], stats["unansweredCount"]) == (2, 2, 0)
    assert (stats["minimum"], stats["maximum"], stats["average"]) == (20, 40, 30)
    assert draft["status"] == "draft"
    listing = api.get(ROOT, headers=owner).json()
    assert listing["items"] == [{"questionId": ids(saved)[0], "title": "年龄", "displayVersionId": ids(saved)[1],
                                 "displayVersionNo": 1, "surveyCount": 3, "usedVersionCount": 1}]


def test_new_restored_and_copied_versions_are_never_merged(api):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    first = new_question(api, owner)
    qid, first_vid = ids(first)
    second = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": first_vid, "content": content("第二版年龄"),
    }, 201)
    restored = write(api, owner, "POST", f"/api/questions/{qid}/restore", {
        "revision": 2, "sourceVersionId": first_vid,
    }, 201)
    copied = write(api, owner, "POST", f"/api/questions/{qid}/copies", {"versionId": first_vid}, 201)
    for saved, value in [(first, 10), (second, 20), (restored, 30), (copied, 40)]:
        survey = publish(api, owner, new_survey(api, owner, [ref(saved)]))
        collect(api, filler, survey, {"q1": value})
        result = get_stats(api, owner, saved)
        assert (result["surveyCount"], result["totalResponses"], result["answeredCount"], result["average"]) == (1, 1, 1, value)
    versions = api.get(f"{ROOT}/{qid}/versions", headers=owner).json()
    assert [item["versionId"] for item in versions["items"]] == [ids(restored)[1], ids(second)[1], first_vid]
    assert versions["total"] == 3
    listing = api.get(ROOT, headers=owner).json()
    assert listing["total"] == 2
    original_item = next(item for item in listing["items"] if item["questionId"] == qid)
    assert original_item["usedVersionCount"] == 3 and original_item["surveyCount"] == 3


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
def test_saved_reference_without_responses_returns_zero_and_null(api, kind):
    _, owner = register(api, "owner")
    saved = new_question(api, owner, content("尚无回答", kind))
    new_survey(api, owner, [ref(saved)])
    stats = get_stats(api, owner, saved)
    assert stats["surveyCount"] == 1
    assert (stats["totalResponses"], stats["answeredCount"], stats["unansweredCount"]) == (0, 0, 0)
    if kind in {"single", "multiple"}:
        assert all(option["count"] == 0 and option["ratio"] is None for option in stats["options"])
    elif kind == "number":
        assert stats["minimum"] is stats["maximum"] is stats["average"] is None
    else:
        values = api.get(version_path(saved) + "/values", headers=owner).json()
        assert values["total"] == 0 and values["items"] == []


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
def test_null_and_skipped_answers_share_unanswered_bucket(api, kind):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    gate = new_question(api, owner, content("分支", "single"))
    target = new_question(api, owner, content("目标题", kind))
    survey = publish(api, owner, new_survey(api, owner, [
        ref(gate, "gate", jumpRules=[{"id": "skip", "condition": {"operator": "eq", "value": "no"},
                                     "target": {"kind": "end"}}]),
        ref(target, "target", required=False),
    ]))
    collect(api, filler, survey, {"gate": "no"})
    collect(api, filler, survey, {"gate": "yes", "target": None})
    valid_value = {"single": "yes", "multiple": ["yes", "no"], "text": "真实回答", "number": 25}[kind]
    collect(api, filler, survey, {"gate": "yes", "target": valid_value})
    stats = get_stats(api, owner, target)
    assert (stats["totalResponses"], stats["answeredCount"], stats["unansweredCount"]) == (3, 1, 2)
    if kind in {"single", "multiple"}:
        counts = {option["optionId"]: (option["count"], option["ratio"]) for option in stats["options"]}
        assert counts["yes"] == (1, 1)
        assert counts["no"] == ((1, 1) if kind == "multiple" else (0, 0))
        if kind == "multiple":
            assert sum(option["ratio"] for option in stats["options"]) == 2
    if kind == "number":
        assert stats["minimum"] == stats["maximum"] == stats["average"] == 25
    if kind in {"text", "number"}:
        values = api.get(version_path(target) + "/values", headers=owner).json()
        assert values["total"] == 1 and values["items"] == [{"value": valid_value}]


def test_option_ratios_use_answered_count_across_surveys(api):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner, choice_content())
    a = publish(api, owner, new_survey(api, owner, [ref(saved, required=False)]))
    b = publish(api, owner, new_survey(api, owner, [ref(saved, "otherId", required=False)]))
    for survey, instance, value in [(a, "q1", ["yes", "no"]), (a, "q1", ["yes"]),
                                    (b, "otherId", ["no"]), (b, "otherId", None)]:
        collect(api, filler, survey, {instance: value})
    stats = get_stats(api, owner, saved)
    assert (stats["totalResponses"], stats["answeredCount"]) == (4, 3)
    assert [option["count"] for option in stats["options"]] == [2, 2]
    assert [option["ratio"] for option in stats["options"]] == pytest.approx([2 / 3, 2 / 3])


@pytest.mark.parametrize("numbers, expected", [
    ([1.7e308, 1.7e308], 1.7e308),
    ([-1.7e308, -1.7e308], -1.7e308),
    ([1.7e308, -1.7e308], 0),
    ([0, 1.5, -2.5], -1 / 3),
])
def test_numeric_aggregates_remain_finite_for_supported_double_boundaries(api, numbers, expected):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner, unrestricted_number())
    survey = publish(api, owner, new_survey(api, owner, [ref(saved)]))
    for value in numbers:
        collect(api, filler, survey, {"q1": value})
    stats = get_stats(api, owner, saved)
    assert stats["minimum"] == min(numbers) and stats["maximum"] == max(numbers)
    assert math.isfinite(stats["average"])
    assert stats["average"] == pytest.approx(expected)


@pytest.mark.parametrize("kind, values", [("text", ["第一份", "第二份", "第三份"]), ("number", [10, 20, 30])])
def test_values_paginate_by_submitted_time_then_response_id_and_expose_only_value(api, db, kind, values):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner, content("原值分页", kind))
    a = publish(api, owner, new_survey(api, owner, [ref(saved, "a")]))
    b = publish(api, owner, new_survey(api, owner, [ref(saved, "b")]))
    submissions = []
    for survey, instance, value in [(a, "a", values[0]), (b, "b", values[1]), (a, "a", values[2])]:
        submissions.append(collect(api, filler, survey, {instance: value}))
    documents = [db.responses.find_one({"submissionKey": value["submissionKey"]}) for value in submissions]
    # The HTTP flow creates every response. Adjust timestamps only to make the
    # tied-time secondary ordering deterministic without relying on wall time.
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for doc in documents[:2]:
        db.responses.update_one({"_id": doc["_id"]}, {"$set": {"submittedAt": moment}})
    db.responses.update_one({"_id": documents[2]["_id"]}, {"$set": {"submittedAt": moment - timedelta(seconds=1)}})
    tie_order = sorted(range(2), key=lambda index: documents[index]["_id"], reverse=True)
    expected = [values[index] for index in tie_order] + [values[2]]
    received = []
    for page in range(1, 5):
        result = api.get(version_path(saved) + f"/values?page={page}&pageSize=1", headers=owner)
        assert result.status_code == 200, result.text
        data = result.json()
        assert set(data) == {"questionId", "versionId", "type", "items", "total", "page", "pageSize"}
        assert (data["questionId"], data["versionId"], data["type"]) == (*ids(saved), kind)
        assert (data["total"], data["page"], data["pageSize"]) == (3, page, 1)
        assert all(set(item) == {"value"} for item in data["items"])
        received.extend(item["value"] for item in data["items"])
    assert received == expected
    assert get_stats(api, owner, saved)["answeredCount"] == 3


@pytest.mark.parametrize("kind", ["single", "multiple"])
def test_choice_values_endpoint_rejects_with_explicit_conflict(api, kind):
    _, owner = register(api, "owner")
    saved = new_question(api, owner, content("选项统计", kind))
    new_survey(api, owner, [ref(saved)])
    assert_api_error(api.get(version_path(saved) + "/values", headers=owner), 409, "STATISTIC_VALUES_UNSUPPORTED")


def test_source_owner_and_recipient_do_not_gain_statistics_without_own_reference(api):
    _, source = register(api, "source")
    _, recipient = register(api, "recipient")
    _, unused = register(api, "unused")
    saved = new_question(api, source, content("共享的内部题目", "text"))
    qid, _ = ids(saved)
    shared = write(api, source, "POST", f"/api/questions/{qid}/shares", {
        "revision": 1, "recipientUsername": "recipient",
    }, 201)
    write(api, source, "POST", f"/api/questions/{qid}/shares", {
        "revision": shared["questionRevision"], "recipientUsername": "unused",
    }, 201)
    survey = new_survey(api, recipient, [ref(saved)])
    write(api, recipient, "PATCH", f"/api/surveys/{survey['_id']}", {
        "revision": survey["revision"], "title": "CANARY_PRIVATE_SURVEY_TITLE",
    })
    for actor in [source, unused]:
        directory = api.get(ROOT, headers=actor)
        assert directory.status_code == 200 and directory.json()["total"] == 0
        for path in [f"{ROOT}/{qid}/versions", version_path(saved), version_path(saved) + "/values"]:
            denied = api.get(path, headers=actor)
            assert_api_error(denied, 404, "NOT_FOUND")
            assert "CANARY_PRIVATE" not in denied.text and "共享的内部题目" not in denied.text
    assert get_stats(api, recipient, saved)["surveyCount"] == 1


def test_shared_statistics_survive_revoke_archive_and_missing_library_records(api, db):
    _, source = register(api, "source")
    _, recipient = register(api, "recipient")
    _, filler = register(api, "filler")
    saved = new_question(api, source, content("历史快照题干", "single"))
    qid, vid = ids(saved)
    shared = write(api, source, "POST", f"/api/questions/{qid}/shares", {
        "revision": 1, "recipientUsername": "recipient",
    }, 201)
    own_survey = publish(api, recipient, new_survey(api, recipient, [ref(saved, "recipientLocal")]))
    source_survey = publish(api, source, new_survey(api, source, [ref(saved, "sourceLocal")]))
    collect(api, filler, own_survey, {"recipientLocal": "yes"})
    collect(api, filler, source_survey, {"sourceLocal": "no"})
    revoked = write(api, source, "POST", f"/api/questions/{qid}/shares/{shared['share']['_id']}/revoke", {
        "revision": shared["questionRevision"],
    })
    archived = write(api, source, "PATCH", f"/api/questions/{qid}/versions/{vid}", {
        "revision": revoked["questionRevision"], "archived": True,
    })
    write(api, source, "PATCH", f"/api/questions/{qid}", {"revision": archived["questionRevision"], "archived": True})
    unchanged = database_snapshot(db)
    assert_api_error(api.get(f"/api/questions/{qid}", headers=recipient), 404, "NOT_FOUND")
    assert database_snapshot(db) == unchanged
    before = get_stats(api, recipient, saved)
    assert before["title"] == "历史快照题干" and before["totalResponses"] == 1
    assert [item["count"] for item in before["options"]] == [1, 0]
    # Fault injection only in this fixture's isolated DB proves the statistics
    # reader does not depend on current library records or accidentally regrant.
    db.questions.delete_one({"_id": ObjectId(qid)})
    db.question_versions.delete_one({"_id": ObjectId(vid)})
    db.question_shares.delete_many({"questionId": ObjectId(qid)})
    assert get_stats(api, recipient, saved) == before
    listing = api.get(ROOT, headers=recipient).json()
    assert listing["items"][0]["title"] == "历史快照题干"
    versions = api.get(f"{ROOT}/{qid}/versions", headers=recipient).json()
    assert versions["items"][0]["title"] == "历史快照题干"


def test_directory_uses_highest_used_snapshot_searches_literally_and_paginates(api):
    _, owner = register(api, "owner")
    a = new_question(api, owner, content("Old draft", "text"))
    b = new_question(api, owner, content("Other", "text"))
    qid, vid = ids(a)
    a2 = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": 1, "baseVersionId": vid, "content": content("Used [literal].*", "text"),
    }, 201)
    unseen = write(api, owner, "POST", f"/api/questions/{qid}/versions", {
        "revision": 2, "baseVersionId": ids(a2)[1], "content": content("CANARY_UNREFERENCED_CURRENT", "text"),
    }, 201)
    new_survey(api, owner, [ref(a)])
    new_survey(api, owner, [ref(a2)])
    new_survey(api, owner, [ref(b)])
    listing = api.get(ROOT, headers=owner).json()
    assert listing["total"] == 2
    assert [item["questionId"] for item in listing["items"]] == sorted([qid, ids(b)[0]])
    item = next(item for item in listing["items"] if item["questionId"] == qid)
    assert item == {"questionId": qid, "title": "Used [literal].*", "displayVersionId": ids(a2)[1],
                    "displayVersionNo": 2, "surveyCount": 2, "usedVersionCount": 2}
    for search, expected_count in [("[literal].*", 1), ("literal", 1), ("Used", 1), ("Old draft", 0),
                                   ("CANARY_UNREFERENCED_CURRENT", 0), ("^.*$", 0)]:
        result = api.get(ROOT, headers=owner, params={"search": search, "pageSize": 1})
        assert result.status_code == 200, result.text
        assert result.json()["total"] == expected_count
    first_page = api.get(ROOT + "?page=1&pageSize=1", headers=owner).json()
    second_page = api.get(ROOT + "?page=2&pageSize=1", headers=owner).json()
    assert (first_page["total"], first_page["page"], first_page["pageSize"]) == (2, 1, 1)
    assert first_page["items"] + second_page["items"] == listing["items"]
    versions = [api.get(f"{ROOT}/{qid}/versions?page={page}&pageSize=1", headers=owner).json() for page in [1, 2, 3]]
    assert [item["versionId"] for page in versions for item in page["items"]] == [ids(a2)[1], vid]
    assert all(page["total"] == 2 for page in versions)
    assert all(set(item) == {"questionId", "versionId", "versionNo", "title", "type", "surveyCount"}
               for page in versions for item in page["items"])
    assert_api_error(api.get(version_path(unseen), headers=owner), 404, "NOT_FOUND")


def test_legacy_v1_surveys_keep_existing_statistics_but_do_not_enter_cross_survey_directory(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner)
    survey = new_survey(api, owner, [ref(saved)])
    # v1 documents cannot be newly written by the v2 API. Convert this isolated
    # fixture to the exact supported legacy storage shape to exercise coexistence.
    snapshot = db.surveys.find_one({"_id": ObjectId(survey["_id"])})["questions"][0]
    for key in ("questionId", "versionId", "versionNo"):
        snapshot.pop(key)
    db.surveys.update_one({"_id": ObjectId(survey["_id"])}, {"$set": {"schemaVersion": 1, "questions": [snapshot]}})
    published = publish(api, owner, survey)
    collect(api, filler, published, {"q1": 20})
    listing = api.get(ROOT, headers=owner).json()
    assert listing["total"] == 0 and listing["items"] == []
    assert_api_error(api.get(version_path(saved), headers=owner), 404, "NOT_FOUND")
    old_stats = api.get(f"/api/surveys/{survey['_id']}/statistics", headers=owner)
    assert old_stats.status_code == 200 and old_stats.json()["totalResponses"] == 1


def test_large_history_streams_lookup_without_building_a_16mib_response_array(api, db):
    _, owner = register(api, "owner")
    _, filler = register(api, "filler")
    saved = new_question(api, owner, content("大历史文本", "text"))
    survey = publish(api, owner, new_survey(api, owner, [ref(saved)]))
    value = "x" * 10000
    begun = collect(api, filler, survey, {"q1": value})
    original = db.responses.find_one({"submissionKey": begun["submissionKey"]})
    # The first canonical response is validated through HTTP. Bulk-clone its
    # schema-valid historical rows only inside this isolated DB so this query
    # regression test does not spend minutes issuing 1,800 unrelated HTTP writes.
    # Each response is tiny; their combined selected-answer payload exceeds the
    # BSON limit and would fail if $lookup materialized one entire-history array.
    count = 1800
    assert len(BSON.encode(original)) * (count + 1) > 16 * 1024 * 1024
    db.responses.insert_many([
        {**original, "_id": ObjectId(), "submissionKey": f"{index + 1:064x}"}
        for index in range(count)
    ])
    stats = get_stats(api, owner, saved)
    assert (stats["totalResponses"], stats["answeredCount"], stats["unansweredCount"]) == (count + 1, count + 1, 0)
    result = api.get(version_path(saved) + "/values?pageSize=1", headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()["total"] == count + 1
    assert result.json()["items"] == [{"value": value}]


@pytest.mark.parametrize("suffix", ["", "/versions", "/statistics", "/values"])
def test_statistics_require_authentication(api, db, suffix):
    oid = str(ObjectId())
    paths = {"": ROOT, "/versions": f"{ROOT}/{oid}/versions",
             "/statistics": f"{ROOT}/{oid}/versions/{oid}", "/values": f"{ROOT}/{oid}/versions/{oid}/values"}
    before = database_snapshot(db)
    response = api.get(paths[suffix])
    assert_api_error(response, 401, "AUTH_REQUIRED")
    assert database_snapshot(db) == before


@pytest.mark.parametrize("query", ["page=0", "page=-1", "pageSize=0", "pageSize=101", "page=1.5"])
def test_directory_validates_paging(api, db, query):
    _, owner = register(api, "owner")
    before = database_snapshot(db)
    assert_api_error(api.get(ROOT + "?" + query, headers=owner), 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before


def test_unknown_or_mismatched_question_and_version_are_indistinguishable(api, db):
    _, owner = register(api, "owner")
    a = new_question(api, owner, content("自己的题目", "text"))
    b = new_question(api, owner, content("另一题", "text"))
    new_survey(api, owner, [ref(a), ref(b, "b")])
    before = database_snapshot(db)
    for qid, vid in [(ids(a)[0], ids(b)[1]), (str(ObjectId()), ids(a)[1]), (ids(a)[0], str(ObjectId())),
                     ("bad", ids(a)[1]), (ids(a)[0], "bad")]:
        for suffix in ["", "/values"]:
            assert_api_error(api.get(f"{ROOT}/{qid}/versions/{vid}{suffix}", headers=owner), 404, "NOT_FOUND")
            assert database_snapshot(db) == before
