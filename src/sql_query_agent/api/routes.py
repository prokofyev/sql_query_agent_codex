"""Эндпоинты обработки запросов, решений, истории и метрик."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from sql_query_agent.api.deps import AppDeps
from sql_query_agent.api.errors import (
    INVALID_SQL,
    NOT_FOUND,
    PRESETS_UNAVAILABLE,
    ApiError,
)
from sql_query_agent.api.schemas import (
    DecisionRequest,
    HealthResponse,
    HistoryResponse,
    PresetSchema,
    PresetsResponse,
    RunReportSchema,
    StartRunRequest,
)
from sql_query_agent.db.explain import SqlNotAllowedError, prepare_statement
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.presets import PresetsError, load_presets
from sql_query_agent.run.report import RunReport
from sql_query_agent.run.session import SessionNotFoundError

logger = get_logger(__name__)

router = APIRouter()


def get_deps(request: Request) -> AppDeps:
    """Достать зависимости приложения из состояния запроса."""

    return request.app.state.deps


Deps = Annotated[AppDeps, Depends(get_deps)]


def validate_sql(sql: str) -> str:
    """Проверить ввод до обращения к агенту.

    Та же проверка, что и в замере: одиночный запрос на чтение. Отдельная
    проверка на входе нужна, чтобы не запускать прогон ради сообщения об
    ошибке и не тратить обращения к модели.
    """

    try:
        return prepare_statement(sql)
    except SqlNotAllowedError as error:
        raise ApiError(INVALID_SQL, str(error), status_code=400) from error


@router.get("/presets", response_model=PresetsResponse)
async def presets() -> PresetsResponse:
    """Библиотека предустановленных запросов."""

    try:
        library = load_presets()
    except PresetsError as error:
        raise ApiError(PRESETS_UNAVAILABLE, str(error), status_code=500) from error
    return PresetsResponse(
        presets=[PresetSchema.from_domain(preset) for preset in library]
    )


@router.post("/runs", response_model=RunReportSchema)
async def start_run(payload: StartRunRequest, deps: Deps) -> RunReportSchema:
    """Запустить прогон по SQL-запросу."""

    sql = validate_sql(payload.sql)
    report = await deps.service.start(sql)
    return RunReportSchema.from_domain(report)


@router.get("/runs", response_model=HistoryResponse)
async def list_runs(deps: Deps, limit: int = 20) -> HistoryResponse:
    """История прогонов процесса."""

    reports = await deps.service.history(limit=max(1, min(limit, 100)))
    return HistoryResponse(
        runs=[RunReportSchema.from_domain(report) for report in reports]
    )


@router.get("/runs/{thread_id}", response_model=RunReportSchema)
async def get_run(thread_id: str, deps: Deps) -> RunReportSchema:
    """Отчёт по конкретному прогону."""

    return RunReportSchema.from_domain(await _report(deps, thread_id))


@router.post("/runs/{thread_id}/decision", response_model=RunReportSchema)
async def decide(
    thread_id: str,
    payload: DecisionRequest,
    deps: Deps,
) -> RunReportSchema:
    """Принять решение пользователя и продолжить прогон."""

    try:
        report = await deps.service.decide(thread_id, accepted=payload.accepted)
    except SessionNotFoundError as error:
        raise ApiError(NOT_FOUND, str(error), status_code=404) from error
    return RunReportSchema.from_domain(report)


async def _report(deps: AppDeps, thread_id: str) -> RunReport:
    """Отчёт или ошибка «не найдено»."""

    try:
        return await deps.service.report(thread_id)
    except SessionNotFoundError as error:
        raise ApiError(NOT_FOUND, str(error), status_code=404) from error


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Проверка жизнеспособности без обращения к внешним системам."""

    return HealthResponse(status="ok")


__all__ = ["router", "validate_sql"]
