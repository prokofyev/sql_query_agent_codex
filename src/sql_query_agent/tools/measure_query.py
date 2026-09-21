"""Инструмент замера времени выполнения для вызова моделью.

Возвращает структурированный результат: медиану, минимум, максимум, число
повторов и краткое описание плана. Ошибка не возбуждается наружу — модель
получает её как данные и может объяснить пользователю.
"""

from typing import Any

from langchain_core.tools import StructuredTool
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.db.explain import (
    QueryMeasurer,
    SqlNotAllowedError,
    SqlTimeoutError,
)
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

MEASURE_QUERY_DESCRIPTION = (
    "Измерить время выполнения SQL-запроса на чтение. "
    "Выполняется в транзакции только для чтения, с прогревом и несколькими повторами; "
    "возвращает медиану, минимум и максимум в миллисекундах. "
    "Принимается только одиночный запрос SELECT или WITH."
)


def summarize_plan(plan: dict[str, Any], *, limit: int = 12) -> list[str]:
    """Собрать краткий список узлов плана: тип и отношение."""

    nodes: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if len(nodes) >= limit:
            return
        node_type = node.get("Node Type", "?")
        relation = node.get("Relation Name")
        nodes.append(f"{node_type} {relation}" if relation else str(node_type))
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(plan)
    return nodes


class QueryMeasureTool:
    """Замер запроса, оформленный как инструмент."""

    def __init__(self, measurer: QueryMeasurer) -> None:
        self._measurer = measurer

    async def run(self, sql: str) -> dict[str, Any]:
        """Измерить запрос и вернуть результат словарём."""

        try:
            outcome = await self._measurer.measure(sql)
        except SqlNotAllowedError as error:
            return {"ok": False, "error": str(error), "error_kind": "not_allowed"}
        except SqlTimeoutError as error:
            return {"ok": False, "error": str(error), "error_kind": "timeout"}
        except Exception as error:  # noqa: BLE001 - ошибка отдаётся модели как данные
            logger.warning("замер не удался", error=str(error))
            return {"ok": False, "error": str(error), "error_kind": "database"}

        return {
            "ok": True,
            "median_ms": outcome.stats.median_ms,
            "minimum_ms": outcome.stats.minimum_ms,
            "maximum_ms": outcome.stats.maximum_ms,
            "runs": outcome.stats.runs,
            "samples_ms": outcome.stats.samples,
            "plan_nodes": summarize_plan(outcome.plan),
        }

    def as_tool(self) -> StructuredTool:
        """Собрать инструмент для передачи модели."""

        return StructuredTool.from_function(
            coroutine=self.run,
            name="measure_query",
            description=MEASURE_QUERY_DESCRIPTION,
        )


def build_measurer(
    pool: AsyncConnectionPool,
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
    warmup_runs: int,
    repeat_runs: int,
) -> QueryMeasurer:
    """Собрать замер с настройками приложения."""

    return QueryMeasurer(
        pool,
        statement_timeout_ms=statement_timeout_ms,
        lock_timeout_ms=lock_timeout_ms,
        warmup_runs=warmup_runs,
        repeat_runs=repeat_runs,
    )
