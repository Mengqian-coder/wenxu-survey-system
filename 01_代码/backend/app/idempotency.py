"""Atomic management mutations and durable, byte-for-byte success receipts.

Only the authenticated actor and validated input are needed for replay. The
callback must use the supplied session for every business read/write, and must
not commit it or send effects outside MongoDB.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable

from bson import Binary, Decimal128, ObjectId
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .errors import APIError


KEY_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
CHUNK_BYTES = 256 * 1024
CANONICAL_VERSION = 1


class MutationError(APIError):
    def __init__(self, status, code, message, retry=False):
        super().__init__(status, code, message)
        self.headers = {"Retry-After": "1"} if retry else {}


def _now():
    value = datetime.now(timezone.utc)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _canonical_input(value):
    # Version 1 is intentionally retained when future normalizers are added.
    # Library ObjectId input is normalized by the request DTO; local question
    # IDs, including those in jump targets, remain case sensitive.
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical_input(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_input(item) for item in value]
    return value


def _target(value):
    return "/".join(part.lower() if re.fullmatch(r"[a-fA-F0-9]{24}", part) else part for part in value.split("/"))


def _fingerprint(operation, target, payload, version):
    if version != 1:
        raise MutationError(503, "IDEMPOTENCY_RESULT_UNAVAILABLE", "原操作回执暂时无法读取，请保留原幂等键", retry=True)
    document = {"canonicalVersion": version, "operationId": operation, "target": _target(target), "payload": _canonical_input(payload)}
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result_refs(operation, content):
    refs = {}
    for name in ("question", "version", "share"):
        value = content.get(name)
        if isinstance(value, dict) and value.get("_id") is not None:
            refs[name + "Id"] = str(value["_id"])
    if content.get("_id") is not None:
        field = "surveyId" if operation.startswith("survey") else "bankId" if operation.startswith("bank") else "questionId"
        refs[field] = str(content["_id"])
    for field in ("bankId", "questionId"):
        if content.get(field) is not None:
            refs[field] = str(content[field])
    return refs


class MutationRunner:
    def __init__(self, db):
        self.db = db
        self.receipts = db.mutation_receipts.with_options(
            read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"))
        self.chunks = db.mutation_response_chunks.with_options(
            read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"))

    def _lookup(self, actor_id, key, operation, target, payload):
        receipt = self.receipts.find_one({"actorId": actor_id, "key": key}, max_time_ms=5000)
        if receipt is None:
            return None
        expected = _fingerprint(operation, target, payload, receipt["canonicalVersion"])
        if receipt["fingerprint"] != expected:
            raise MutationError(409, "IDEMPOTENCY_KEY_REUSED", "该幂等键已用于其他操作或参数")
        if receipt["status"] != "succeeded":
            raise MutationError(409, "IDEMPOTENCY_IN_PROGRESS", "同一操作正在处理中，请使用原幂等键重试", retry=True)
        try:
            stored = receipt["response"]
            if stored["mode"] == "inline":
                raw = bytes(stored["data"])
                if len(raw) > CHUNK_BYTES:
                    raise ValueError("oversized inline receipt")
            elif stored["mode"] == "chunks":
                chunks = list(self.chunks.find({"receiptId": receipt["_id"]}).sort("index", 1).max_time_ms(5000))
                if len(chunks) != stored["chunkCount"] or any(
                    chunk["index"] != index or not 0 < len(chunk["data"]) <= CHUNK_BYTES
                    for index, chunk in enumerate(chunks)
                ):
                    raise ValueError("incomplete receipt chunks")
                raw = b"".join(bytes(chunk["data"]) for chunk in chunks)
            else:
                raise ValueError("unknown response mode")
            if len(raw) != stored["byteLength"] or hashlib.sha256(raw).hexdigest() != stored["sha256"]:
                raise ValueError("receipt integrity mismatch")
            # Validate corruption without rebuilding a response from live data.
            if not isinstance(json.loads(raw), dict) or not 200 <= receipt["httpStatus"] < 300:
                raise ValueError("invalid saved response")
        except (KeyError, TypeError, ValueError, UnicodeError, PyMongoError) as exc:
            raise MutationError(503, "IDEMPOTENCY_RESULT_UNAVAILABLE", "原操作已完成，但成功回执暂时无法读取，请保留原幂等键", retry=True) from exc
        return Response(raw, status_code=receipt["httpStatus"], media_type="application/json", headers={"Idempotency-Replayed": "true"})

    def _save_response(self, receipt_id, operation, content, status, session):
        encoded = jsonable_encoder(content, custom_encoder={ObjectId: str, Decimal128: lambda value: float(value.to_decimal())})
        response = JSONResponse(encoded, status_code=status, headers={"Idempotency-Replayed": "false"})
        raw = response.body
        storage = {"mode": "inline", "byteLength": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if len(raw) <= CHUNK_BYTES:
            storage["data"] = Binary(raw)
        else:
            storage["mode"] = "chunks"
            pieces = [raw[index:index + CHUNK_BYTES] for index in range(0, len(raw), CHUNK_BYTES)]
            storage["chunkCount"] = len(pieces)
            self.db.mutation_response_chunks.insert_many([
                {"_id": ObjectId(), "receiptId": receipt_id, "index": index, "data": Binary(piece)}
                for index, piece in enumerate(pieces)
            ], session=session)
        self.db.mutation_receipts.update_one({"_id": receipt_id, "status": "processing"}, {"$set": {
            "status": "succeeded", "httpStatus": status, "response": storage,
            "resultRefs": _result_refs(operation, encoded), "completedAt": _now(),
        }}, session=session)
        return response

    @staticmethod
    def _abort(session):
        if session.in_transaction:
            try:
                session.abort_transaction()
            except PyMongoError:
                pass

    def run(self, actor, key, operation, target, payload, callback: Callable):
        if not isinstance(key, str) or not re.fullmatch(KEY_PATTERN, key):
            raise MutationError(422, "INVALID_REQUEST", "Idempotency-Key 必须为小写 UUID v4")
        actor_id = actor["_id"] if isinstance(actor, dict) else actor
        replay = self._lookup(actor_id, key, operation, target, payload)
        if replay is not None:
            return replay
        fingerprint = _fingerprint(operation, target, payload, CANONICAL_VERSION)
        # No callback is re-entered after an uncertain commit. A transient error
        # before commit may retry the fully aborted transaction with this key.
        for attempt in range(3):
            with self.db.client.start_session() as session:
                session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY, max_commit_time_ms=5000)
                try:
                    receipt_id = ObjectId()
                    self.db.mutation_receipts.insert_one({
                        "_id": receipt_id, "actorId": actor_id, "key": key, "operationId": operation,
                        "target": _target(target), "canonicalVersion": CANONICAL_VERSION, "fingerprint": fingerprint,
                        "status": "processing", "resultRefs": {}, "createdAt": _now(),
                    }, session=session)
                    status, content = callback(session)
                    if not 200 <= status < 300 or not isinstance(content, dict):
                        raise ValueError("management callbacks must return a successful JSON object")
                    response = self._save_response(receipt_id, operation, content, status, session)
                except BaseException as exc:
                    self._abort(session)
                    if isinstance(exc, DuplicateKeyError) or (isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError")):
                        replay = self._lookup(actor_id, key, operation, target, payload)
                        if replay is not None:
                            return replay
                        if attempt < 2:
                            continue
                        raise MutationError(409, "IDEMPOTENCY_IN_PROGRESS", "同一操作正在竞争，请使用原幂等键重试", retry=True) from exc
                    raise
                uncertain = False
                for commit_attempt in range(3):
                    try:
                        session.commit_transaction()
                        return response
                    except PyMongoError as exc:
                        if exc.has_error_label("UnknownTransactionCommitResult"):
                            uncertain = True
                            if commit_attempt < 2:
                                continue
                            # Missing on this fresh read is still not proof of
                            # rollback. Never execute the business callback again.
                            try:
                                replay = self._lookup(actor_id, key, operation, target, payload)
                            except PyMongoError:
                                replay = None
                            if replay is not None:
                                return replay
                            raise MutationError(503, "IDEMPOTENCY_RESULT_UNKNOWN", "操作提交结果尚未确认，请保留原幂等键重试", retry=True) from exc
                        if uncertain:
                            raise MutationError(503, "IDEMPOTENCY_RESULT_UNKNOWN", "操作提交结果尚未确认，请保留原幂等键重试", retry=True) from exc
                        self._abort(session)
                        if exc.has_error_label("TransientTransactionError"):
                            replay = self._lookup(actor_id, key, operation, target, payload)
                            if replay is not None:
                                return replay
                            if attempt < 2:
                                break
                            raise MutationError(409, "IDEMPOTENCY_IN_PROGRESS", "操作正在竞争，请使用原幂等键重试", retry=True) from exc
                        raise
        raise MutationError(409, "IDEMPOTENCY_IN_PROGRESS", "操作正在竞争，请使用原幂等键重试", retry=True)
