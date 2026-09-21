"""Тесты замера: медиана, устойчивость к выбросам и ограничения ввода."""

from typing import Any

import pytest

from sql_query_agent.db.explain import (
    QueryMeasurer,
    SqlNotAllowedError,
    SqlTimeoutError,
    prepare_statement,
)


class _FakeCursor:
    """Курсор, возвращающий заранее заданные величины замеров.

    Счётчик общий для соединения: каждый вызов `EXPLAIN` создаёт новый
    курсор, но последовательность величин должна продолжаться.
    """

    def __init__(self, durations: list[float], counter: list[int]) -> None:
        self._durations = durations
        self._counter = counter

    async def fetchone(self) -> Any:
        position = min(self._counter[0], len(self._durations) - 1)
        self._counter[0] += 1
        duration = self._durations[position]
        return ([{"Execution Time": duration, "Planning Time": 0.5}],)


class _FakeTransaction:
    """Транзакция-заглушка."""

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeConnection:
    """Соединение-заглушка: записывает запросы и подсовывает курсор."""

    def __init__(self, durations: list[float]) -> None:
        self._durations = durations
        self.executed: list[str] = []
        self._counter = [0]

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def execute(self, sql: str, params: object = None) -> _FakeCursor:
        self.executed.append(sql)
        return _FakeCursor(self._durations, self._counter)


class _FakeConnectionContext:
    """Контекстный менеджер соединения."""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakePool:
    """Пул-заглушка, отдающий одно и то же соединение."""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self._connection)


async def test_median_is_used_and_outlier_does_not_shift_it() -> None:
    """Единичный выброс не смещает медиану."""

    connection = _FakeConnection([10.0, 10.5, 90.0])
    measurer = QueryMeasurer(_FakePool(connection), warmup_runs=0, repeat_runs=3)

    outcome = await measurer.measure("select 1")

    assert outcome.stats.samples == [10.0, 10.5, 90.0]
    assert outcome.stats.median_ms == 10.5
    assert outcome.stats.minimum_ms == 10.0
    assert outcome.stats.maximum_ms == 90.0
    assert outcome.stats.runs == 3


async def test_warmup_runs_are_not_counted() -> None:
    """Прогревочные прогоны не попадают в статистику."""

    connection = _FakeConnection([100.0, 1.0, 2.0])
    measurer = QueryMeasurer(_FakePool(connection), warmup_runs=1, repeat_runs=2)

    outcome = await measurer.measure("select 1")

    assert outcome.stats.samples == [1.0, 2.0]
    assert outcome.stats.median_ms == 1.5


async def test_read_only_transaction_is_set() -> None:
    """Замер выполняется в транзакции только для чтения и с таймаутами."""

    connection = _FakeConnection([1.0])
    measurer = QueryMeasurer(_FakePool(connection), warmup_runs=0, repeat_runs=1)

    await measurer.measure("select 1")

    executed = " ".join(connection.executed)
    assert "SET TRANSACTION READ ONLY" in executed
    assert "statement_timeout" in executed
    assert "lock_timeout" in executed


async def test_timeout_is_reported_separately() -> None:
    """Превышение таймаута сообщается отдельной ошибкой."""

    class _TimeoutCursor:
        async def fetchone(self) -> Any:
            raise RuntimeError("canceling statement due to statement timeout")

    class _TimeoutConnection(_FakeConnection):
        async def execute(self, sql: str, params: object = None) -> Any:
            if sql.startswith("EXPLAIN"):
                return _TimeoutCursor()
            return await super().execute(sql, params)

    measurer = QueryMeasurer(_FakePool(_TimeoutConnection([])), warmup_runs=0, repeat_runs=1)

    with pytest.raises(SqlTimeoutError):
        await measurer.measure("select 1")


@pytest.mark.parametrize(
    "sql_text",
    ["", "   ", ";", ";;"],
)
def test_empty_input_is_rejected(sql_text: str) -> None:
    """Пустой ввод отклоняется."""

    with pytest.raises(SqlNotAllowedError):
        prepare_statement(sql_text)


def test_multiple_statements_are_rejected() -> None:
    """Две команды в одном вводе отклоняются."""

    with pytest.raises(SqlNotAllowedError):
        prepare_statement("select 1; select 2")


def test_write_statement_is_rejected() -> None:
    """Команда изменения данных отклоняется."""

    with pytest.raises(SqlNotAllowedError):
        prepare_statement("update brand set brand_name = 'x'")


def test_delete_statement_is_rejected() -> None:
    """Удаление данных отклоняется."""

    with pytest.raises(SqlNotAllowedError):
        prepare_statement("delete from brand")


def test_trailing_semicolon_is_allowed() -> None:
    """Один завершающий разделитель допускается."""

    assert prepare_statement("select 1;") == "select 1"


def test_with_statement_is_allowed() -> None:
    """Запрос с CTE допускается."""

    assert prepare_statement("with t as (select 1) select * from t").startswith("with")
