"""Narrative permission evidence through real HTTP and an isolated replica set.

The tape retains safe request/response and database evidence. No relationship,
survey, or answer is inserted directly: all business setup uses the public API.
"""

from functools import wraps

from pymongo.collection import Collection

from evidence_support import EvidenceTape
from test_api import register


COLLECTIONS = (
    "questions", "question_versions", "question_shares", "surveys", "responses",
    "participations", "mutation_receipts", "mutation_response_chunks",
)


def _content(title, kind="text"):
    value = {"type": kind, "title": title, "description": "权限执行证据", "validation": {}}
    if kind == "number":
        value["validation"] = {"integerOnly": True, "min": 0, "max": 150}
    return value


def _new_question(tape, actor, headers, title, kind="text"):
    return tape.request(actor, headers, "POST", "/api/questions",
                        {"content": _content(title, kind)}, expected=201).json()


def _ref(saved, instance="q1", required=True):
    return {"id": instance, "questionId": saved["question"]["_id"],
            "versionId": saved["version"]["_id"], "required": required, "jumpRules": []}


def _survey(tape, actor, headers, refs, title):
    return tape.request(actor, headers, "POST", "/api/surveys", {
        "title": title, "description": "权限执行证据",
        "settings": {"anonymous": True, "allowRepeat": True}, "questions": refs,
    }, expected=201).json()


def _publish(tape, actor, headers, survey):
    return tape.request(actor, headers, "POST", f"/api/surveys/{survey['_id']}/publish",
                        {"revision": survey["revision"]}).json()


def _fill(tape, actor, headers, survey, value, instance="q1"):
    path = f"/api/public/surveys/{survey['publicId']}"
    tape.request(actor, headers, "GET", path)
    started = tape.request(actor, headers, "POST", path + "/start").json()
    return tape.request(actor, headers, "POST", path + "/submit", {
        "submissionKey": started["submissionKey"], "submissionToken": started["submissionToken"],
        "answers": [{"questionId": instance, "value": value}],
    }, expected=201).json()


def _new_version(tape, actor, headers, saved, revision, title):
    return tape.request(actor, headers, "POST", f"/api/questions/{saved['question']['_id']}/versions", {
        "revision": revision, "baseVersionId": saved["version"]["_id"],
        "content": _content(title, saved["version"]["content"]["type"]),
    }, expected=201).json()


def _grant(tape, owner, saved, recipient_username, revision):
    return tape.request("owner", owner, "POST", f"/api/questions/{saved['question']['_id']}/shares", {
        "revision": revision, "recipientUsername": recipient_username,
    }, expected=201).json()


def _revoke(tape, owner, saved, share, revision):
    return tape.request("owner", owner, "POST",
                        f"/api/questions/{saved['question']['_id']}/shares/{share['share']['_id']}/revoke",
                        {"revision": revision}).json()


def _denied(tape, actor, headers, method, path, body=None):
    response = tape.request(actor, headers, method, path, body, expected=404)
    tape.check(f"{actor}: {method} {path} 使用统一拒绝码",
               response.json().get("error", {}).get("code") == "NOT_FOUND",
               response.json())
    return response


def _raw_state(db):
    """Exact BSON document comparisons complement the redacted evidence tape."""
    return {name: list(db[name].find({}).sort("_id", 1)) for name in COLLECTIONS}


def _unchanged(tape, db, label, before):
    after = _raw_state(db)
    tape.database(label, COLLECTIONS)
    for name in COLLECTIONS:
        tape.check(f"{label}: {name} 文档和数量完全不变", before[name] == after[name],
                   {"beforeCount": len(before[name]), "afterCount": len(after[name]),
                    "documentsEqual": before[name] == after[name]})


def test_permission_evidence_whole_question_grant_and_management_boundary(api, db):
    """P01: historical/future use is granted; management and private data are not."""
    _, owner = register(api, "p01_owner")
    _, recipient = register(api, "p01_recipient")
    _, stranger = register(api, "p01_stranger")
    tape = EvidenceTape("P01", "整题授权覆盖历史和未来，管理及他人数据仍拒绝", api, db)
    try:
        first = _new_question(tape, "owner", owner, "P01 历史版本 1")
        qid, v1 = first["question"]["_id"], first["version"]["_id"]
        second = _new_version(tape, "owner", owner, first, 1, "P01 授权前版本 2")
        shared = _grant(tape, owner, first, "p01_recipient", 2)
        third = _new_version(tape, "owner", owner, second, shared["questionRevision"],
                             "P01 授权后版本 3")
        current_revision = third["question"]["revision"]
        listing = tape.request("recipient", recipient, "GET", f"/api/questions/{qid}/versions").json()
        tape.check("授权同时覆盖两个历史版本和一个未来版本",
                   [row["versionNo"] for row in listing["items"]] == [3, 2, 1], listing)
        for saved in (first, second, third):
            vid = saved["version"]["_id"]
            tape.request("recipient", recipient, "GET", f"/api/questions/{qid}/versions/{vid}")
            survey = _survey(tape, "recipient", recipient, [_ref(saved)],
                             f"P01 引用版本 {saved['version']['versionNo']}")
            tape.check("共享版本可以保存为本人问卷快照",
                       survey["questions"][0]["versionId"] == vid, survey["questions"][0])
        private = _publish(tape, "owner", owner,
                           _survey(tape, "owner", owner, [_ref(first)], "P01 OWNER_PRIVATE_SURVEY"))
        _fill(tape, "stranger", stranger, private, "P01 OWNER_PRIVATE_ANSWER")
        tape.database("拒绝操作前的完整业务快照", COLLECTIONS)
        before = _raw_state(db)
        for actor, headers in (("recipient", recipient), ("stranger", stranger)):
            if actor == "stranger":
                _denied(tape, actor, headers, "GET", f"/api/questions/{qid}")
                _denied(tape, actor, headers, "GET", f"/api/questions/{qid}/versions/{v1}")
            for suffix in ("shares", "usage"):
                _denied(tape, actor, headers, "GET", f"/api/questions/{qid}/{suffix}")
            for method, suffix, body in (
                ("POST", "/versions", {"revision": current_revision, "baseVersionId": v1,
                                         "content": _content("P01 UNAUTHORIZED_EDIT")}),
                ("POST", "/restore", {"revision": current_revision, "sourceVersionId": v1}),
                ("PATCH", "", {"revision": current_revision, "archived": True}),
                ("PATCH", f"/versions/{v1}", {"revision": current_revision, "archived": True}),
                ("POST", "/shares", {"revision": current_revision, "recipientUsername": "p01_stranger"}),
                ("POST", f"/shares/{shared['share']['_id']}/revoke", {"revision": current_revision}),
            ):
                _denied(tape, actor, headers, method, f"/api/questions/{qid}" + suffix, body)
            for suffix in ("", "/responses", "/statistics"):
                _denied(tape, actor, headers, "GET", f"/api/surveys/{private['_id']}" + suffix)
        _unchanged(tape, db, "所有越权请求后的完整业务快照", before)
    finally:
        tape.finish()


def test_permission_evidence_revoked_saved_draft_and_independent_copy(api, db):
    """P02: revocation blocks new source uses but preserves saved local workflow."""
    _, owner = register(api, "p02_owner")
    recipient_user, recipient = register(api, "p02_recipient")
    _, stranger = register(api, "p02_stranger")
    tape = EvidenceTape("P02", "撤权阻止新增引用，已存草稿和独立副本继续工作", api, db)
    try:
        first = _new_question(tape, "owner", owner, "P02 已保存的共享题")
        qid, vid = first["question"]["_id"], first["version"]["_id"]
        second = _new_version(tape, "owner", owner, first, 1, "P02 不能在撤权后切入的版本")
        share = _grant(tape, owner, first, "p02_recipient", 2)
        draft = _survey(tape, "recipient", recipient, [_ref(first)], "P02 保留历史引用的草稿")
        removable = _survey(tape, "recipient", recipient, [_ref(first)], "P02 删除后不可重加")
        empty = _survey(tape, "recipient", recipient, [], "P02 不能新增实例的草稿")
        copied = tape.request("recipient", recipient, "POST", f"/api/questions/{qid}/copies",
                              {"versionId": vid}, expected=201).json()
        tape.check("另存副本拥有独立题目身份和所有者",
                   copied["question"]["_id"] != qid and copied["question"]["ownerId"] == recipient_user["_id"],
                   copied["question"])
        revoked = _revoke(tape, owner, first, share, share["questionRevision"])
        tape.check("授权关系已显式撤销", revoked["share"]["revokedAt"] is not None, revoked)
        tape.database("撤权完成后的基线", COLLECTIONS)
        before = _raw_state(db)
        for suffix in ("", "/versions", f"/versions/{vid}"):
            _denied(tape, "recipient", recipient, "GET", f"/api/questions/{qid}" + suffix)
        _denied(tape, "recipient", recipient, "POST", f"/api/questions/{qid}/copies", {"versionId": vid})
        _denied(tape, "recipient", recipient, "POST", "/api/surveys", {
            "title": "P02 不允许的新引用", "description": "",
            "settings": {"anonymous": True, "allowRepeat": True}, "questions": [_ref(first)],
        })
        _denied(tape, "recipient", recipient, "PATCH", f"/api/surveys/{draft['_id']}", {
            "revision": draft["revision"], "questions": [_ref(second)],
        })
        _denied(tape, "recipient", recipient, "PATCH", f"/api/surveys/{draft['_id']}", {
            "revision": draft["revision"], "questions": [_ref(first, "new_instance")],
        })
        _denied(tape, "recipient", recipient, "PATCH", f"/api/surveys/{empty['_id']}", {
            "revision": empty["revision"], "questions": [_ref(first, "new_instance")],
        })
        _unchanged(tape, db, "新增引用、换版、另存和新增实例全部拒绝后的快照", before)
        removed = tape.request("recipient", recipient, "PATCH", f"/api/surveys/{removable['_id']}", {
            "revision": removable["revision"], "questions": [],
        }).json()
        tape.database("删除已有实例后的基线", COLLECTIONS)
        removed_before = _raw_state(db)
        _denied(tape, "recipient", recipient, "PATCH", f"/api/surveys/{removable['_id']}", {
            "revision": removed["revision"], "questions": [_ref(first)],
        })
        _unchanged(tape, db, "删后重加被拒绝后的快照", removed_before)
        updated = tape.request("recipient", recipient, "PATCH", f"/api/surveys/{draft['_id']}", {
            "revision": draft["revision"], "questions": [_ref(first, required=False)],
        }).json()
        tape.check("旧实例可修改卷内必答且保留原版本",
                   updated["questions"][0]["required"] is False and updated["questions"][0]["versionId"] == vid,
                   updated["questions"][0])
        published = _publish(tape, "recipient", recipient, updated)
        _fill(tape, "stranger", stranger, published, "P02_RETAINED_SNAPSHOT_ANSWER")
        stats = tape.request("recipient", recipient, "GET", f"/api/surveys/{draft['_id']}/statistics").json()
        tape.check("撤权后的旧草稿仍可发布、填写和统计", stats["totalResponses"] == 1, stats)
        version_stats = tape.request("recipient", recipient, "GET",
                                    f"/api/question-statistics/{qid}/versions/{vid}").json()
        tape.check("撤权后的历史引用仍支持本人跨问卷统计",
                   version_stats["totalResponses"] == version_stats["answeredCount"] == 1, version_stats)
        copy_id = copied["question"]["_id"]
        tape.request("recipient", recipient, "GET", f"/api/questions/{copy_id}")
        copy_share = tape.request("recipient", recipient, "POST", f"/api/questions/{copy_id}/shares", {
            "revision": 1, "recipientUsername": "p02_stranger",
        }, expected=201).json()
        tape.request("stranger", stranger, "GET", f"/api/questions/{copy_id}")
        _denied(tape, "stranger", stranger, "GET", f"/api/questions/{qid}")
        tape.check("撤权不影响副本继续共享", copy_share["share"]["revokedAt"] is None, copy_share)
        tape.database("旧草稿完成填写且副本重新共享后的结果", COLLECTIONS)
    finally:
        tape.finish()


def test_permission_evidence_usage_privacy_and_source_free_statistics(api, db, monkeypatch):
    """P03: source ownership does not grant private titles, answers, or statistics."""
    _, owner = register(api, "p03_owner")
    _, recipient = register(api, "p03_recipient")
    _, stranger = register(api, "p03_stranger")
    tape = EvidenceTape("P03", "引用仅透露其他问卷数量，撤权后统计直接读取本人快照", api, db)
    try:
        saved = _new_question(tape, "owner", owner, "P03 历史数值题", "number")
        qid, vid = saved["question"]["_id"], saved["version"]["_id"]
        share = _grant(tape, owner, saved, "p03_recipient", 1)
        own = _publish(tape, "owner", owner,
                       _survey(tape, "owner", owner, [_ref(saved, "owner_instance")], "P03 OWNER_VISIBLE_SURVEY"))
        other = _publish(tape, "recipient", recipient,
                         _survey(tape, "recipient", recipient, [_ref(saved, "recipient_instance")],
                                 "P03 RECIPIENT_PRIVATE_SURVEY"))
        _fill(tape, "stranger", stranger, own, 20, "owner_instance")
        _fill(tape, "stranger", stranger, other, 80, "recipient_instance")
        _revoke(tape, owner, saved, share, share["questionRevision"])
        tape.database("双方分别产生答卷且撤权后的基线", COLLECTIONS)
        before = _raw_state(db)
        usage = tape.request("owner", owner, "GET", f"/api/questions/{qid}/usage").json()
        tape.check("引用查询仅暴露自己的明细和其他问卷数量",
                   usage["totalSurveyCount"] == 2 and usage["visibleSurveyCount"] == usage["otherSurveyCount"] == 1
                   and [row["surveyId"] for row in usage["items"]] == [own["_id"]], usage)
        tape.check("引用查询没有接收者的标题、问卷标识或答卷",
                   other["title"] not in str(usage) and other["_id"] not in str(usage)
                   and "answers" not in str(usage), usage)
        for suffix in ("", "/responses", "/statistics"):
            _denied(tape, "owner", owner, "GET", f"/api/surveys/{other['_id']}" + suffix)
        _denied(tape, "recipient", recipient, "GET", f"/api/questions/{qid}")
        shared_directory = tape.request("recipient", recipient, "GET", "/api/questions?scope=shared").json()
        tape.check("共享题目录已消失，历史统计无需从此进入", shared_directory["total"] == 0, shared_directory)
        root = f"/api/question-statistics/{qid}/versions/{vid}"
        source_reads = []
        source_collections = {"questions", "question_versions", "question_shares"}

        def watch(original, operation):
            @wraps(original)
            def observed(collection, *args, **kwargs):
                if collection.database.name == db.name and collection.name in source_collections:
                    source_reads.append({"collection": collection.name, "operation": operation})
                return original(collection, *args, **kwargs)
            return observed

        with monkeypatch.context() as probe:
            for operation in ("find_one", "find", "aggregate", "count_documents", "distinct"):
                probe.setattr(Collection, operation, watch(getattr(Collection, operation), operation))
            directory = tape.request("recipient", recipient, "GET", "/api/question-statistics").json()
            versions = tape.request("recipient", recipient, "GET", f"/api/question-statistics/{qid}/versions").json()
            own_stats = tape.request("recipient", recipient, "GET", root).json()
            values = tape.request("recipient", recipient, "GET", root + "/values").json()
        tape.check("撤权后可从独立统计目录找到历史题目",
                   directory["total"] == 1 and directory["items"][0]["questionId"] == qid, directory)
        tape.check("统计版本目录取自本人的已保存引用",
                   versions["total"] == 1 and versions["items"][0]["versionId"] == vid, versions)
        tape.check("接收者只统计自己的回答 80",
                   own_stats["totalResponses"] == own_stats["answeredCount"] == 1
                   and own_stats["average"] == 80 and values["items"] == [{"value": 80}],
                   {"statistics": own_stats, "values": values})
        tape.check("四个统计入口完全没有读取原题、版本或共享关系", source_reads == [],
                   {"watchedCollections": sorted(source_collections),
                    "watchedOperations": ["find_one", "find", "aggregate", "count_documents", "distinct"],
                    "sourceReadCount": len(source_reads), "sourceReads": source_reads})
        owner_stats = tape.request("owner", owner, "GET", root).json()
        tape.check("原题所有者也只统计自己的回答 20",
                   owner_stats["totalResponses"] == 1 and owner_stats["average"] == 20, owner_stats)
        for suffix in ("", "/values"):
            _denied(tape, "stranger", stranger, "GET", root + suffix)
        _unchanged(tape, db, "所有权限读取和统计查询后业务文档不变", before)
    finally:
        tape.finish()
