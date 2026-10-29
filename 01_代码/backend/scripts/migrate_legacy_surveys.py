"""Preview legacy survey migration; pass --apply to authorize actual writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.bootstrap import check_replica_set
from app.config import Settings
from app.migration import LegacySurveyMigrator, MigrationError


def parser():
    result = argparse.ArgumentParser(description="显式迁移旧问卷为固定版本引用。默认仅预览，不创建集合、不改验证器、不读写答卷。")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="实际执行逐卷事务；执行前请完成数据库备份")
    mode.add_argument("--dry-run", action="store_true", help="仅预览（默认），不修改数据库")
    result.add_argument("--survey-id", action="append", dest="survey_ids", help="限定问卷 ObjectId，可重复传入")
    result.add_argument("--batch-size", type=int, default=100, help="每页扫描数量，1 至 1000，默认 100")
    result.add_argument("--limit", type=int, help="本次最多处理的问卷数量")
    result.add_argument("--after-id", help="从此 ObjectId 之后继续扫描（不包含该 ID）")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError:
        print(json.dumps({"error": "INVALID_CONFIGURATION", "message": "后端 .env/环境变量配置不完整或不合法；未连接数据库"}, ensure_ascii=False))
        return 2
    summary = {"mode": "apply" if args.apply else "dry-run", "database": settings.mongodb_database,
               "processed": 0, "question_count": 0, "migrated": 0, "would_migrate": 0,
               "already_current": 0, "not_found": 0, "failed": 0, "last_survey_id": None}
    try:
        with MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
            check_replica_set(client)
            migrator = LegacySurveyMigrator(client[settings.mongodb_database])
            if args.apply:
                migrator.check_prerequisites()
            for survey_id in migrator.candidates(survey_ids=args.survey_ids, after_id=args.after_id,
                                                batch_size=args.batch_size, limit=args.limit):
                summary["processed"] += 1
                summary["last_survey_id"] = str(survey_id)
                try:
                    result = migrator.migrate_one(survey_id, apply=args.apply)
                    summary[result.status] += 1
                    summary["question_count"] += result.question_count
                    print(json.dumps(result.as_dict(), ensure_ascii=False))
                except MigrationError as exc:
                    summary["failed"] += 1
                    print(json.dumps({"survey_id": str(survey_id), "status": "failed", "error": exc.code, "message": str(exc)}, ensure_ascii=False))
                    # An uncertain commit must be resolved before advancing a
                    # resume cursor beyond this survey.
                    if exc.code == "MIGRATION_RESULT_UNKNOWN":
                        summary["last_survey_id"] = None
                        break
    except MigrationError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 2
    except (PyMongoError, RuntimeError):
        # Driver exceptions can contain URI credentials/topology details.
        print(json.dumps({"error": "MIGRATION_UNAVAILABLE", "message": "迁移预检查或数据库操作失败；请检查副本集、权限及新版集合和索引。未输出连接串或凭据。"}, ensure_ascii=False))
        return 2
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
