"""Учёт обращений к GigaChat.

Вызовы модели считаются по виду операции: извлечение имён, исправление,
предложение индекса. Это метки низкой кардинальности — сам запрос и его текст
в метки не попадают.
"""

from typing import Any

from langchain_core.messages import AIMessage

from sql_query_agent.agent.llm import AdvisorModel, IndexProposal, SqlFix
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.observability.metrics import RunMetrics

logger = get_logger(__name__)


class MeteredAdvisor:
    """Обёртка модели: считает вызовы и ошибки по видам операций."""

    def __init__(self, model: AdvisorModel, metrics: RunMetrics) -> None:
        self._model = model
        self._metrics = metrics

    async def extract_identifiers(self, sql: str, tools: list[Any]) -> AIMessage:
        """Извлечь имена, посчитав обращение к модели."""

        return await self._call(
            "extract", self._model.extract_identifiers(sql, tools)
        )

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> SqlFix:
        """Исправить запрос, посчитав обращение к модели."""

        return await self._call("fix", self._model.propose_fix(sql, unknown))

    async def propose_index(
        self,
        sql: str,
        plan_nodes: list[str],
        stats: dict[str, Any],
    ) -> IndexProposal:
        """Предложить индекс, посчитав обращение к модели."""

        return await self._call(
            "index", self._model.propose_index(sql, plan_nodes, stats)
        )

    async def _call(self, operation: str, awaitable: Any) -> Any:
        """Выполнить обращение к модели и посчитать его исход."""

        self._metrics.gigachat_calls.labels(operation=operation).inc()
        try:
            return await awaitable
        except Exception:
            self._metrics.gigachat_errors.labels(operation=operation).inc()
            logger.warning("обращение к модели не удалось", operation=operation)
            raise


def metered_advisor(model: AdvisorModel, metrics: RunMetrics | None) -> AdvisorModel:
    """Обернуть модель учётом метрик, если метрики заданы."""

    if metrics is None:
        return model
    return MeteredAdvisor(model, metrics)


__all__ = ["MeteredAdvisor", "metered_advisor"]
