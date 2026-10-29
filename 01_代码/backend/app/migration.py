"""Explicit, resumable representation migration; never called at application startup.

Each legacy survey is one transaction. Answers, local IDs, content and business
timestamps are unchanged. The unique legacy origin is the durable retry identity.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass

from bson import ObjectId
from pymongo import ReadPreference
from pymongo.errors import BulkWriteError, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .domain import DomainError, validate_questions
from .question_service import CONTENT_FIELDS
from .service import ensure_size, utcnow


class MigrationError(RuntimeError):
    """An intentionally sanitized operator-facing failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MigrationResult:
    survey_id: str
    status: str
    question_count: int
    revision: int | None = None

    def as_dict(self):
        return asdict(self)


def _oid(value):
    if not isinstance(value, (str, ObjectId)) or not ObjectId.is_valid(value):
        raise MigrationError("INVALID_SURVEY_ID", "问卷 ID 必须为 24 位十六进制 ObjectId")
    return ObjectId(value)


class LegacySurveyMigrator:
    """No DDL, automatic expiry, answer reads or secret-bearing logging."""

    def __init__(self, db):
        self.db = db
        self.confirm_db = db.with_options(
            read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"))

    def check_prerequisites(self):
        """Refuse apply when deployed v2 collections/indexes are missing."""
        names = set(self.db.list_collection_names())
        if not {"surveys", "questions", "question_versions"} <= names:
            raise MigrationError("V2_SCHEMA_REQUIRED", "请先部署新版集合、验证器和索引，再执行迁移")
        required = {
            "questions": ("uq_questions_legacy_origin", [("origin.surveyId", 1), ("origin.instanceId", 1)]),
            "question_versions": ("uq_question_versions_question_number", [("questionId", 1), ("versionNo", 1)]),
        }
        for collection, (name, key) in required.items():
            index = self.db[collection].index_information().get(name, {})
            if index.get("key") != key or not index.get("unique"):
                raise MigrationError("V2_INDEX_REQUIRED", "迁移所需的唯一索引缺失；本工具不会自动创建或修改索引")
            if collection == "questions" and index.get("partialFilterExpression") != {"origin.kind": "legacy"}:
                raise MigrationError("V2_INDEX_REQUIRED", "旧题来源唯一索引的部分条件不符合新版要求")

    def candidates(self, *, survey_ids=None, after_id=None, batch_size=100, limit=None):
        """Keyset pages, including current rows only when explicitly requested."""
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise MigrationError("INVALID_BATCH_SIZE", "批次大小必须为 1 至 1000")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise MigrationError("INVALID_LIMIT", "处理上限必须大于零")
        last_id = _oid(after_id) if after_id else None
        if survey_ids:
            selected = sorted({_oid(value) for value in survey_ids})
            selected = [value for value in selected if last_id is None or value > last_id]
            yield from selected[:limit] if limit is not None else selected
            return
        query = {"schemaVersion": 1}
        seen = 0
        while True:
            page_query = deepcopy(query)
            if last_id is not None:
                page_query.setdefault("_id", {})["$gt"] = last_id
            page_limit = min(batch_size, limit - seen) if limit is not None else batch_size
            rows = list(self.db.surveys.find(page_query, {"_id": 1}).sort("_id", 1).limit(page_limit))
            if not rows:
                return
            for row in rows:
                yield row["_id"]
                seen += 1
            last_id = rows[-1]["_id"]
            if limit is not None and seen >= limit:
                return

    @staticmethod
    def _prepare_questions(survey, now):
        """Validate existing semantics, then preserve bytes/values, not defaults."""
        try:
            validate_questions(survey["questions"])
        except (DomainError, KeyError, TypeError, ValueError) as exc:
            raise MigrationError("INVALID_LEGACY_SURVEY", "旧问卷题目不符合现有题型或跳转规则，未做任何变更") from exc
        questions, versions, snapshots = [], [], []
        for instance in survey["questions"]:
            qid, vid = ObjectId(), ObjectId()
            content = {key: deepcopy(instance[key]) for key in CONTENT_FIELDS if key in instance}
            question = dict(
                _id=qid, ownerId=survey["ownerId"], latestVersionId=vid, versionSeq=1,
                origin=dict(kind="legacy", surveyId=survey["_id"], instanceId=instance["id"]),
                revision=1, guardRevision=0, archivedAt=None, createdAt=now, updatedAt=now,
            )
            version = dict(
                _id=vid, questionId=qid, versionNo=1, baseVersionId=None, operation="migrate",
                content=content, createdBy=survey["ownerId"], createdAt=now, archivedAt=None,
            )
            questions.append(question)
            versions.append(version)
            snapshots.append({**deepcopy(instance), "questionId": qid, "versionId": vid, "versionNo": 1})
            ensure_size(question)
            ensure_size(version)
        ensure_size({**survey, "schemaVersion": 2, "revision": survey["revision"] + 1, "questions": snapshots})
        return questions, versions, snapshots

    @staticmethod
    def _abort(session):
        if session.in_transaction:
            try:
                session.abort_transaction()
            except PyMongoError:
                pass

    def _confirm(self, survey_id, original_instances):
        """Absence is inconclusive; never use it to re-enter an uncertain write."""
        current = self.confirm_db.surveys.find_one({"_id": survey_id}, {"schemaVersion": 1, "revision": 1}, max_time_ms=5000)
        if not current or current.get("schemaVersion") != 2:
            return None
        for instance in original_instances:
            question = self.confirm_db.questions.find_one({
                "origin.kind": "legacy", "origin.surveyId": survey_id, "origin.instanceId": instance["id"],
            }, max_time_ms=5000)
            if not question:
                return None
            version = self.confirm_db.question_versions.find_one({
                "questionId": question["_id"], "versionNo": 1, "operation": "migrate",
            }, max_time_ms=5000)
            if not version or version["content"] != {key: instance[key] for key in CONTENT_FIELDS if key in instance}:
                return None
        return MigrationResult(str(survey_id), "migrated", len(original_instances), current["revision"])

    def migrate_one(self, survey_id, *, apply=False):
        survey_id = _oid(survey_id)
        original_instances = None
        if not apply:
            survey = self.db.surveys.find_one({"_id": survey_id})
            if not survey:
                return MigrationResult(str(survey_id), "not_found", 0)
            if survey.get("schemaVersion") == 2:
                return MigrationResult(str(survey_id), "already_current", len(survey["questions"]), survey["revision"])
            if survey.get("schemaVersion") != 1:
                raise MigrationError("UNSUPPORTED_SCHEMA", "问卷结构版本不受支持")
            self._prepare_questions(survey, utcnow())
            return MigrationResult(str(survey_id), "would_migrate", len(survey["questions"]), survey["revision"])

        self.check_prerequisites()
        for attempt in range(3):
            with self.db.client.start_session() as session:
                session.start_transaction(
                    read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY, max_commit_time_ms=5000,
                )
                try:
                    survey = self.db.surveys.find_one({"_id": survey_id}, session=session)
                    if not survey or survey.get("schemaVersion") == 2:
                        self._abort(session)
                        return MigrationResult(str(survey_id), "already_current" if survey else "not_found",
                                               len(survey["questions"]) if survey else 0, survey["revision"] if survey else None)
                    if survey.get("schemaVersion") != 1:
                        raise MigrationError("UNSUPPORTED_SCHEMA", "问卷结构版本不受支持")
                    original_instances = deepcopy(survey["questions"])
                    questions, versions, snapshots = self._prepare_questions(survey, utcnow())
                    if questions:
                        self.db.questions.insert_many(questions, session=session)
                        self.db.question_versions.insert_many(versions, session=session)
                    changed = self.db.surveys.update_one(
                        {"_id": survey_id, "schemaVersion": 1, "revision": survey["revision"]},
                        {"$set": {"schemaVersion": 2, "questions": snapshots}, "$inc": {"revision": 1}}, session=session,
                    )
                    if changed.modified_count != 1:
                        raise MigrationError("REVISION_CONFLICT", "问卷正在修改，整卷迁移已回滚，可重新执行")
                except BaseException as exc:
                    self._abort(session)
                    duplicate = isinstance(exc, DuplicateKeyError) or (
                        isinstance(exc, BulkWriteError) and any(error.get("code") == 11000 for error in exc.details.get("writeErrors", [])))
                    if duplicate:
                        confirmed = self._confirm(survey_id, original_instances or [])
                        if confirmed:
                            return confirmed
                        raise MigrationError("LEGACY_ORIGIN_CONFLICT", "旧题迁移来源已存在，但本卷迁移尚未确认；请检查后重试") from exc
                    if isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError"):
                        if attempt < 2:
                            continue
                        raise MigrationError("MIGRATION_BUSY", "问卷正在并发修改，整卷迁移已回滚，可重新执行") from exc
                    raise
                uncertain = False
                for commit_attempt in range(3):
                    try:
                        session.commit_transaction()
                        return MigrationResult(str(survey_id), "migrated", len(snapshots), survey["revision"] + 1)
                    except PyMongoError as exc:
                        if exc.has_error_label("UnknownTransactionCommitResult"):
                            uncertain = True
                            if commit_attempt < 2:
                                continue
                        elif not uncertain:
                            self._abort(session)
                            if exc.has_error_label("TransientTransactionError") and attempt < 2:
                                break
                            raise
                        # Do not regenerate question identities or repeat a
                        # business callback after any uncertain commit.
                        try:
                            confirmed = self._confirm(survey_id, original_instances)
                        except PyMongoError:
                            confirmed = None
                        if confirmed:
                            return confirmed
                        raise MigrationError("MIGRATION_RESULT_UNKNOWN", "本卷提交结果尚未确认；保留同一问卷 ID 重新查询或重跑，勿手工补建题目") from exc
        raise MigrationError("MIGRATION_BUSY", "问卷正在并发修改，请稍后重试")
