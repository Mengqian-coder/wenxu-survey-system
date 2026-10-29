"""Business operations and MongoDB transaction boundaries."""
import secrets
import re
from datetime import datetime, timezone

from bson import BSON, Decimal128, ObjectId
from pymongo import ReturnDocument, timeout
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .domain import validate_submission, preview_answers
from .errors import APIError
from .security import (
    decode_token, dummy_hash, issue_token, normalize_username, password_hasher,
)


def utcnow():
    value = datetime.now(timezone.utc)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise APIError(404, "NOT_FOUND", "资源不存在")
    return ObjectId(value)


def serialize(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize(v) for v in value]
    return value


def ensure_size(doc):
    if len(BSON.encode(doc)) > 16 * 1024 * 1024:
        raise APIError(413, "DOCUMENT_TOO_LARGE", "数据超过 MongoDB 单文档大小上限")


class SurveyService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    @staticmethod
    def safe_user(user):
        return serialize({key: user[key] for key in ("_id", "username", "createdAt")})

    def register(self, body):
        username, key = normalize_username(body.username)
        now = utcnow()
        user = dict(_id=ObjectId(), username=username, usernameKey=key,
                    passwordHash=password_hasher.hash(body.password), createdAt=now, updatedAt=now)
        try:
            self.db.users.insert_one(user)
        except DuplicateKeyError:
            raise APIError(409, "USERNAME_TAKEN", "用户名已被注册") from None
        return self.safe_user(user)

    def login(self, body):
        try:
            _, key = normalize_username(body.username)
        except APIError:
            key = ""
        user = self.db.users.find_one({"usernameKey": key}) if key else None
        valid = password_hasher.verify(body.password, user["passwordHash"] if user else dummy_hash)
        if not user or not valid:
            raise APIError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
        return {"access_token": issue_token(self.settings, user["_id"], "access"), "token_type": "bearer"}

    def authenticate(self, token):
        claims = decode_token(self.settings, token, "access")
        user = self.db.users.find_one({"_id": ObjectId(claims["sub"])})
        if not user:
            raise APIError(401, "INVALID_TOKEN", "账号不存在或认证已失效")
        return user

    def _expire(self, survey, now=None, session=None):
        now = now or utcnow()
        if survey["status"] == "published" and survey["deadlineAt"] is not None and survey["deadlineAt"] <= now:
            updated = self.db.surveys.find_one_and_update(
                {"_id": survey["_id"], "status": "published", "deadlineAt": {"$lte": now}},
                [{"$set": {"status": "closed", "closedAt": "$deadlineAt", "closeReason": "deadline", "updatedAt": now,
                            "revision": {"$add": ["$revision", 1]}}}], return_document=ReturnDocument.AFTER, session=session,
            )
            return updated or self.db.surveys.find_one({"_id": survey["_id"]}, session=session)
        return survey

    def owned(self, survey_id, user, session=None):
        survey = self.db.surveys.find_one({"_id": object_id(survey_id), "ownerId": user["_id"]}, session=session)
        if not survey:
            raise APIError(404, "NOT_FOUND", "问卷不存在")
        return self._expire(survey, session=session)

    @staticmethod
    def _deadline(value, now):
        if value is not None and value <= now:
            raise APIError(422, "INVALID_DEADLINE", "截止时间必须晚于当前时间")

    def create(self, body, user, session=None):
        from .question_service import QuestionService
        now = utcnow()
        self._deadline(body.deadlineAt, now)
        questions = QuestionService(self.db).bind(body.questions, user, session=session)
        doc = dict(
            _id=ObjectId(), ownerId=user["_id"], publicId=secrets.token_hex(16),
            title=body.title, description=body.description, settings=body.settings.model_dump(),
            deadlineAt=body.deadlineAt, status="draft", questions=questions, schemaVersion=2, revision=1,
            createdAt=now, updatedAt=now, publishedAt=None, closedAt=None, closeReason=None,
        )
        ensure_size(doc)
        for _ in range(3):
            try:
                self.db.surveys.insert_one(doc, session=session)
                return serialize(doc)
            except DuplicateKeyError:
                if session is not None:
                    # A failed transaction cannot be reused after a unique-key error.
                    raise
                doc["publicId"] = secrets.token_hex(16)
        raise APIError(503, "ID_COLLISION", "暂时无法分配分享标识，请重试")

    def list_surveys(self, user, page, page_size, status=None, search=""):
        query = {"ownerId": user["_id"]}
        now = utcnow()
        if status == "published":
            query.update(status="published", **{"$or": [{"deadlineAt": None}, {"deadlineAt": {"$gt": now}}]})
        elif status == "closed":
            query["$or"] = [{"status": "closed"}, {"status": "published", "deadlineAt": {"$lte": now}}]
        elif status == "draft":
            query["status"] = "draft"
        if search.strip():
            query["title"] = {"$regex": re.escape(search.strip()), "$options": "i"}
        # Lazy expiry is only a persistence convenience; submission always checks time.
        docs = list(self.db.surveys.find(query).sort([("createdAt", -1), ("_id", -1)])
                    .skip((page - 1) * page_size).limit(page_size))
        docs = [self._expire(doc) for doc in docs]
        response_counts = {row["_id"]: row["count"] for row in self.db.responses.aggregate([
            {"$match": {"surveyId": {"$in": [doc["_id"] for doc in docs]}}},
            {"$group": {"_id": "$surveyId", "count": {"$sum": 1}}},
        ])} if docs else {}
        for doc in docs:
            doc["questionCount"] = len(doc["questions"])
            doc["responseCount"] = response_counts.get(doc["_id"], 0)
            doc.pop("questions", None)
        return serialize(dict(items=docs, total=self.db.surveys.count_documents(query), page=page, pageSize=page_size))

    def patch(self, survey_id, body, user, session=None):
        from .question_service import QuestionService
        survey = self.owned(survey_id, user, session=session)
        if survey["revision"] != body.revision:
            raise APIError(409, "REVISION_CONFLICT", "问卷已被修改，请刷新后重试")
        fields = body.model_fields_set - {"revision"}
        if not fields:
            raise APIError(422, "EMPTY_PATCH", "请提供需要修改的字段")
        if survey["status"] == "closed":
            raise APIError(409, "SURVEY_CLOSED", "已关闭问卷不能修改")
        if survey["status"] == "published" and fields - {"title", "description", "deadlineAt"}:
            raise APIError(409, "SURVEY_LOCKED", "发布后禁止修改题目、选项、规则和填写设置")
        now = utcnow()
        values = body.model_dump(exclude_unset=True)
        values.pop("revision", None)
        if "settings" in fields:
            # exclude_unset is recursive; explicitly include nested defaults so
            # the MongoDB settings object always contains both required flags.
            values["settings"] = body.settings.model_dump()
        if "questions" in fields:
            if survey["schemaVersion"] != 2:
                raise APIError(409, "LEGACY_SURVEY_REQUIRES_MIGRATION", "旧版问卷需先迁移后才能修改题目结构")
            values["questions"] = QuestionService(self.db).bind(body.questions, user, survey["questions"], session)
        if "deadlineAt" in fields:
            self._deadline(body.deadlineAt, now)
        values["updatedAt"] = now
        candidate = {**survey, **values, "revision": body.revision + 1}
        ensure_size(candidate)
        query = {"_id": survey["_id"], "ownerId": user["_id"], "revision": body.revision, "status": survey["status"]}
        if survey["status"] == "published":
            query["$or"] = [{"deadlineAt": None}, {"deadlineAt": {"$gt": now}}]
        updated = self.db.surveys.find_one_and_update(query, {"$set": values, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER, session=session)
        if not updated:
            raise APIError(409, "REVISION_CONFLICT", "问卷状态已改变，请刷新后重试")
        return serialize(updated)

    def publish(self, survey_id, revision, user, session=None):
        from .question_service import validate_snapshot
        survey = self.owned(survey_id, user, session=session)
        if survey["revision"] != revision:
            raise APIError(409, "REVISION_CONFLICT", "问卷已被修改，请刷新后重试")
        if survey["status"] != "draft":
            raise APIError(409, "INVALID_STATE", "只有草稿问卷可以发布")
        validate_snapshot(survey["questions"], publish=True)
        now = utcnow()
        self._deadline(survey["deadlineAt"], now)
        updated = self.db.surveys.find_one_and_update(
            {"_id": survey["_id"], "ownerId": user["_id"], "revision": revision, "status": "draft"},
            {"$set": {"status": "published", "publishedAt": now, "updatedAt": now}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if not updated:
            raise APIError(409, "REVISION_CONFLICT", "问卷已改变，请刷新后重试")
        return serialize(updated)

    def close(self, survey_id, revision, user, session=None):
        survey = self.owned(survey_id, user, session=session)
        if survey["revision"] != revision:
            raise APIError(409, "REVISION_CONFLICT", "问卷已被修改，请刷新后重试")
        if survey["status"] != "published":
            raise APIError(409, "INVALID_STATE", "只有已发布问卷可以手动关闭")
        now = utcnow()
        updated = self.db.surveys.find_one_and_update(
            {"_id": survey["_id"], "ownerId": user["_id"], "revision": revision, "status": "published"},
            {"$set": {"status": "closed", "closedAt": now, "closeReason": "manual", "updatedAt": now}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if not updated:
            raise APIError(409, "REVISION_CONFLICT", "问卷已改变，请刷新后重试")
        return serialize(updated)

    def _public(self, public_id, now=None):
        survey = self.db.surveys.find_one({"publicId": public_id})
        if not survey or survey["status"] == "draft":
            raise APIError(404, "NOT_FOUND", "问卷不存在或尚未发布")
        survey = self._expire(survey, now)
        if survey["status"] != "published":
            raise APIError(410, "SURVEY_CLOSED", "问卷已关闭或已截止")
        return survey

    def public_get(self, public_id, user):
        survey = self._public(public_id)
        # Exclude internal rule definitions and ownership from participant data.
        fields = ("_id", "publicId", "title", "description", "status", "settings", "deadlineAt", "questions")
        public = {k: survey[k] for k in fields}
        allowed = {"id", "type", "title", "description", "required", "validation", "options"}
        public["questions"] = [{k: v for k, v in q.items() if k in allowed} for q in survey["questions"]]
        public["canSubmit"] = survey["settings"]["allowRepeat"] or not self.db.participations.find_one({"surveyId": survey["_id"], "userId": user["_id"]})
        return serialize(public)

    def start(self, public_id, user):
        survey = self._public(public_id)
        if not survey["settings"]["allowRepeat"] and self.db.participations.find_one({"surveyId": survey["_id"], "userId": user["_id"]}):
            raise APIError(409, "ALREADY_SUBMITTED", "该账号已提交此问卷")
        key = secrets.token_hex(32)
        token = issue_token(self.settings, user["_id"], "submission", sid=str(survey["_id"]), key=key)
        return {"submissionKey": key, "submissionToken": token}

    def preview(self, public_id, body, user):
        from .question_service import answer_definition
        survey = self._public(public_id)
        return preview_answers(answer_definition(survey["questions"]), [a.model_dump() for a in body.answers], body.changedQuestionId)

    @staticmethod
    def _retry_matches(existing, answers, user, survey):
        if existing["answers"] != answers or (not survey["settings"]["anonymous"] and existing.get("respondentId") != user["_id"]):
            raise APIError(409, "SUBMISSION_KEY_REUSED", "同一提交标识不能用于不同答案")
        return {"submitted": True}

    def submit(self, public_id, body, user, received_at):
        from .question_service import answer_definition
        survey = self.db.surveys.find_one({"publicId": public_id})
        if not survey or survey["status"] == "draft":
            raise APIError(404, "NOT_FOUND", "问卷不存在或尚未发布")
        claims = decode_token(self.settings, body.submissionToken, "submission")
        if claims["sub"] != str(user["_id"]) or claims.get("sid") != str(survey["_id"]) or claims.get("key") != body.submissionKey:
            raise APIError(403, "SUBMISSION_TOKEN_MISMATCH", "填写凭据与账号、问卷或提交标识不匹配")
        answers = validate_submission(answer_definition(survey["questions"]), [a.model_dump() for a in body.answers])
        key_query = {"surveyId": survey["_id"], "submissionKey": body.submissionKey}
        existing = self.db.responses.find_one(key_query)
        if existing:
            return self._retry_matches(existing, answers, user, survey)
        response = dict(_id=ObjectId(), surveyId=survey["_id"], submissionKey=body.submissionKey,
                        answers=answers, submittedAt=received_at, schemaVersion=1)
        if not survey["settings"]["anonymous"]:
            response["respondentId"] = user["_id"]
        ensure_size(response)

        def transaction(session):
            current = self.db.surveys.find_one({"_id": survey["_id"]}, session=session)
            existing_tx = self.db.responses.find_one(key_query, session=session)
            if existing_tx:
                return self._retry_matches(existing_tx, answers, user, current)
            if (current["publishedAt"] is None or current["publishedAt"] > received_at or
                    current["deadlineAt"] is not None and current["deadlineAt"] <= received_at or
                    current["status"] == "draft" or
                    current["status"] == "closed" and current["closeReason"] != "deadline"):
                raise APIError(410, "SURVEY_CLOSED", "问卷已关闭或已截止")
            # This real write serializes submissions with close/deadline edits.
            locked = self.db.surveys.update_one(
                {"_id": current["_id"], "revision": current["revision"], "status": current["status"]},
                {"$inc": {"revision": 1}, "$set": {"updatedAt": utcnow()}}, session=session,
            )
            if locked.matched_count != 1:
                raise APIError(409, "REVISION_CONFLICT", "问卷状态已变化，请重试")
            if not current["settings"]["allowRepeat"]:
                self.db.participations.insert_one({"_id": ObjectId(), "surveyId": current["_id"], "userId": user["_id"]}, session=session)
            self.db.responses.insert_one(response, session=session)
            return {"submitted": True}

        try:
            with timeout(15), self.db.client.start_session() as session:
                return session.with_transaction(transaction, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"), max_commit_time_ms=5000)
        except DuplicateKeyError:
            # The winning concurrent transaction may have committed the same key.
            existing = self.db.responses.find_one(key_query)
            if existing:
                return self._retry_matches(existing, answers, user, survey)
            raise APIError(409, "ALREADY_SUBMITTED", "该账号已提交此问卷") from None

    def responses(self, survey_id, user, page, page_size):
        survey = self.owned(survey_id, user)
        query = {"surveyId": survey["_id"]}
        docs = list(self.db.responses.find(query, {"submissionKey": 0}).sort([("submittedAt", -1), ("_id", -1)])
                    .skip((page - 1) * page_size).limit(page_size))
        identities = {row["_id"]: row["username"] for row in self.db.users.find(
            {"_id": {"$in": [doc["respondentId"] for doc in docs if "respondentId" in doc]}}, {"username": 1},
        )}
        for doc in docs:
            if "respondentId" in doc:
                doc["respondentName"] = identities.get(doc["respondentId"], "已登录用户")
        return serialize(dict(items=docs, total=self.db.responses.count_documents(query), page=page, pageSize=page_size))

    def statistics(self, survey_id, user, question_id, page, page_size):
        survey = self.owned(survey_id, user)
        questions = survey["questions"]
        if question_id:
            questions = [q for q in questions if q["id"] == question_id]
            if not questions:
                raise APIError(404, "NOT_FOUND", "题目不存在")
        start = [{"$match": {"surveyId": survey["_id"]}}, {"$unwind": "$answers"}]
        counts = {row["_id"]: row for row in self.db.responses.aggregate(start + [{"$group": {
            "_id": "$answers.questionId", "answeredCount": {"$sum": {"$cond": [{"$ne": ["$answers.value", None]}, 1, 0]}},
            # Finite doubles near 1e308 can overflow a double running sum.
            # Decimal128 aggregation has ample exponent range. Converting via
            # shortest decimal text also avoids double->decimal's 15-digit rounding.
            "average": {"$avg": {"$cond": [
                {"$isNumber": "$answers.value"}, {"$toDecimal": {"$toString": "$answers.value"}}, None,
            ]}},
        }}])}
        choice_ids = [q["id"] for q in questions if q["type"] in ("single", "multiple")]
        choices = {}
        if choice_ids:
            pipeline = start + [{"$match": {"answers.questionId": {"$in": choice_ids}, "answers.value": {"$ne": None}}},
                                {"$unwind": "$answers.value"}, {"$group": {"_id": {"question": "$answers.questionId", "option": "$answers.value"}, "count": {"$sum": 1}}}]
            for row in self.db.responses.aggregate(pipeline):
                choices[(row["_id"]["question"], row["_id"]["option"])] = row["count"]
        result = []
        for q in questions:
            group = counts.get(q["id"], {})
            item = {"questionId": q["id"], "title": q["title"], "type": q["type"], "answeredCount": group.get("answeredCount", 0)}
            if q["type"] in ("single", "multiple"):
                item["options"] = [{"optionId": opt["id"], "label": opt["label"], "count": choices.get((q["id"], opt["id"]), 0)} for opt in q["options"]]
            if q["type"] == "number":
                item["average"] = group.get("average")
            if question_id and q["type"] in ("text", "number"):
                pipeline = start + [{"$match": {"answers.questionId": q["id"], "answers.value": {"$ne": None}}},
                                    {"$sort": {"submittedAt": -1, "_id": -1}}, {"$skip": (page - 1) * page_size},
                                    {"$limit": page_size}, {"$project": {"_id": 0, "value": "$answers.value"}}]
                item.update(values=[r["value"] for r in self.db.responses.aggregate(pipeline)], valueTotal=item["answeredCount"], page=page, pageSize=page_size)
            result.append(item)
        return serialize({"surveyId": survey["_id"], "totalResponses": self.db.responses.count_documents({"surveyId": survey["_id"]}), "questions": result})
