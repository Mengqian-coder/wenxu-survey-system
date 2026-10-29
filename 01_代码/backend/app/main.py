"""HTTP API. Start from backend/: python -m uvicorn app.main:app --port 8000 --no-access-log"""
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .bootstrap import check_replica_set, initialize_database
from .bank_contracts import (BankCreate, BankDeleted, BankItemAdded, BankItemPage,
                             BankItemRemoved, BankPage, BankPatch, BankView)
from .bank_service import QuestionBankService
from .config import Settings
from .domain import DomainError
from .errors import APIError
from .frontend import FRONTEND_DIST, SPAStaticFiles
from .idempotency import KEY_PATTERN, MutationRunner
from .models import (ArchiveRequest, LoginRequest, PreviewRequest, QuestionCopy, QuestionCreate,
                     QuestionRestore, QuestionVersionCreate, RegisterRequest, RevisionRequest,
                     ShareRequest, SubmitRequest, SurveyCreate, SurveyPatch)
from .question_service import QuestionService
from .statistics_service import CrossSurveyStatisticsService
from .statistics_contracts import (StatisticQuestionPage, StatisticValuesPage,
                                   StatisticVersionPage, VersionStatistics)
from .service import SurveyService, serialize, utcnow
from .views import (ErrorResponse, PublicSurvey, QuestionPage, QuestionVersionResult,
                    QuestionView, SharePage, ShareResult, SurveyPage, SurveyView,
                    UsagePage, VersionPage, VersionResult)

bearer = HTTPBearer(auto_error=False)
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", pattern=KEY_PATTERN)]


def response_errors():
    return {status: {"model": ErrorResponse} for status in (401, 404, 409, 422, 503)}


def mutation_responses(success_status=200):
    responses = response_errors()
    responses[413] = {"model": ErrorResponse}
    responses[success_status] = {"headers": {
        "Idempotency-Replayed": {"description": "是否返回第一次成功操作的历史回执", "schema": {"type": "boolean"}},
    }}
    for status in (409, 503):
        responses[status]["headers"] = {"Retry-After": {"description": "处理中或提交结果不明时的建议重试秒数", "schema": {"type": "integer", "minimum": 1}}}
    return responses


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
                allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                expose_headers=["Idempotency-Replayed", "Retry-After"],
            )
        return await self.configured(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(self, app, settings_provider):
        self.app, self.settings_provider = app, settings_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["received_at"] = utcnow()
        if scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
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
            app.state.questions = QuestionService(db)
            app.state.banks = QuestionBankService(db)
            app.state.question_statistics = CrossSurveyStatisticsService(db)
            app.state.mutations = MutationRunner(db)
            yield
        finally:
            client.close()

    app = FastAPI(title="MongoDB 在线问卷系统", version="2.2.0", lifespan=lifespan,
                  description="独立题目、不可变版本、共享授权、引用查询、个人题库及跨本人问卷的精确版本统计。除注册、登录和健康检查外，业务接口均需 Bearer 令牌。")
    app.add_middleware(BodyLimitMiddleware, settings_provider=lambda: app.state.settings)
    app.add_middleware(ConfiguredCORSMiddleware, settings_provider=lambda: app.state.settings)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        payload = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            payload["details"] = exc.details
        headers = dict(getattr(exc, "headers", {}))
        if exc.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
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

    def questions(request: Request):
        return request.app.state.questions

    def mutations(request: Request):
        return request.app.state.mutations

    def banks(request: Request):
        return request.app.state.banks

    def question_statistics(request: Request):
        return request.app.state.question_statistics

    async def no_body(request: Request):
        if await request.body():
            raise APIError(422, "INVALID_REQUEST", "此操作不接受请求体")

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

    @app.post("/api/surveys", status_code=201, tags=["问卷管理"], response_model=SurveyView, response_model_exclude_unset=True, responses=mutation_responses(201))
    def create_survey(body: SurveyCreate, key: IdempotencyKey, user=Depends(current_user), svc=Depends(service), runner=Depends(mutations)):
        return runner.run(user, key, "survey.create", "/api/surveys", body.model_dump(), lambda session: (201, svc.create(body, user, session=session)))

    @app.get("/api/surveys", tags=["问卷管理"], response_model=SurveyPage, response_model_exclude_unset=True, responses=response_errors())
    def list_surveys(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                     status: Literal["draft", "published", "closed"] | None = None,
                     search: str = Query("", max_length=200), user=Depends(current_user), svc=Depends(service)):
        return svc.list_surveys(user, page, pageSize, status=status, search=search)

    @app.get("/api/surveys/{survey_id}", tags=["问卷管理"], response_model=SurveyView, response_model_exclude_unset=True, responses=response_errors())
    def get_survey(survey_id: str, user=Depends(current_user), svc=Depends(service)):
        return serialize(svc.owned(survey_id, user))

    @app.patch("/api/surveys/{survey_id}", tags=["问卷管理"], response_model=SurveyView, response_model_exclude_unset=True, responses=mutation_responses())
    def patch_survey(survey_id: str, body: SurveyPatch, key: IdempotencyKey, user=Depends(current_user), svc=Depends(service), runner=Depends(mutations)):
        # PATCH distinguishes omitted top-level fields, but supplied nested DTOs
        # include their defaults just as the business write does.
        payload = {name: value for name, value in body.model_dump().items() if name in body.model_fields_set}
        return runner.run(user, key, "survey.patch", f"/api/surveys/{survey_id}", payload, lambda session: (200, svc.patch(survey_id, body, user, session=session)))

    @app.post("/api/surveys/{survey_id}/publish", tags=["问卷管理"], response_model=SurveyView, response_model_exclude_unset=True, responses=mutation_responses())
    def publish(survey_id: str, body: RevisionRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(service), runner=Depends(mutations)):
        return runner.run(user, key, "survey.publish", f"/api/surveys/{survey_id}/publish", body.model_dump(), lambda session: (200, svc.publish(survey_id, body.revision, user, session=session)))

    @app.post("/api/surveys/{survey_id}/close", tags=["问卷管理"], response_model=SurveyView, response_model_exclude_unset=True, responses=mutation_responses())
    def close(survey_id: str, body: RevisionRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(service), runner=Depends(mutations)):
        return runner.run(user, key, "survey.close", f"/api/surveys/{survey_id}/close", body.model_dump(), lambda session: (200, svc.close(survey_id, body.revision, user, session=session)))

    @app.post("/api/questions", status_code=201, tags=["题目与版本"], response_model=QuestionVersionResult, response_model_exclude_unset=True, responses=mutation_responses(201))
    def create_question(body: QuestionCreate, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.create", "/api/questions", body.model_dump(), lambda session: (201, svc.create(body, user, session=session)))

    @app.get("/api/questions", tags=["题目与版本"], response_model=QuestionPage, response_model_exclude_unset=True, responses=response_errors())
    def list_questions(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                       scope: Literal["owned", "shared", "all"] = "owned", archive: Literal["exclude", "include", "only"] = "exclude",
                       search: str = Query("", max_length=200), user=Depends(current_user), svc=Depends(questions)):
        return svc.list_questions(user, page, pageSize, scope=scope, archive=archive, search=search)

    @app.get("/api/questions/{question_id}", tags=["题目与版本"], response_model=QuestionView, response_model_exclude_unset=True, responses=response_errors())
    def get_question(question_id: str, user=Depends(current_user), svc=Depends(questions)):
        return svc.get(question_id, user)

    @app.patch("/api/questions/{question_id}", tags=["题目与版本"], response_model=QuestionView, response_model_exclude_unset=True, responses=mutation_responses())
    def archive_question(question_id: str, body: ArchiveRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.archive", f"/api/questions/{question_id}", body.model_dump(), lambda session: (200, svc.archive(question_id, body, user, session=session)))

    @app.get("/api/questions/{question_id}/versions", tags=["题目与版本"], response_model=VersionPage, response_model_exclude_unset=True, responses=response_errors())
    def list_versions(question_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                      archive: Literal["exclude", "include", "only"] = "exclude", user=Depends(current_user), svc=Depends(questions)):
        return svc.versions(question_id, user, page, pageSize, archive=archive)

    @app.post("/api/questions/{question_id}/versions", status_code=201, tags=["题目与版本"], response_model=QuestionVersionResult, response_model_exclude_unset=True, responses=mutation_responses(201))
    def add_version(question_id: str, body: QuestionVersionCreate, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.version.create", f"/api/questions/{question_id}/versions", body.model_dump(), lambda session: (201, svc.add_version(question_id, body, user, session=session)))

    @app.get("/api/questions/{question_id}/versions/{version_id}", tags=["题目与版本"], response_model=VersionResult, response_model_exclude_unset=True, responses=response_errors())
    def get_version(question_id: str, version_id: str, user=Depends(current_user), svc=Depends(questions)):
        return svc.get_version(question_id, version_id, user)

    @app.patch("/api/questions/{question_id}/versions/{version_id}", tags=["题目与版本"], response_model=VersionResult, response_model_exclude_unset=True, responses=mutation_responses())
    def archive_version(question_id: str, version_id: str, body: ArchiveRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.version.archive", f"/api/questions/{question_id}/versions/{version_id}", body.model_dump(), lambda session: (200, svc.archive_version(question_id, version_id, body, user, session=session)))

    @app.post("/api/questions/{question_id}/restore", status_code=201, tags=["题目与版本"], response_model=QuestionVersionResult, response_model_exclude_unset=True, responses=mutation_responses(201))
    def restore_question(question_id: str, body: QuestionRestore, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.restore", f"/api/questions/{question_id}/restore", body.model_dump(), lambda session: (201, svc.restore(question_id, body, user, session=session)))

    @app.post("/api/questions/{question_id}/copies", status_code=201, tags=["题目与版本"], response_model=QuestionVersionResult, response_model_exclude_unset=True, responses=mutation_responses(201))
    def copy_question(question_id: str, body: QuestionCopy, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.copy", f"/api/questions/{question_id}/copies", body.model_dump(), lambda session: (201, svc.copy(question_id, body, user, session=session)))

    @app.get("/api/questions/{question_id}/shares", tags=["共享与引用"], response_model=SharePage, responses=response_errors())
    def question_shares(question_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                        state: Literal["active", "revoked", "all"] = "all", user=Depends(current_user), svc=Depends(questions)):
        return svc.shares(question_id, user, page, pageSize, state=state)

    grant_responses = mutation_responses(201)
    grant_responses[200] = {**mutation_responses()[200], "model": ShareResult}

    @app.post("/api/questions/{question_id}/shares", status_code=201, tags=["共享与引用"], response_model=ShareResult, responses=grant_responses)
    def grant_question(question_id: str, body: ShareRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.share.grant", f"/api/questions/{question_id}/shares", body.model_dump(), lambda session: svc.grant(question_id, body, user, session=session))

    @app.post("/api/questions/{question_id}/shares/{share_id}/revoke", tags=["共享与引用"], response_model=ShareResult, responses=mutation_responses())
    def revoke_question(question_id: str, share_id: str, body: RevisionRequest, key: IdempotencyKey, user=Depends(current_user), svc=Depends(questions), runner=Depends(mutations)):
        return runner.run(user, key, "question.share.revoke", f"/api/questions/{question_id}/shares/{share_id}/revoke", body.model_dump(), lambda session: (200, svc.revoke(question_id, share_id, body, user, session=session)))

    @app.get("/api/questions/{question_id}/usage", tags=["共享与引用"], response_model=UsagePage, responses=response_errors())
    def question_usage(question_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(questions)):
        return svc.usage(question_id, user, page, pageSize)

    @app.post("/api/question-banks", status_code=201, tags=["个人题库"], response_model=BankView, responses=mutation_responses(201))
    def create_bank(body: BankCreate, key: IdempotencyKey, user=Depends(current_user), svc=Depends(banks), runner=Depends(mutations)):
        return runner.run(user, key, "bank.create", "/api/question-banks", body.model_dump(), lambda session: (201, svc.create(body, user, session=session)))

    @app.get("/api/question-banks", tags=["个人题库"], response_model=BankPage, responses=response_errors())
    def list_banks(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(banks)):
        return svc.list_banks(user, page, pageSize)

    @app.get("/api/question-banks/{bank_id}", tags=["个人题库"], response_model=BankView, responses=response_errors())
    def get_bank(bank_id: str, user=Depends(current_user), svc=Depends(banks)):
        return svc.get(bank_id, user)

    @app.patch("/api/question-banks/{bank_id}", tags=["个人题库"], response_model=BankView, responses=mutation_responses())
    def patch_bank(bank_id: str, body: BankPatch, key: IdempotencyKey, user=Depends(current_user), svc=Depends(banks), runner=Depends(mutations)):
        payload = {name: value for name, value in body.model_dump().items() if name in body.model_fields_set}
        return runner.run(user, key, "bank.patch", f"/api/question-banks/{bank_id}", payload, lambda session: (200, svc.patch(bank_id, body, user, session=session)))

    @app.delete("/api/question-banks/{bank_id}", tags=["个人题库"], response_model=BankDeleted, responses=mutation_responses(), dependencies=[Depends(no_body)])
    def delete_bank(bank_id: str, key: IdempotencyKey, revision: int = Query(ge=1, le=2**53 - 1), user=Depends(current_user), svc=Depends(banks), runner=Depends(mutations)):
        return runner.run(user, key, "bank.delete", f"/api/question-banks/{bank_id}", {"revision": revision}, lambda session: (200, svc.delete(bank_id, revision, user, session=session)))

    @app.get("/api/question-banks/{bank_id}/items", tags=["个人题库"], response_model=BankItemPage, response_model_exclude_unset=True, responses=response_errors())
    def bank_items(bank_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(banks)):
        return svc.items(bank_id, user, page, pageSize)

    add_responses = mutation_responses(201)
    add_responses[200] = {**mutation_responses()[200], "model": BankItemAdded}

    @app.put("/api/question-banks/{bank_id}/items/{question_id}", status_code=201, tags=["个人题库"], response_model=BankItemAdded, responses=add_responses, dependencies=[Depends(no_body)])
    def add_bank_item(bank_id: str, question_id: str, key: IdempotencyKey, user=Depends(current_user), svc=Depends(banks), runner=Depends(mutations)):
        return runner.run(user, key, "bank.item.add", f"/api/question-banks/{bank_id}/items/{question_id}", {}, lambda session: svc.add(bank_id, question_id, user, session=session))

    @app.delete("/api/question-banks/{bank_id}/items/{question_id}", tags=["个人题库"], response_model=BankItemRemoved, responses=mutation_responses(), dependencies=[Depends(no_body)])
    def remove_bank_item(bank_id: str, question_id: str, key: IdempotencyKey, user=Depends(current_user), svc=Depends(banks), runner=Depends(mutations)):
        return runner.run(user, key, "bank.item.remove", f"/api/question-banks/{bank_id}/items/{question_id}", {}, lambda session: (200, svc.remove(bank_id, question_id, user, session=session)))

    @app.get("/api/question-statistics", tags=["跨问卷统计"], response_model=StatisticQuestionPage, responses=response_errors())
    def statistic_questions(page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                            search: str = Query('', max_length=200), user=Depends(current_user), svc=Depends(question_statistics)):
        return svc.list_questions(user, page, pageSize, search=search)

    @app.get("/api/question-statistics/{question_id}/versions", tags=["跨问卷统计"], response_model=StatisticVersionPage, responses=response_errors())
    def statistic_versions(question_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                           user=Depends(current_user), svc=Depends(question_statistics)):
        return svc.versions(question_id, user, page, pageSize)

    @app.get("/api/question-statistics/{question_id}/versions/{version_id}", tags=["跨问卷统计"], response_model=VersionStatistics, responses=response_errors())
    def version_statistics(question_id: str, version_id: str, user=Depends(current_user), svc=Depends(question_statistics)):
        return svc.statistics(question_id, version_id, user)

    @app.get("/api/question-statistics/{question_id}/versions/{version_id}/values", tags=["跨问卷统计"], response_model=StatisticValuesPage, responses=response_errors())
    def statistic_values(question_id: str, version_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
                         user=Depends(current_user), svc=Depends(question_statistics)):
        return svc.values(question_id, version_id, user, page, pageSize)

    @app.get("/api/surveys/{survey_id}/responses", tags=["答卷与统计"])
    def responses(survey_id: str, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(service)):
        return svc.responses(survey_id, user, page, pageSize)

    @app.get("/api/surveys/{survey_id}/statistics", tags=["答卷与统计"])
    def statistics(survey_id: str, questionId: str | None = None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user=Depends(current_user), svc=Depends(service)):
        return svc.statistics(survey_id, user, questionId, page, pageSize)

    @app.get("/api/public/surveys/{public_id}", tags=["填写问卷"], response_model=PublicSurvey, response_model_exclude_unset=True, responses={**response_errors(), 410: {"model": ErrorResponse}})
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
