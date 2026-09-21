"""Тесты инструмента замера: структура результата и обработка ошибок."""

from typing import Any

from sql_query_agent.db.explain import QueryMeasurer
from sql_query_agent.tools.measure_query import QueryMeasureTool, summarize_plan


class _Cursor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def fetchone(self) -> Any:
        return ([self._payload],)


class _Transaction:
    async def __aenter__(self) -> "_Transaction":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Connection:
    def __init__(self, payload: dict[str, Any], *, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def execute(self, sql: str, params: object = None) -> _Cursor:
        if self._error is not None:
            raise self._error
        return _Cursor(self._payload)


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)


PLAN = {
    "Node Type": "Aggregate",
    "Execution Time": 12.5,
    "Planning Time": 0.4,
    "Plans": [{"Node Type": "Seq Scan", "Relation Name": "sku"}],
}


def _tool(payload: dict[str, Any], *, error: Exception | None = None) -> QueryMeasureTool:
    measurer = QueryMeasurer(_Pool(_Connection(payload, error=error)), warmup_runs=0, repeat_runs=2)
    return QueryMeasureTool(measurer)


async def test_successful_measure_returns_stats() -> None:
    """Успешный замер возвращает медиану, границы и число повторов."""

    result = await _tool(PLAN).run("select 1")

    assert result["ok"] is True
    assert result["median_ms"] == 12.5
    assert result["runs"] == 2
    assert result["plan_nodes"][0] == "Aggregate"


async def test_summarize_plan_flattens_nodes() -> None:
    """Описание плана содержит вложенные узлы с отношениями."""

    nodes = summarize_plan(PLAN)

    assert nodes == ["Aggregate", "Seq Scan sku"]


async def test_disallowed_sql_is_returned_as_data() -> None:
    """Недопустимый запрос возвращается как данные, а не исключение."""

    result = await _tool(PLAN).run("delete from brand")

    assert result["ok"] is False
    assert result["error_kind"] == "not_allowed"


async def test_timeout_is_returned_as_data() -> None:
    """Таймаут возвращается как данные с отдельным признаком."""

    result = await _tool(PLAN, error=RuntimeError("statement timeout")).run("select 1")

    assert result["ok"] is False
    assert result["error_kind"] == "timeout"


async def test_database_error_is_returned_as_data() -> None:
    """Ошибка базы возвращается как данные с отдельным признаком."""

    result = await _tool(PLAN, error=RuntimeError("relation does not exist")).run("select 1")

    assert result["ok"] is False
    assert result["error_kind"] == "database"
    assert "relation does not exist" in result["error"]
