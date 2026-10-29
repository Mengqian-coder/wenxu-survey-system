"""Apply the same committed validators/indexes as database/init-mongodb.js."""
import json
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

from .config import Settings

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "schema-definitions.json"


def initialize_database(db):
    definitions = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for name, definition in definitions.items():
        options = dict(validator={"$jsonSchema": definition["schema"]}, validationLevel="strict", validationAction="error")
        # A fresh DB is the default. Existing collections are deliberately tightened,
        # never dropped, rewritten, or silently deduplicated.
        if name not in db.list_collection_names():
            try:
                db.create_collection(name, **options)
            except CollectionInvalid:
                db.command({"collMod": name, **options})
        else:
            db.command({"collMod": name, **options})
        for spec in definition["indexes"]:
            spec = dict(spec)
            key = spec.pop("key")
            db[name].create_index(list(key.items()), **spec)


def check_replica_set(client):
    hello = client.admin.command("hello")
    if not hello.get("setName"):
        raise RuntimeError("提交事务要求 MongoDB 副本集，请运行 scripts/dev_mongo.py start 或配置副本集 URI")


def main():
    settings = Settings()
    with MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        check_replica_set(client)
        initialize_database(client[settings.mongodb_database])
        print(f"已初始化 {settings.mongodb_database} 的业务集合、验证器和索引；未删除业务数据。")


if __name__ == "__main__":
    main()
