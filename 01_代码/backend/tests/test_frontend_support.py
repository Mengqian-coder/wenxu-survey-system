"""Queries required by the actual dashboard; no existing user data is touched."""
from datetime import timedelta
from bson import ObjectId
from app.service import utcnow
from app.frontend import FRONTEND_DIST
import pytest
from test_api import api, register, create_survey, publish, assert_api_error, database_snapshot


def test_dashboard_filters_literal_search_counts_and_ownership(api, db):
    _, owner = register(api, "owner")
    _, other = register(api, "other")
    draft = create_survey(api, owner)
    sid = draft["_id"]
    edited = api.patch(f"/api/surveys/{sid}", headers=owner, json={"revision": draft["revision"], "title": "literal [abc].*"}).json()
    published = publish(api, owner, create_survey(api, owner))
    expired = publish(api, owner, create_survey(api, owner))
    db.surveys.update_one({"_id": ObjectId(expired["_id"])}, {"$set": {"deadlineAt": utcnow() - timedelta(seconds=1)}})
    create_survey(api, other)
    assert api.get("/api/surveys?status=draft", headers=owner).json()["total"] == 1
    assert api.get("/api/surveys?status=published", headers=owner).json()["total"] == 1
    assert api.get("/api/surveys?status=closed", headers=owner).json()["total"] == 1
    found = api.get("/api/surveys?search=%5Babc%5D.*", headers=owner).json()
    assert found["total"] == 1 and found["items"][0]["_id"] == sid
    assert found["items"][0]["questionCount"] == 1
    assert found["items"][0]["responseCount"] == 0
    assert "questions" not in found["items"][0]
    before = database_snapshot(db)
    assert_api_error(api.get("/api/surveys?status=invalid", headers=owner), 422, "INVALID_REQUEST")
    assert database_snapshot(db) == before


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").exists(), reason="Build frontend first to check static hosting")
def test_production_frontend_spa_routes_and_api_asset_404(api):
    for path in ("/", "/login", "/register", "/surveys/new", "/survey/example"):
        response = api.get(path)
        assert response.status_code == 200 and "text/html" in response.headers["content-type"]
        assert '<div id="root"></div>' in response.text
    assert api.get("/api/not-a-real-endpoint").status_code == 404
    assert api.get("/assets/does-not-exist.js").status_code == 404
    assert api.get("/health").json() == {"status": "ok"}
