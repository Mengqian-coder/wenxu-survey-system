"""HTTP API. Start from backend/: python -m uvicorn app.main:app --port 8000 --no-access-log"""
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .bootstrap import check_replica_set, initialize_database
from .config import Settings
from .domain import DomainError
from .errors import APIError
from .frontend import FRONTEND_DIST, SPAStaticFiles
from .models import LoginRequest, PreviewRequest, RegisterRequest, RevisionRequest, SubmitRequest, SurveyCreate, SurveyPatch
from .service import SurveyService, serialize, utcnow

bearer = HTTPBearer(auto_error=False)


class ConfiguredCORSMiddleware:
    """Use the validated lifespan settings, including values read from .env."""
    def __init__(self, app, settings_provider):
        self.app, self.settings_provider = app, settings_provider
        self.configured = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if self.configured is None:
            origins = self.settings_provider().cors_origins
            self.configured = CORSMiddleware(
                self.app, allow_origins=origins, allow_credentials=False,
                allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"],
            )
        return await self.configured(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(self, app, settings_provider):
        self.app, self.settings_provider = app, settings_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["received_at"] = utcnow()
        if scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = self.settings_provider().max_request_bytes
        headers = dict(scope.get("headers", []))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > limit:
            return await JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体大小不合法"}}, status_code=413)(scope, receive, send)
        data, length = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            length += len(chunk)
            if length > limit:
                return await JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体超过大小上限"}}, status_code=413)(scope, receive, send)
            data.append(chunk)
            if not message.get("more_body", False):
                break
        body, delivered = b"".join(data), False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        configured = settings or Settings()
        client = MongoClient(configured.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        try:
            check_replica_set(client)
            db = client[configured.mongodb_database]
            if configured.auto_init_db:
                initialize_database(db)
            app.state.settings, app.state.db, app.state.client = configured, db, client
            app.state.service = SurveyService(db, configured)
            yield
        finally:
            client.close()

    app = FastAPI(title="MongoDB 在线问卷系统", version="1.0.0", lifespan=lifespan,
                  description="第一阶段业务后端。除注册、登录和健康检查外，业务接口均需 Bearer 令牌。")
    app.add_middleware(BodyLimitMiddleware, settings_provider=lambda: app.state.settings)
    app.add_middleware(ConfiguredCORSMiddleware, settings_provider=lambda: app.state.settings)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        payload = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            payload["details"] = exc.details
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse({"error": payload}, status_code=exc.status, headers=headers)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": str(exc), "details": exc.details}}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        # Do not reflect passwords, bearer/submission tokens or anonymous answers.
        issues = [{"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "请求参数不合法", "details": issues}}, status_code=422)

    @app.exception_handler(PyMongoError)
    async def database_error(request, exc):
        return JSONResponse({"error": {"code": "DATABASE_ERROR", "message": "数据库操作未完成，请稍后重试"}}, status_code=503)

    def service(request: Request):
        return request.app.state.service

    def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), svc=Depends(service)):
        if credentials is None:
            raise APIError(401, "AUTH_REQUIRED", "请先登录")
        return svc.authenticate(credentials.credentials)

    @app.get("/health", tags=["系统"])
    def health(svc=Depends(service)):
        svc.db.command("ping")
        return {"status": "ok"}

    @app.post("/api/auth/register", status_code=201, tags=["账号"])
    def register(body: RegisterRequest, svc=Depends(service)):
        return svc.register(body)

    @app.post("/api/auth/login", tags=["账号"])
    def login(body: LoginRequest, svc=Depends(service)):
        return svc.login(body)

    @app.get("/api/auth/me", tags=["账号"])
    def me(user=Depends(current_user), svc=Depends(service)):
        return svc.safe_user(user)

    @app.post("/api/surveys", status_code=201, tags=["问卷管理"])
    def create_survey(body: SurveyCreate, user=Depends(current_user), svc=Depends(service)):
        return svc.create(body, user)

    @app.get("/api/surveys", tags=["问卷管理"])
    def list_surveys(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                     status: Literal["draft", "published", "closed"] | None = None,
                     search: str = Query("", max_length=200), user=Depends(current_user), svc=Depends(service)):
        return svc.list_surveys(user, page, pageSize, status=status, search=search)

    @app.get("/api/surveys/{survey_id}", tags=["问卷管理"])
    def get_survey(survey_id: str, user=Depends(current_user), svc=Depends(service)):
        return serialize(svc.owned(survey_id, user))

    @app.patch("/api/surveys/{survey_id}", tags=["问卷管理"])
    def patch_survey(survey_id: str, body: SurveyPatch, user=Depends(current_user), svc=Depends(service)):
        return svc.patch(survey_id, body, user)

    @app.post("/api/surveys/{survey_id}/publish", tags=["问卷管理"])
    def publish(survey_id: str, body: RevisionRequest, user=Depends(current_user), svc=Depends(service)):
        return svc.publish(survey_id, body.revision, user)

    @app.post("/api/surveys/{survey_id}/close", tags=["问卷管理"])
    def close(survey_id: str, body: RevisionRequest, user=Depends(current_user), svc=Depends(service)):
        return svc.close(survey_id, body.revision, user)

    @app.get("/api/surveys/{survey_id}/responses", tags=["答卷与统计"])
    def responses(survey_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(service)):
        return svc.responses(survey_id, user, page, pageSize)

    @app.get("/api/surveys/{survey_id}/statistics", tags=["答卷与统计"])
    def statistics(survey_id: str, questionId: str | None = None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(service)):
        return svc.statistics(survey_id, user, questionId, page, pageSize)

    @app.get("/api/public/surveys/{public_id}", tags=["填写问卷"])
    def public_survey(public_id: str, user=Depends(current_user), svc=Depends(service)):
        return svc.public_get(public_id, user)

    @app.post("/api/public/surveys/{public_id}/start", tags=["填写问卷"])
    def start(public_id: str, user=Depends(current_user), svc=Depends(service)):
        return svc.start(public_id, user)

    @app.post("/api/public/surveys/{public_id}/preview", tags=["填写问卷"])
    def preview(public_id: str, body: PreviewRequest, user=Depends(current_user), svc=Depends(service)):
        return svc.preview(public_id, body, user)

    @app.post("/api/public/surveys/{public_id}/submit", status_code=201, tags=["填写问卷"])
    def submit(public_id: str, body: SubmitRequest, request: Request, user=Depends(current_user), svc=Depends(service)):
        return svc.submit(public_id, body, user, request.state.received_at)

    if (FRONTEND_DIST / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
