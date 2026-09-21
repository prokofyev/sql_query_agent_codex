"""Инструмент применения индекса для вызова моделью.

Индекс создаётся в транзакции, замер выполняется там же, после чего
транзакция откатывается. Модель получает обе величины и итог сравнения.
"""

from typing import Any

from langchain_core.tools import StructuredTool

from sql_query_agent.db.index_apply import IndexApplier
from sql_query_agent.domain_comparison import Comparison, compare
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

APPLY_INDEX_DESCRIPTION = (
    "Проверить, ускорит ли запрос новый индекс. Создаёт индекс внутри транзакции, "
    "повторно измеряет запрос и откатывает транзакцию, поэтому индекс в базе не остаётся. "
    "Возвращает замеры до и после и итог сравнения. "
    "Аргумент index_ddl — полная команда CREATE INDEX."
)


class ApplyIndexTool:
    """Применение индекса, оформленное как инструмент."""

    def __init__(self, applier: IndexApplier, *, significance_ratio: float = 1.10) -> None:
        self._applier = applier
        self._significance_ratio = significance_ratio

    async def run(self, sql: str, index_ddl: str) -> dict[str, Any]:
        """Применить индекс и вернуть сравнение замеров."""

        outcome = await self._applier.apply(sql, index_ddl)
        comparison: Comparison = compare(
            outcome.before.stats,
            outcome.after.stats if outcome.after else None,
            significance_ratio=self._significance_ratio,
            applied=outcome.applied,
            error=outcome.error,
        )
        return {
            "applied": outcome.applied,
            "error": outcome.error,
            "index_ddl": outcome.index_ddl,
            "before_median_ms": comparison.before_median_ms,
            "after_median_ms": comparison.after_median_ms,
            "speedup": comparison.speedup,
            "verdict": comparison.verdict.value,
            "improved": comparison.improved,
            "reason": comparison.reason,
            "before": comparison.before.model_dump(mode="json") if comparison.before else None,
            "after": comparison.after.model_dump(mode="json") if comparison.after else None,
        }

    def as_tool(self) -> StructuredTool:
        """Собрать инструмент для передачи модели."""

        return StructuredTool.from_function(
            coroutine=self.run,
            name="apply_index",
            description=APPLY_INDEX_DESCRIPTION,
        )
