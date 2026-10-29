"""Migration exercises real transactions on the isolated replica fixture only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo.client_session import ClientSession
from pymongo.errors import OperationFailure
from pymongo.results import UpdateResult
import pytest

from app.migration import LegacySurveyMigrator, MigrationError
from app.service import utcnow


def legacy(db, *, questions=None, status="draft", owner=None):
    now = utcnow() - timedelta(days=30)
    survey = dict(
        _id=ObjectId(), ownerId=owner or ObjectId(), publicId=ObjectId().binary.hex() + "0" * 8,
        title="旧问卷", description="历史内容", status=status,
        settings=dict(anonymous=True, allowRepeat=True), deadlineAt=None,
        questions=questions if questions is not None else [dict(
            id="local_text", type="text", title="旧题内容", description="原说明", required=True,
            validation=dict(minLength=1, maxLength=100), jumpRules=[],
        )], schemaVersion=1, revision=7, createdAt=now, updatedAt=now,
        publishedAt=now if status != "draft" else None,
        closedAt=now if status == "closed" else None, closeReason="manual" if status == "closed" else None,
    )
    db.surveys.insert_one(survey)
    return db.surveys.find_one({"_id": survey["_id"]})


def state(db):
    return {name: list(db[name].find().sort("_id", 1)) for name in (
        "surveys", "questions", "question_versions", "responses", "participations", "mutation_receipts")}


def unknown_commit():
    return OperationFailure("injected unknown commit", code=91,
                            details={"errorLabels": ["UnknownTransactionCommitResult"]})


def test_default_dry_run_is_entirely_read_only(db):
    survey = legacy(db)
    before = state(db)
    result = LegacySurveyMigrator(db).migrate_one(survey["_id"])
    assert result.status == "would_migrate"
    assert result.question_count == 1
    assert state(db) == before


def test_apply_preserves_snapshot_and_records_unique_origin(db):
    survey = legacy(db)
    migrator = LegacySurveyMigrator(db)
    result = migrator.migrate_one(survey["_id"], apply=True)
    current = db.surveys.find_one({"_id": survey["_id"]})
    snapshot = current["questions"][0]
    assert result.status == "migrated"
    assert current["schemaVersion"] == 2 and current["revision"] == 8
    assert {key: value for key, value in snapshot.items() if key not in ("questionId", "versionId", "versionNo")} == survey["questions"][0]
    assert {key: value for key, value in current.items() if key not in ("schemaVersion", "revision", "questions")} == {
        key: value for key, value in survey.items() if key not in ("schemaVersion", "revision", "questions")}
    question = db.questions.find_one({"_id": snapshot["questionId"]})
    version = db.question_versions.find_one({"_id": snapshot["versionId"]})
    assert question["origin"] == {"kind": "legacy", "surveyId": survey["_id"], "instanceId": "local_text"}
    assert question["ownerId"] == survey["ownerId"] and question["latestVersionId"] == version["_id"]
    assert version["operation"] == "migrate" and version["versionNo"] == 1 and version["baseVersionId"] is None
    assert set(version["content"]) == {"type", "title", "description", "validation"}
    assert migrator.migrate_one(survey["_id"], apply=True).status == "already_current"
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 1
    assert db.surveys.find_one({"_id": survey["_id"]}) == current


def test_same_text_never_merges_instances_or_surveys(db):
    first = legacy(db)
    duplicate = deepcopy(first["questions"][0])
    duplicate["id"] = "other_local"
    db.surveys.update_one({"_id": first["_id"]}, {"$push": {"questions": duplicate}})
    second = legacy(db, owner=first["ownerId"])
    migrator = LegacySurveyMigrator(db)
    for survey_id in migrator.candidates(batch_size=1):
        assert migrator.migrate_one(survey_id, apply=True).status == "migrated"
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 3
    ids = [item["questionId"] for row in db.surveys.find({}) for item in row["questions"]]
    assert len(set(ids)) == 3
    assert first["_id"] != second["_id"]


@pytest.mark.parametrize("status", ["published", "closed"])
def test_published_closed_answers_participations_and_business_dates_unchanged(db, status):
    survey = legacy(db, status=status)
    response = dict(_id=ObjectId(), surveyId=survey["_id"], submissionKey="a" * 64,
                    answers=[dict(questionId="local_text", value="过去的回答")], submittedAt=survey["createdAt"], schemaVersion=1)
    participation = dict(_id=ObjectId(), surveyId=survey["_id"], userId=ObjectId())
    db.responses.insert_one(response)
    db.participations.insert_one(participation)
    LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    current = db.surveys.find_one({"_id": survey["_id"]})
    for field in ("status", "createdAt", "updatedAt", "publishedAt", "closedAt", "closeReason", "deadlineAt", "settings"):
        assert current[field] == survey[field]
    assert db.responses.find_one({"_id": response["_id"]}) == response
    assert db.participations.find_one({"_id": participation["_id"]}) == participation


def test_jump_rules_order_required_and_all_four_contents_unchanged(db):
    questions = [
        dict(id="single", type="single", title="单选", description="", required=False, validation={},
             options=[dict(id="a", label="A"), dict(id="b", label="B")],
             jumpRules=[dict(id="skip", condition=dict(operator="eq", value="b"), target=dict(kind="question", questionId="number"))]),
        dict(id="multiple", type="multiple", title="多选", description="", required=False,
             validation=dict(minSelections=0, maxSelections=2), options=[dict(id="a", label="A"), dict(id="b", label="B")], jumpRules=[]),
        dict(id="text", type="text", title="文本", description="", required=False, validation={}, jumpRules=[]),
        dict(id="number", type="number", title="数字", description="", required=True, validation=dict(integerOnly=True, min=0, max=120), jumpRules=[]),
    ]
    survey = legacy(db, questions=questions)
    LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    snapshots = db.surveys.find_one({"_id": survey["_id"]})["questions"]
    assert [{key: value for key, value in item.items() if key not in ("questionId", "versionId", "versionNo")} for item in snapshots] == questions


def test_empty_draft_can_migrate(db):
    survey = legacy(db, questions=[])
    result = LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    assert result.status == "migrated" and result.question_count == 0
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 0


def test_fault_after_question_insert_rolls_back_entire_survey(db):
    survey = legacy(db)
    before = state(db)
    original_insert = type(db.question_versions).insert_many

    def fail_version(collection, *args, **kwargs):
        if collection.name == "question_versions":
            raise RuntimeError("injected")
        return original_insert(collection, *args, **kwargs)

    with patch.object(type(db.question_versions), "insert_many", fail_version):
        with pytest.raises(RuntimeError, match="injected"):
            LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    assert state(db) == before


def test_failed_survey_cas_rolls_back_questions_and_versions(db):
    survey = legacy(db)
    before = state(db)
    with patch.object(type(db.surveys), "update_one", return_value=UpdateResult({"n": 0, "nModified": 0, "ok": 1}, True)):
        with pytest.raises(MigrationError, match="回滚") as caught:
            LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    assert caught.value.code == "REVISION_CONFLICT"
    assert state(db) == before


def test_concurrent_edit_retries_from_fresh_survey_without_lost_update(db, monkeypatch):
    survey = legacy(db)
    migrator = LegacySurveyMigrator(db)
    prepare = migrator._prepare_questions
    calls = 0

    def concurrent(row, now):
        nonlocal calls
        calls += 1
        result = prepare(row, now)
        if calls == 1:
            db.surveys.update_one({"_id": row["_id"]}, {"$set": {"title": "并发修改保留"}, "$inc": {"revision": 1}})
        return result

    monkeypatch.setattr(migrator, "_prepare_questions", concurrent)
    assert migrator.migrate_one(survey["_id"], apply=True).status == "migrated"
    current = db.surveys.find_one({"_id": survey["_id"]})
    assert calls == 2 and current["revision"] == 9 and current["title"] == "并发修改保留"
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 1


def test_unknown_commit_confirms_original_transaction_without_recreating(db, monkeypatch):
    survey = legacy(db)
    original_commit = ClientSession.commit_transaction
    calls = 0

    def dropped_response(session):
        nonlocal calls
        calls += 1
        original_commit(session)
        raise unknown_commit()

    monkeypatch.setattr(ClientSession, "commit_transaction", dropped_response)
    result = LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    assert result.status == "migrated" and calls == 3
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 1
    assert LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True).status == "already_current"


def test_unconfirmed_commit_does_not_repeat_business_callback(db, monkeypatch):
    survey = legacy(db)
    migrator = LegacySurveyMigrator(db)
    original_prepare = migrator._prepare_questions
    calls = 0

    def count_prepare(row, now):
        nonlocal calls
        calls += 1
        return original_prepare(row, now)

    monkeypatch.setattr(migrator, "_prepare_questions", count_prepare)
    monkeypatch.setattr(ClientSession, "commit_transaction", lambda session: (_ for _ in ()).throw(unknown_commit()))
    with pytest.raises(MigrationError) as caught:
        migrator.migrate_one(survey["_id"], apply=True)
    assert caught.value.code == "MIGRATION_RESULT_UNKNOWN" and calls == 1
    assert db.surveys.find_one({"_id": survey["_id"]})["schemaVersion"] == 1
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 0


def test_two_concurrent_migrators_create_one_identity_per_instance(db, monkeypatch):
    survey = legacy(db)
    original_prepare = LegacySurveyMigrator._prepare_questions
    barrier, lock = threading.Barrier(2), threading.Lock()
    calls = 0

    def prepare(row, now):
        nonlocal calls
        with lock:
            calls += 1
            first_attempt = calls <= 2
        result = original_prepare(row, now)
        if first_attempt:
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(LegacySurveyMigrator, "_prepare_questions", staticmethod(prepare))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True), range(2)))
    assert all(result.status in ("migrated", "already_current") for result in results)
    assert db.questions.count_documents({}) == db.question_versions.count_documents({}) == 1
    assert db.surveys.find_one({"_id": survey["_id"]})["revision"] == 8


def test_migrated_draft_can_edit_instances_through_real_api(api, db):
    from test_api import register

    user, headers = register(api, "migration-owner")
    survey = legacy(db, owner=ObjectId(user["_id"]))
    LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    current = api.get(f"/api/surveys/{survey['_id']}", headers=headers)
    assert current.status_code == 200, current.text
    current = current.json()
    instance = {key: current["questions"][0][key] for key in ("id", "questionId", "versionId", "required", "jumpRules")}
    instance["required"] = False
    updated = api.patch(f"/api/surveys/{survey['_id']}", headers={**headers, "Idempotency-Key": str(uuid4())},
                        json={"revision": current["revision"], "questions": [instance]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["questions"][0]["required"] is False
    assert db.question_versions.count_documents({}) == 1
    question = api.get(f"/api/questions/{instance['questionId']}", headers=headers)
    assert question.status_code == 200 and question.json()["origin"]["surveyId"] == str(survey["_id"])


def test_inflight_fill_and_submission_retry_survive_migration(api, db):
    from test_api import register, start, submit

    user, headers = register(api, "migration-fill")
    survey = legacy(db, owner=ObjectId(user["_id"]), status="published")
    before = api.get(f"/api/public/surveys/{survey['publicId']}", headers=headers)
    assert before.status_code == 200, before.text
    started = start(api, headers, survey)
    LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    after = api.get(f"/api/public/surveys/{survey['publicId']}", headers=headers)
    assert after.json() == before.json()
    answers = [dict(questionId="local_text", value="迁移前开始填写")]
    submitted = submit(api, headers, survey, started, answers)
    assert submitted.status_code == 201, submitted.text
    assert submit(api, headers, survey, started, answers).status_code == 201
    assert db.responses.count_documents({"surveyId": survey["_id"]}) == 1
    statistics = api.get(f"/api/surveys/{survey['_id']}/statistics", headers=headers)
    assert statistics.status_code == 200 and statistics.json()["totalResponses"] == 1


def test_preexisting_conflicting_origin_is_not_adopted_or_duplicated(db):
    survey = legacy(db)
    migrator = LegacySurveyMigrator(db)
    questions, versions, _ = migrator._prepare_questions(survey, utcnow())
    db.questions.insert_many(questions)
    db.question_versions.insert_many(versions)
    before = state(db)
    with pytest.raises(MigrationError) as caught:
        migrator.migrate_one(survey["_id"], apply=True)
    assert caught.value.code == "LEGACY_ORIGIN_CONFLICT"
    assert state(db) == before


def test_keyset_limit_selection_and_missing_target(db):
    rows = sorted([legacy(db)["_id"] for _ in range(4)])
    migrator = LegacySurveyMigrator(db)
    assert list(migrator.candidates(batch_size=1, limit=2)) == rows[:2]
    assert list(migrator.candidates(batch_size=2, after_id=rows[1])) == rows[2:]
    missing = ObjectId()
    assert list(migrator.candidates(survey_ids=[missing, rows[0], rows[0]])) == [rows[0], missing]
    assert migrator.migrate_one(missing).status == "not_found"


@pytest.mark.parametrize("kwargs", [{"batch_size": 0}, {"batch_size": 1001}, {"limit": 0}, {"after_id": "bad"}, {"survey_ids": ["bad"]}])
def test_invalid_scan_parameters_do_not_write(db, kwargs):
    before = state(db)
    with pytest.raises(MigrationError):
        list(LegacySurveyMigrator(db).candidates(**kwargs))
    assert state(db) == before


def test_missing_unique_index_refuses_apply_without_creating_it(db):
    survey = legacy(db)
    db.questions.drop_index("uq_questions_legacy_origin")
    before = state(db)
    with pytest.raises(MigrationError) as caught:
        LegacySurveyMigrator(db).migrate_one(survey["_id"], apply=True)
    assert caught.value.code == "V2_INDEX_REQUIRED"
    assert "uq_questions_legacy_origin" not in db.questions.index_information()
    assert state(db) == before


def test_cli_uses_env_defaults_and_dry_run_on_isolated_database(db, monkeypatch, capsys):
    survey = legacy(db)
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_legacy_surveys.py"
    spec = importlib.util.spec_from_file_location("migration_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = SimpleNamespace(mongodb_uri="mongodb://isolated-fixture/", mongodb_database=db.name)
    monkeypatch.setattr(module, "Settings", lambda: settings)

    class FixtureClient:
        def __enter__(self):
            return db.client

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(module, "MongoClient", lambda *args, **kwargs: FixtureClient())
    before = state(db)
    assert module.main(["--survey-id", str(survey["_id"])]) == 0
    output = capsys.readouterr().out
    assert '"mode": "dry-run"' in output and '"would_migrate": 1' in output
    assert "password" not in output.lower() and "mongodb://" not in output
    assert state(db) == before
