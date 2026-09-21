"""Сборка ASGI-приложения FastAPI.

Приложение создаётся из готовых зависимостей: так тесты собирают его с
подставными моделью и замером, а рабочая точка входа — с реальными пулом,
моделью GigaChat и журналом в базе.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from sql_query_agent.api.deps import AppDeps
from sql_query_agent.api.errors import install_error_handlers
from sql_query_agent.api.routes import router
from sql_query_agent.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)


def create_app(
    deps: AppDeps,
    *,
    with_lifespan: bool = True,
) -> FastAPI:
    """Собрать приложение с роутерами и обработчиками ошибок."""

    application = FastAPI(
        title="sql-index-advisor",
        version="0.1.0",
        lifespan=lifespan if with_lifespan else None,
    )
    application.state.deps = deps
    application.state.settings = deps.settings

    install_error_handlers(application)
    application.include_router(router)

    @application.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Метрики процесса в формате Prometheus."""

        registry = deps.metrics.registry if deps.metrics is not None else None
        payload = generate_latest(registry) if registry is not None else generate_latest()
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    return application


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Настроить логирование и освободить ресурсы при остановке."""

    deps: AppDeps = application.state.deps
    configure_logging(
        level=deps.settings.observability.log_level,
        json_logs=deps.settings.observability.json_logs,
    )
    logger.info("сервис запускается")
    try:
        aopen = getattr(deps, "aopen", None)
        if aopen is not None:
            await aopen()
        yield
    finally:
        await deps.aclose()
        logger.info("сервис остановлен")


def build_default_app(*, with_ui: bool = True) -> Any:
    """Собрать приложение с реальными зависимостями из окружения."""

    from sql_query_agent.runtime import build_default_deps

    deps = build_default_deps()
    application = create_app(deps)

    if with_ui:
        from sql_query_agent.ui.app import mount_ui
        from sql_query_agent.ui.client import AdvisorApiClient

        mount_ui(application, AdvisorApiClient(application))

    return application


__all__ = ["build_default_app", "create_app", "lifespan"]
