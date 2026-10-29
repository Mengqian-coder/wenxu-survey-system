"""Owned classification containers; membership never grants question access.

Every mutation is called inside MutationRunner's transaction. Real writes to
the bank and source question serialize membership changes with deletion and
revocation without treating either guard as a public metadata revision.
"""
from bson import ObjectId
from pymongo import ReturnDocument

from .errors import APIError
from .question_service import QuestionService
from .service import ensure_size, object_id, serialize, utcnow


class QuestionBankService:
    def __init__(self, db):
        self.db = db
        self.questions = QuestionService(db)

    def _owned(self, bank_id, user, session=None):
        bank = self.db.question_banks.find_one({
            "_id": object_id(bank_id), "ownerId": user["_id"],
        }, session=session)
        if bank is None:
            raise APIError(404, "NOT_FOUND", "题库不存在")
        return bank

    @staticmethod
    def _revision(bank, revision):
        if bank["revision"] != revision:
            raise APIError(409, "REVISION_CONFLICT", "题库已被修改，请刷新后重试")

    def _view(self, bank, session=None):
        result = {name: bank[name] for name in (
            "_id", "ownerId", "name", "description", "revision", "createdAt", "updatedAt",
        )}
        result["itemCount"] = self.db.question_bank_items.count_documents(
            {"bankId": bank["_id"]}, session=session)
        return serialize(result)

    def _guard(self, bank, user, session=None):
        locked = self.db.question_banks.find_one_and_update({
            "_id": bank["_id"], "ownerId": user["_id"],
        }, {"$inc": {"guardRevision": 1}},
            return_document=ReturnDocument.AFTER, session=session)
        if locked is None:
            raise APIError(404, "NOT_FOUND", "题库不存在")
        return locked

    def create(self, body, user, session=None):
        now = utcnow()
        bank = dict(_id=ObjectId(), ownerId=user["_id"], name=body.name,
                    description=body.description, revision=1, guardRevision=0,
                    createdAt=now, updatedAt=now)
        ensure_size(bank)
        self.db.question_banks.insert_one(bank, session=session)
        return self._view(bank, session)

    def list_banks(self, user, page, page_size):
        query = {"ownerId": user["_id"]}
        banks = self.db.question_banks.find(query).sort([
            ("updatedAt", -1), ("_id", -1),
        ]).skip((page - 1) * page_size).limit(page_size)
        return dict(items=[self._view(bank) for bank in banks],
                    total=self.db.question_banks.count_documents(query),
                    page=page, pageSize=page_size)

    def get(self, bank_id, user):
        return self._view(self._owned(bank_id, user))

    def patch(self, bank_id, body, user, session=None):
        bank = self._owned(bank_id, user, session)
        self._revision(bank, body.revision)
        values = body.model_dump(exclude_unset=True, exclude={"revision"})
        changed = {name: value for name, value in values.items() if bank[name] != value}
        if changed:
            changed["updatedAt"] = utcnow()
            ensure_size({**bank, **changed})
            updated = self.db.question_banks.find_one_and_update({
                "_id": bank["_id"], "ownerId": user["_id"], "revision": body.revision,
            }, {"$set": changed, "$inc": {"revision": 1}},
                return_document=ReturnDocument.AFTER, session=session)
            if updated is None:
                raise APIError(409, "REVISION_CONFLICT", "题库已被修改，请刷新后重试")
            bank = updated
        return self._view(bank, session)

    def delete(self, bank_id, revision, user, session=None):
        bank = self._owned(bank_id, user, session)
        self._revision(bank, revision)
        result = self.db.question_banks.delete_one({
            "_id": bank["_id"], "ownerId": user["_id"], "revision": revision,
        }, session=session)
        if result.deleted_count != 1:
            raise APIError(409, "REVISION_CONFLICT", "题库已被修改，请刷新后重试")
        self.db.question_bank_items.delete_many({"bankId": bank["_id"]}, session=session)
        return {"bankId": str(bank["_id"]), "deleted": True}

    def items(self, bank_id, user, page, page_size):
        bank = self._owned(bank_id, user)
        query = {"bankId": bank["_id"]}
        records = self.db.question_bank_items.find(query).sort([
            ("createdAt", -1), ("_id", -1),
        ]).skip((page - 1) * page_size).limit(page_size)
        items = []
        for record in records:
            item = {"questionId": str(record["questionId"]), "available": False}
            try:
                question = self.questions._question(str(record["questionId"]), user)
                item.update(available=True, question=self.questions._view(question, user, detail=False))
            except APIError as exc:
                if exc.status != 404:
                    raise
            items.append(item)
        return dict(items=items, total=self.db.question_bank_items.count_documents(query),
                    page=page, pageSize=page_size)

    def add(self, bank_id, question_id, user, session=None):
        bank = self._guard(self._owned(bank_id, user, session), user, session)
        qid = object_id(question_id)
        query = {"bankId": bank["_id"], "questionId": qid}
        result = {"bankId": str(bank["_id"]), "questionId": str(qid), "created": False}
        if self.db.question_bank_items.find_one(query, {"_id": 1}, session=session):
            return 200, result
        # Classification allows archived content, but current read access is
        # checked and coordinated with revocation through a real master write.
        question = self.questions._question(str(qid), user, session)
        locked = self.db.questions.update_one({"_id": question["_id"]},
            {"$inc": {"guardRevision": 1}}, session=session)
        if locked.matched_count != 1:
            raise APIError(404, "NOT_FOUND", "题目不存在")
        self.db.question_bank_items.insert_one({
            "_id": ObjectId(), **query, "createdAt": utcnow(),
        }, session=session)
        result["created"] = True
        return 201, result

    def remove(self, bank_id, question_id, user, session=None):
        bank = self._guard(self._owned(bank_id, user, session), user, session)
        qid = object_id(question_id)
        result = self.db.question_bank_items.delete_one({
            "bankId": bank["_id"], "questionId": qid,
        }, session=session)
        return {"bankId": str(bank["_id"]), "questionId": str(qid),
                "removed": result.deleted_count == 1}
