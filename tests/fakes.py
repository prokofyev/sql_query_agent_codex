"""Подставные объекты для тестов графа: модель, схема, замер и индекс."""

from typing import Any

from langchain_core.messages import AIMessage

from sql_query_agent.agent.llm import IndexProposal, NameReplacement, SqlFix
from sql_query_agent.db.catalog import SchemaCatalog


class FakeModel:
    """Модель-заглушка: считает вызовы и возвращает заранее заданные ответы."""

    def __init__(
        self,
        *,
        tool_call: bool = True,
        entities: dict[str, Any] | None = None,
        fixed_sql: str = "select * from sku where product_color_id = 1",
        ddl: str = "CREATE INDEX fake_idx ON sku (product_id)",
        reason: str = "индекс по внешнему ключу",
        replacements: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tool_call = tool_call
        self.replacements = replacements
        self.entities = entities if entities is not None else {
            "tables": ["sku"],
            "columns": ["product_colr_id"],
        }
        self.fixed_sql = fixed_sql
        self.ddl = ddl
        self.reason = reason
        self.extract_calls = 0
        self.fix_calls = 0
        self.index_calls = 0

    async def extract_identifiers(self, sql: str, tools: list[Any]) -> AIMessage:
        """Вернуть ответ с вызовом инструмента или без него."""

        self.extract_calls += 1
        if not self.tool_call:
            return AIMessage(content="не буду вызывать инструмент")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "check_schema",
                    "args": dict(self.entities),
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> SqlFix:
        """Вернуть исправленный запрос и заявленные замены.

        Если замены не заданы явно, они выводятся из списка ненайденных имён и
        кандидатов: так тесты графа не дублируют данные инструмента.
        """

        self.fix_calls += 1
        replacements = self.replacements
        if replacements is None:
            replacements = _claimed_replacements(unknown)
        return SqlFix(
            sql=self.fixed_sql,
            replacements=[NameReplacement.model_validate(item) for item in replacements],
        )

    async def propose_index(
        self,
        sql: str,
        plan_nodes: list[str],
        stats: dict[str, Any],
    ) -> IndexProposal:
        """Вернуть предложение по индексу."""

        self.index_calls += 1
        return IndexProposal(ddl=self.ddl, reason=self.reason)


class FakeChecker:
    """Проверка имён поверх подготовленного каталога."""

    def __init__(self, catalog: SchemaCatalog) -> None:
        self._catalog = catalog
        self.calls = 0

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        """Проверить имена по каталогу."""

        from sql_query_agent.tools.schema_check import check_entities

        self.calls += 1
        result = check_entities(
            self._catalog,
            _to_entities(args),
            threshold=65.0,
            suggestion_limit=3,
        )
        return result.model_dump(mode="json")

    def as_tool(self) -> Any:
        """Инструмент не нужен: модель подставная."""

        return None


def _claimed_replacements(unknown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Замены, которые «сделала» подставная модель: по первому кандидату каждого имени."""

    claimed: list[dict[str, Any]] = []
    for item in unknown:
        candidates = item.get("candidates") or []
        if not candidates:
            continue
        claimed.append(
            {
                "old_name": str(item.get("name") or ""),
                "new_name": str(candidates[0].get("name") or ""),
                "kind": str(item.get("kind") or ""),
                "table": str(item.get("table") or ""),
            }
        )
    return claimed


def _to_entities(args: dict[str, Any]) -> Any:
    """Привести словарь аргумента к доменной модели."""

    from sql_query_agent.domain import SchemaEntities

    return SchemaEntities.model_validate(args)


class FakeMeasure:
    """Замер-заглушка с заранее заданным результатом."""

    def __init__(self, *, ok: bool = True, median_ms: float = 100.0) -> None:
        self.ok = ok
        self.median_ms = median_ms
        self.calls = 0

    async def run(self, sql: str) -> dict[str, Any]:
        """Вернуть заранее заданный результат замера."""

        self.calls += 1
        if not self.ok:
            return {"ok": False, "error": "синтаксическая ошибка", "error_kind": "database"}
        return {
            "ok": True,
            "median_ms": self.median_ms,
            "minimum_ms": self.median_ms - 1,
            "maximum_ms": self.median_ms + 1,
            "runs": 3,
            "samples_ms": [self.median_ms] * 3,
            "plan_nodes": ["Seq Scan sku"],
        }


class FakeApply:
    """Применение индекса-заглушка."""

    def __init__(
        self,
        *,
        applied: bool = True,
        before_ms: float = 100.0,
        after_ms: float = 10.0,
        error: str | None = None,
    ) -> None:
        self.applied = applied
        self.before_ms = before_ms
        self.after_ms = after_ms
        self.error = error
        self.calls = 0

    async def run(self, sql: str, index_ddl: str) -> dict[str, Any]:
        """Вернуть заранее заданное сравнение."""

        from sql_query_agent.domain import TimingStats
        from sql_query_agent.domain_comparison import compare

        self.calls += 1
        before = TimingStats(
            samples=[self.before_ms] * 3,
            minimum_ms=self.before_ms - 1,
            median_ms=self.before_ms,
            maximum_ms=self.before_ms + 1,
        )
        after = (
            TimingStats(
                samples=[self.after_ms] * 3,
                minimum_ms=self.after_ms - 1,
                median_ms=self.after_ms,
                maximum_ms=self.after_ms + 1,
            )
            if self.applied
            else None
        )
        comparison = compare(before, after, applied=self.applied, error=self.error)
        return {
            "applied": self.applied,
            "error": self.error,
            "index_ddl": index_ddl,
            "before_median_ms": comparison.before_median_ms,
            "after_median_ms": comparison.after_median_ms,
            "speedup": comparison.speedup,
            "verdict": comparison.verdict.value,
            "improved": comparison.improved,
            "reason": comparison.reason,
        }
