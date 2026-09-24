"""Внутрипроцессный клиент API для веб-интерфейса.

UI ходит в API через `ASGITransport`: без сети и порта, но по тому же
контракту, что и внешние клиенты. Контракт поэтому ровно один, а интерфейс
можно тестировать на том же приложении FastAPI.
"""

from typing import Any

import httpx

from sql_query_agent.logging_setup import get_logger
from sql_query_agent.run.report import RunReport, RunStatus, build_report
from sql_query_agent.ui.view import RunView, build_run_view, error_view

logger = get_logger(__name__)


class AdvisorApiClient:
    """Клиент эндпоинтов агента поверх ASGI-транспорта."""

    def __init__(self, app: Any) -> None:
        self._transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def _client(self) -> httpx.AsyncClient:
        """Клиент с базовым адресом и общим транспортом."""

        return httpx.AsyncClient(transport=self._transport, base_url="http://ui")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        sql: str = "",
    ) -> RunView:
        """Выполнить запрос и превратить ответ в модель экрана."""

        try:
            async with await self._client() as client:
                response = await client.request(method, path, json=payload)
        except httpx.HTTPError as error:  # pragma: no cover - защита от сети
            logger.warning("сервис недоступен", error=str(error))
            return error_view("Сервис недоступен", sql=sql)

        if response.status_code >= 400:
            return error_view(_error_message(response), sql=sql)
        return build_run_view(_report(response.json()), sql=sql)

    async def presets(self) -> list[dict[str, Any]]:
        """Библиотека предустановленных запросов."""

        try:
            async with await self._client() as client:
                response = await client.get("/presets")
        except httpx.HTTPError as error:  # pragma: no cover - защита от сети
            logger.warning("библиотека запросов недоступна", error=str(error))
            return []
        if response.status_code != 200:
            return []
        return list(response.json().get("presets") or [])

    async def schema(self) -> dict[str, Any]:
        """Схема базы для панели интерфейса."""

        try:
            async with await self._client() as client:
                response = await client.get("/schema")
        except httpx.HTTPError as error:  # pragma: no cover - защита от сети
            logger.warning("схема базы недоступна", error=str(error))
            return {}
        if response.status_code != 200:
            return {}
        return dict(response.json() or {})

    async def start(self, sql: str) -> RunView:
        """Запустить прогон по запросу."""

        return await self._request("POST", "/runs", payload={"sql": sql}, sql=sql)

    async def decide(self, thread_id: str, *, accepted: bool) -> RunView:
        """Передать решение пользователя."""

        return await self._request(
            "POST",
            f"/runs/{thread_id}/decision",
            payload={"accepted": accepted},
        )

    async def report(self, thread_id: str) -> RunView:
        """Отчёт по прогону."""

        return await self._request("GET", f"/runs/{thread_id}")


def _error_message(response: httpx.Response) -> str:
    """Текст ошибки из единого конверта API."""

    try:
        payload = response.json()
    except ValueError:  # pragma: no cover - не-JSON ответ
        return "Не удалось обработать запрос"
    return str(payload.get("message") or "Не удалось обработать запрос")


def _report(payload: dict[str, Any]) -> RunReport:
    """Собрать отчёт из ответа API.

    Ответ уже содержит готовые величины, поэтому он превращается в ту же
    модель отчёта: иначе экран и API пришлось бы держать в согласии вручную.
    """

    comparison = payload.get("comparison")
    return build_report(
        str(payload.get("thread_id") or ""),
        {
            "original_sql": payload.get("original_sql"),
            "current_sql": payload.get("current_sql"),
            "schema_checked": payload.get("schema_checked"),
            "schema_result": {"unknown": payload.get("unknown") or []},
            "warnings": payload.get("warnings") or [],
            "unfixable_message": payload.get("unfixable_message") or "",
            "fixed_sql": (payload.get("fix") or {}).get("fixed_sql"),
            "proposal": _proposal(payload),
            "before_stats": _before_stats(payload),
            "apply_result": _apply_result(comparison),
            "status": _status(payload),
            "fix_applied": (payload.get("decisions") or {}).get("fix") == "accepted",
            "fix_declined": (payload.get("decisions") or {}).get("fix") == "declined",
            "index_declined": (payload.get("decisions") or {}).get("index") == "declined",
        },
        _interrupts(payload),
    )


def _interrupts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Точка остановки из ответа API."""

    step = payload.get("step")
    return [{"step": step}] if step else []


def _status(payload: dict[str, Any]) -> str:
    """Статус состояния графа по статусу отчёта."""

    status = str(payload.get("status") or "")
    if status == RunStatus.COMPLETED.value:
        return "compared"
    if status == RunStatus.FAILED.value:
        return "measure_failed"
    return status


def _proposal(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Предложение об индексе из ответа API."""

    index = payload.get("index")
    if not index:
        return None
    return {"ddl": index.get("ddl"), "reason": index.get("reason")}


def _before_stats(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Замер до применения индекса."""

    comparison = payload.get("comparison") or {}
    before = comparison.get("before") or (payload.get("index") or {}).get("before")
    return before


def _apply_result(comparison: dict[str, Any] | None) -> dict[str, Any] | None:
    """Результат применения индекса из ответа API."""

    if not comparison:
        return None
    return {
        "applied": comparison.get("applied"),
        "error": comparison.get("error"),
        "index_ddl": comparison.get("index_ddl"),
        "before_median_ms": comparison.get("before_median_ms"),
        "after_median_ms": comparison.get("after_median_ms"),
        "speedup": comparison.get("speedup"),
        "verdict": comparison.get("verdict"),
        "improved": comparison.get("improved"),
        "reason": comparison.get("reason"),
        "before": comparison.get("before"),
        "after": comparison.get("after"),
    }


__all__ = ["AdvisorApiClient"]
