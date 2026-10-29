"""Immutable library versions and trusted, pinned survey references."""
from copy import deepcopy
import re

from bson import ObjectId
from pymongo import ReturnDocument

from .domain import validate_questions
from .errors import APIError
from .service import ensure_size, object_id, serialize, utcnow


CONTENT_FIELDS = ("type", "title", "description", "validation", "options")
INSTANCE_FIELDS = ("id", "required", "jumpRules")
REFERENCE_FIELDS = ("questionId", "versionId", "versionNo")


def answer_definition(questions):
    """Project trusted snapshots onto the existing answer/routing contract."""
    return [{k: deepcopy(v) for k, v in q.items() if k not in REFERENCE_FIELDS} for q in questions]


def validate_snapshot(questions, publish=False):
    """Validate existing domain fields without exposing refs to the old DTO."""
    result = validate_questions(answer_definition(questions), publish=publish)
    for original, normalized in zip(questions, result):
        normalized.update({k: original[k] for k in REFERENCE_FIELDS if k in original})
    return result


def normalize_content(content):
    value = content.model_dump(exclude_none=True) if hasattr(content, "model_dump") else deepcopy(content)
    normalized = validate_questions([{**value, "id": "library", "required": False, "jumpRules": []}])[0]
    return {k: v for k, v in normalized.items() if k in CONTENT_FIELDS}


class QuestionService:
    def __init__(self, db):
        self.db = db

    def _question(self, question_id, user, session=None, owner=False):
        question = self.db.questions.find_one({"_id": object_id(question_id)}, session=session)
        allowed = question and question["ownerId"] == user["_id"]
        if question and not allowed and not owner:
            allowed = self.db.question_shares.find_one({
                "questionId": question["_id"], "recipientId": user["_id"], "revokedAt": None,
            }, session=session)
        if not allowed:
            raise APIError(404, "NOT_FOUND", "题目不存在")
        return question

    def _version(self, question, version_id, session=None):
        version = self.db.question_versions.find_one({
            "_id": object_id(version_id), "questionId": question["_id"],
        }, session=session)
        if not version:
            raise APIError(404, "NOT_FOUND", "版本不存在")
        return version

    @staticmethod
    def _revision(question, expected):
        if question["revision"] != expected:
            raise APIError(409, "REVISION_CONFLICT", "题目已被修改，请刷新后重试")

    @staticmethod
    def _summary(version):
        return {**{k: version[k] for k in (
            "_id", "versionNo", "baseVersionId", "operation", "createdAt", "archivedAt",
        )}, "title": version["content"]["title"], "type": version["content"]["type"]}

    def _view(self, question, user, session=None, detail=True):
        latest = self._version(question, str(question["latestVersionId"]), session)
        available = question["archivedAt"] is None and self.db.question_versions.find_one({
            "questionId": question["_id"], "archivedAt": None,
        }, {"_id": 1}, session=session) is not None
        result = {k: question[k] for k in (
            "_id", "ownerId", "revision", "latestVersionId", "archivedAt", "createdAt", "updatedAt",
        )}
        result.update(latestVersion=self._summary(latest), permissions={
            "canManage": question["ownerId"] == user["_id"], "canUse": available,
        })
        if detail:
            origin = deepcopy(question["origin"])
            if origin["kind"] == "legacy" and not self.db.surveys.find_one({
                "_id": origin["surveyId"], "ownerId": user["_id"],
            }, {"_id": 1}, session=session):
                origin = {"kind": "legacy"}
            result["origin"] = origin
        return serialize(result)

    @staticmethod
    def _version_view(question, version, user):
        result = deepcopy(version)
        result["permissions"] = {
            "canUse": question["archivedAt"] is None and version["archivedAt"] is None,
        }
        return serialize(result)

    def _new(self, content, user, origin, operation, session=None):
        now = utcnow()
        qid, vid = ObjectId(), ObjectId()
        question = dict(_id=qid, ownerId=user["_id"], latestVersionId=vid, versionSeq=1,
                        origin=origin, revision=1, guardRevision=0, archivedAt=None,
                        createdAt=now, updatedAt=now)
        version = dict(_id=vid, questionId=qid, versionNo=1, baseVersionId=None,
                       operation=operation, content=content, createdBy=user["_id"],
                       createdAt=now, archivedAt=None)
        ensure_size(question)
        ensure_size(version)
        self.db.questions.insert_one(question, session=session)
        self.db.question_versions.insert_one(version, session=session)
        return {"question": self._view(question, user, session),
                "version": self._version_view(question, version, user)}

    def create(self, body, user, session=None):
        return self._new(normalize_content(body.content), user, {"kind": "original"}, "create", session)

    def get(self, question_id, user):
        return self._view(self._question(question_id, user), user)

    def list_questions(self, user, page, page_size, scope="owned", archive="exclude", search=""):
        shared = list(self.db.question_shares.distinct("questionId", {"recipientId": user["_id"], "revokedAt": None}))
        query = {"ownerId": user["_id"]} if scope == "owned" else {"_id": {"$in": shared}, "ownerId": {"$ne": user["_id"]}}
        if scope == "all":
            query = {"$or": [{"ownerId": user["_id"]}, {"_id": {"$in": shared}}]}
        if archive != "include":
            query["archivedAt"] = None if archive == "exclude" else {"$ne": None}
        if search.strip():
            version_ids = list(self.db.question_versions.distinct("_id", {"content.title": {"$regex": re.escape(search.strip()), "$options": "i"}}))
            query["latestVersionId"] = {"$in": version_ids}
        docs = list(self.db.questions.find(query).sort([("updatedAt", -1), ("_id", -1)]).skip((page - 1) * page_size).limit(page_size))
        return dict(items=[self._view(q, user, detail=False) for q in docs], total=self.db.questions.count_documents(query), page=page, pageSize=page_size)

    def versions(self, question_id, user, page, page_size, archive="exclude"):
        question = self._question(question_id, user)
        query = {"questionId": question["_id"]}
        if archive != "include":
            query["archivedAt"] = None if archive == "exclude" else {"$ne": None}
        docs = self.db.question_versions.find(query).sort("versionNo", -1).skip((page - 1) * page_size).limit(page_size)
        return serialize(dict(items=[self._summary(v) for v in docs], total=self.db.question_versions.count_documents(query),
                              page=page, pageSize=page_size, questionRevision=question["revision"]))

    def get_version(self, question_id, version_id, user):
        question = self._question(question_id, user)
        return dict(questionRevision=question["revision"], version=self._version_view(question, self._version(question, version_id), user))

    def _append(self, question, content, source, operation, user, session=None):
        now, vid = utcnow(), ObjectId()
        version = dict(_id=vid, questionId=question["_id"], versionNo=question["versionSeq"] + 1,
                       baseVersionId=source["_id"], operation=operation, content=content,
                       createdBy=user["_id"], createdAt=now, archivedAt=None)
        ensure_size(version)
        updated = self.db.questions.find_one_and_update({"_id": question["_id"], "revision": question["revision"]},
            {"$set": {"latestVersionId": vid, "updatedAt": now}, "$inc": {"revision": 1, "versionSeq": 1}},
            return_document=ReturnDocument.AFTER, session=session)
        if not updated:
            raise APIError(409, "REVISION_CONFLICT", "题目已被修改，请刷新后重试")
        self.db.question_versions.insert_one(version, session=session)
        return {"question": self._view(updated, user, session), "version": self._version_view(updated, version, user)}

    def add_version(self, question_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        source = self._version(question, body.baseVersionId, session)
        return self._append(question, normalize_content(body.content), source, "edit", user, session)

    def restore(self, question_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        source = self._version(question, body.sourceVersionId, session)
        return self._append(question, deepcopy(source["content"]), source, "restore", user, session)

    def _use(self, question_id, version_id, user, session=None):
        question = self._question(question_id, user, session)
        # A real master write serializes new bindings/copies with revoke/archive.
        locked = self.db.questions.find_one_and_update({"_id": question["_id"]}, {"$inc": {"guardRevision": 1}},
            return_document=ReturnDocument.AFTER, session=session)
        version = self._version(locked, version_id, session)
        if locked["archivedAt"] is not None:
            raise APIError(409, "QUESTION_ARCHIVED", "题目已归档，不能新增选用")
        if version["archivedAt"] is not None:
            raise APIError(409, "VERSION_ARCHIVED", "版本已归档，不能新增选用")
        return locked, version

    def copy(self, question_id, body, user, session=None):
        question, version = self._use(question_id, body.versionId, user, session)
        return self._new(deepcopy(version["content"]), user,
                         {"kind": "copy", "questionId": question["_id"], "versionId": version["_id"]}, "copy", session)

    def archive(self, question_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        if (question["archivedAt"] is not None) != body.archived:
            now = utcnow()
            question = self.db.questions.find_one_and_update({"_id": question["_id"], "revision": body.revision},
                {"$set": {"archivedAt": now if body.archived else None, "updatedAt": now}, "$inc": {"revision": 1, "guardRevision": 1}},
                return_document=ReturnDocument.AFTER, session=session)
            if not question:
                raise APIError(409, "REVISION_CONFLICT", "题目已被修改，请刷新后重试")
        return self._view(question, user, session)

    def archive_version(self, question_id, version_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        version = self._version(question, version_id, session)
        if (version["archivedAt"] is not None) != body.archived:
            now = utcnow()
            question = self.db.questions.find_one_and_update({"_id": question["_id"], "revision": body.revision},
                {"$set": {"updatedAt": now}, "$inc": {"revision": 1, "guardRevision": 1}},
                return_document=ReturnDocument.AFTER, session=session)
            if not question:
                raise APIError(409, "REVISION_CONFLICT", "题目已被修改，请刷新后重试")
            version = {**version, "archivedAt": now if body.archived else None}
            self.db.question_versions.update_one({"_id": version["_id"]}, {"$set": {"archivedAt": version["archivedAt"]}}, session=session)
        return dict(questionRevision=question["revision"], version=self._version_view(question, version, user))

    def _share_view(self, share, recipient=None, session=None):
        if recipient is None:
            recipient = self.db.users.find_one({"_id": share["recipientId"]}, {"username": 1}, session=session)
        # Accounts are not deletable. Do not manufacture a different identity.
        if not recipient:
            raise APIError(503, "DATABASE_ERROR", "共享接收者数据暂时无法读取")
        return serialize({**{k: share[k] for k in (
            "_id", "grantedAt", "revokedAt", "createdAt", "updatedAt",
        )}, "recipient": {"_id": recipient["_id"], "username": recipient["username"]}})

    def shares(self, question_id, user, page, page_size, state="all"):
        question = self._question(question_id, user, owner=True)
        query = {"questionId": question["_id"]}
        if state != "all":
            query["revokedAt"] = None if state == "active" else {"$ne": None}
        docs = list(self.db.question_shares.find(query).sort([("updatedAt", -1), ("_id", -1)])
                    .skip((page - 1) * page_size).limit(page_size))
        recipients = {row["_id"]: row for row in self.db.users.find(
            {"_id": {"$in": [doc["recipientId"] for doc in docs]}}, {"username": 1},
        )}
        return dict(items=[self._share_view(doc, recipients.get(doc["recipientId"])) for doc in docs],
                    total=self.db.question_shares.count_documents(query), page=page, pageSize=page_size,
                    questionRevision=question["revision"])

    def _manage_access(self, question, session=None):
        now = utcnow()
        updated = self.db.questions.find_one_and_update(
            {"_id": question["_id"], "revision": question["revision"]},
            {"$set": {"updatedAt": now}, "$inc": {"revision": 1, "guardRevision": 1}},
            return_document=ReturnDocument.AFTER, session=session,
        )
        if not updated:
            raise APIError(409, "REVISION_CONFLICT", "题目已被修改，请刷新后重试")
        return updated, now

    def grant(self, question_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        # The DTO applies exactly registration's NFKC/trim/lower key transform.
        # Lowercasing may expand a valid 32-character display name, so do not
        # validate the resulting identity key as another display name.
        recipient = self.db.users.find_one({"usernameKey": body.recipientUsername}, {"username": 1}, session=session)
        if not recipient:
            raise APIError(422, "SHARE_RECIPIENT_NOT_FOUND", "未找到该用户名对应的已注册用户")
        if recipient["_id"] == user["_id"]:
            raise APIError(422, "SELF_SHARE_NOT_ALLOWED", "无需向自己共享题目")
        share = self.db.question_shares.find_one({"questionId": question["_id"], "recipientId": recipient["_id"]}, session=session)
        if share and share["revokedAt"] is None:
            return 200, dict(questionRevision=question["revision"], share=self._share_view(share, recipient))
        question, now = self._manage_access(question, session)
        status = 200 if share else 201
        if share:
            share = self.db.question_shares.find_one_and_update({"_id": share["_id"]}, {
                "$set": {"grantedBy": user["_id"], "grantedAt": now, "revokedAt": None, "updatedAt": now},
                "$inc": {"revision": 1},
            }, return_document=ReturnDocument.AFTER, session=session)
        else:
            share = dict(_id=ObjectId(), questionId=question["_id"], recipientId=recipient["_id"],
                         grantedBy=user["_id"], grantedAt=now, revokedAt=None, revision=1, createdAt=now, updatedAt=now)
            self.db.question_shares.insert_one(share, session=session)
        return status, dict(questionRevision=question["revision"], share=self._share_view(share, recipient))

    def revoke(self, question_id, share_id, body, user, session=None):
        question = self._question(question_id, user, session, owner=True)
        self._revision(question, body.revision)
        share = self.db.question_shares.find_one({"_id": object_id(share_id), "questionId": question["_id"]}, session=session)
        if not share:
            raise APIError(404, "NOT_FOUND", "共享关系不存在")
        if share["revokedAt"] is None:
            question, now = self._manage_access(question, session)
            share = self.db.question_shares.find_one_and_update({"_id": share["_id"]}, {
                "$set": {"revokedAt": now, "updatedAt": now}, "$inc": {"revision": 1},
            }, return_document=ReturnDocument.AFTER, session=session)
        return dict(questionRevision=question["revision"], share=self._share_view(share, session=session))

    def usage(self, question_id, user, page, page_size):
        question = self._question(question_id, user, owner=True)
        query = {"schemaVersion": 2, "questions.questionId": question["_id"]}
        # Compute counts and the owned page from the same aggregation input. The
        # branch for other owners only emits a count, never identifiers/content.
        result = next(self.db.surveys.aggregate([
            {"$match": query},
            {"$facet": {
                "counts": [{"$group": {"_id": {"$eq": ["$ownerId", user["_id"]]}, "count": {"$sum": 1}}}],
                "visible": [
                    {"$match": {"ownerId": user["_id"]}}, {"$sort": {"createdAt": -1, "_id": -1}},
                    {"$skip": (page - 1) * page_size}, {"$limit": page_size},
                    {"$project": {"title": 1, "status": 1, "deadlineAt": 1,
                        "question": {"$let": {
                            "vars": {"q": {"$arrayElemAt": [{"$filter": {"input": "$questions", "as": "q",
                                "cond": {"$eq": ["$$q.questionId", question["_id"]]}}}, 0]}},
                            "in": {"id": "$$q.id", "versionId": "$$q.versionId", "versionNo": "$$q.versionNo"},
                        }}}},
                ],
            }},
        ]))
        counts = {row["_id"]: row["count"] for row in result["counts"]}
        own_count, other_count, now = counts.get(True, 0), counts.get(False, 0), utcnow()
        items = []
        for survey in result["visible"]:
            status = survey["status"]
            if status == "published" and survey["deadlineAt"] is not None and survey["deadlineAt"] <= now:
                status = "closed"
            snapshot = survey["question"]
            items.append(dict(surveyId=survey["_id"], title=survey["title"], status=status,
                              instanceId=snapshot["id"], versionId=snapshot["versionId"], versionNo=snapshot["versionNo"]))
        return serialize(dict(questionId=question["_id"], totalSurveyCount=own_count + other_count,
                              visibleSurveyCount=own_count, otherSurveyCount=other_count,
                              items=items, total=own_count, page=page, pageSize=page_size))

    def bind(self, references, user, existing=(), session=None):
        refs = [r.model_dump(exclude_none=True) for r in references]
        if len({r["questionId"] for r in refs}) != len(refs):
            raise APIError(422, "DUPLICATE_QUESTION", "同一问卷内同一道题目最多出现一次")
        if len({r["id"] for r in refs}) != len(refs):
            raise APIError(422, "DUPLICATE_INSTANCE", "同一问卷内题目 ID 不能重复")
        saved = {(q["id"], str(q.get("questionId")), str(q.get("versionId"))): q for q in existing}
        bound = {}
        for ref in sorted(refs, key=lambda r: r["questionId"]):
            prior = saved.get((ref["id"], ref["questionId"], ref["versionId"]))
            if prior:
                snapshot = {k: deepcopy(prior[k]) for k in (*CONTENT_FIELDS, *REFERENCE_FIELDS) if k in prior}
            else:
                _, version = self._use(ref["questionId"], ref["versionId"], user, session)
                snapshot = {**deepcopy(version["content"]), "questionId": object_id(ref["questionId"]),
                            "versionId": version["_id"], "versionNo": version["versionNo"]}
            bound[ref["id"]] = {**snapshot, **{k: ref[k] for k in INSTANCE_FIELDS}}
        # Preserve client array order after locking masters in a stable order.
        return validate_snapshot([bound[r["id"]] for r in refs])
