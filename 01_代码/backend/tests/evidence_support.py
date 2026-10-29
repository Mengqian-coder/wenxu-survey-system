"""Record reproducible, synthetic API/database evidence without credentials."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from bson import ObjectId


COLLECTIONS = frozenset({
    'questions', 'question_versions', 'question_shares', 'surveys', 'responses',
    'participations', 'question_banks', 'question_bank_items',
    'mutation_receipts', 'mutation_response_chunks',
})
SENSITIVE = frozenset({
    'password', 'passwordhash', 'access_token', 'refresh_token', 'authorization',
    'submissionkey', 'submissiontoken', 'participationkey', 'participationtoken',
    'respondentkey', 'unique_id', 'secret_key', 'mongodb_uri',
})


def clean(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if str(k).casefold() in SENSITIVE else clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'binaryOmitted': True, 'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError('Unsupported evidence value type: ' + type(value).__name__)


def canonical(value):
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class EvidenceTape:
    def __init__(self, case_id, title, api, db):
        self.case_id, self.title, self.api, self.db = case_id, title, api, db
        self.started = datetime.now(timezone.utc).isoformat()
        self.steps = []
        self.failed = False
        hello = db.client.admin.command('hello')
        self.environment = dict(database=db.name, mongoVersion=db.client.server_info()['version'],
            replicaSet=hello.get('setName'), writablePrimary=bool(hello.get('isWritablePrimary')),
            apiTransport='FastAPI TestClient / ASGI routes; real MongoDB replica set')

    def request(self, actor, headers, method, path, body=None, expected=200):
        if path.startswith('/api/auth/'):
            raise ValueError('Register/login credentials must never enter the evidence tape')
        outgoing = dict(headers)
        if method.upper() in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            outgoing['Idempotency-Key'] = str(uuid4())
        arguments = {'headers': outgoing}
        if body is not None:
            arguments['json'] = body
        start = time.monotonic()
        response = self.api.request(method, path, **arguments)
        try:
            result = clean(response.json())
        except ValueError:
            result = {'nonJsonResponse': True}
        passed = response.status_code == expected
        self.steps.append(dict(kind='http', actor=actor, method=method.upper(), path=path,
            requestBody=clean(body), expectedStatus=expected, actualStatus=response.status_code,
            responseBody=result, passed=passed, elapsedMs=round((time.monotonic()-start)*1000, 3)))
        if not passed:
            self.failed = True
        assert passed, f'{self.case_id}: {method} {path}: expected {expected}, got {response.status_code}; {canonical(result)}'
        return response

    def database(self, label, collections, queries=None):
        if any(name not in COLLECTIONS for name in collections):
            raise ValueError('Only explicit business/receipt collections may be recorded; users are excluded')
        documents = {name: clean(list(self.db[name].find((queries or {}).get(name, {})).sort('_id', 1)))
                     for name in collections}
        self.steps.append(dict(kind='database', label=label, collections={
            name: dict(count=len(rows), sha256=hashlib.sha256(canonical(rows).encode('utf-8')).hexdigest(), documents=rows)
            for name, rows in documents.items()}))
        return documents

    def check(self, label, condition, observed=None):
        passed = bool(condition)
        self.steps.append(dict(kind='assertion', label=label, passed=passed, observed=clean(observed)))
        if not passed:
            self.failed = True
        assert passed, f'{self.case_id}: {label}'

    def finish(self):
        error_type = sys.exc_info()[0]
        passed = not self.failed and error_type is None
        result = dict(caseId=self.case_id, title=self.title, startedAtUtc=self.started,
            finishedAtUtc=datetime.now(timezone.utc).isoformat(), passed=passed,
            exceptionType=error_type.__name__ if error_type else None,
            database=self.db.name, environment=self.environment,
            scope='Synthetic isolated API/MongoDB scenario; credentials redacted; binary values represented by length and SHA-256.',
            steps=self.steps)
        target = os.environ.get('WENXU_EVIDENCE_DIR')
        if target:
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            # A fresh output directory is assigned to every run by the runner.
            with (directory / (self.case_id + '.json')).open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
