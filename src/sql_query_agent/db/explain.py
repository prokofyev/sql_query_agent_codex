"""Замер времени выполнения запроса.

Замер идёт в транзакции, доступной только для чтения: СУБД сама отклоняет
попытки записи, поэтому отдельный разбор SQL для этого не нужен. Перед
серией замеров выполняются прогревочные прогоны, а итогом считается медиана:
одиночный замер слишком чувствителен к выбросам.
"""

import statistics
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool

from sql_query_agent.domain import TimingStats
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

ALLOWED_FIRST_KEYWORDS = frozenset({"select", "with"})


class SqlNotAllowedError(ValueError):
    """Запрос не может быть измерен: не одиночный запрос на чтение."""


class SqlTimeoutError(RuntimeError):
    """Выполнение запроса прервано по таймауту."""


def prepare_statement(sql_text: str) -> str:
    """Проверить, что ввод — одиночный запрос на чтение, и вернуть его без `;`."""

    sql = sql_text.strip()
    if not sql:
        raise SqlNotAllowedError("Запрос не задан")
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise SqlNotAllowedError("Запрос не задан")
    if ";" in sql:
        raise SqlNotAllowedError("Допускается только один SQL-запрос")
    first = sql.split(None, 1)[0].lower()
    if first not in ALLOWED_FIRST_KEYWORDS:
        raise SqlNotAllowedError("Принимаются только запросы на чтение (SELECT или WITH)")
    return sql


@dataclass(slots=True)
class ExplainOutcome:
    """Результат одного замера."""

    stats: TimingStats
    plan: dict[str, Any]
    planning_time_ms: float | None = None


class QueryMeasurer:
    """Измерение времени выполнения запросов."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        statement_timeout_ms: int = 5_000,
        lock_timeout_ms: int = 1_000,
        warmup_runs: int = 1,
        repeat_runs: int = 3,
    ) -> None:
        self._pool = pool
        self._statement_timeout_ms = statement_timeout_ms
        self._lock_timeout_ms = lock_timeout_ms
        self._warmup_runs = warmup_runs
        self._repeat_runs = repeat_runs

    async def explain_once(
        self, connection: Any, sql: str
    ) -> tuple[float, dict[str, Any]]:
        """Один замер: `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`."""

        cursor = await connection.execute(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"  # noqa: S608 - sql проверен заранее
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            raise RuntimeError("СУБД вернула пустой план выполнения")
        payload = row[0][0]
        return float(payload["Execution Time"]), payload

    async def measure_on_connection(self, connection: Any, sql: str) -> ExplainOutcome:
        """Прогрев и серия замеров на уже открытом соединении.

        Используется там, где замер нужно выполнить внутри чужой транзакции,
        например при проверке эффекта индекса.
        """

        samples: list[float] = []
        plan: dict[str, Any] = {}
        planning_time: float | None = None

        for _ in range(self._warmup_runs):
            await self.explain_once(connection, sql)
        for _ in range(self._repeat_runs):
            elapsed, payload = await self.explain_once(connection, sql)
            samples.append(elapsed)
            plan = payload
            if planning_time is None:
                planning_time = float(payload.get("Planning Time", 0.0))

        return self.build_outcome(samples, plan, planning_time)

    @staticmethod
    def build_outcome(
        samples: list[float],
        plan: dict[str, Any],
        planning_time: float | None = None,
    ) -> ExplainOutcome:
        """Собрать результат замера из готовых величин."""

        return ExplainOutcome(
            stats=TimingStats(
                samples=samples,
                minimum_ms=min(samples),
                median_ms=statistics.median(samples),
                maximum_ms=max(samples),
            ),
            plan=plan,
            planning_time_ms=planning_time,
        )

    async def measure(self, sql_text: str) -> ExplainOutcome:
        """Измерить запрос: прогрев, затем серия замеров и медиана."""

        sql = prepare_statement(sql_text)
        samples: list[float] = []
        plan: dict[str, Any] = {}
        planning_time: float | None = None

        try:
            async with self._pool.connection() as connection, connection.transaction():
                await connection.execute("SET TRANSACTION READ ONLY")
                await connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(self._statement_timeout_ms),),
                )
                await connection.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    (str(self._lock_timeout_ms),),
                )
                outcome = await self.measure_on_connection(connection, sql)
                samples = outcome.stats.samples
                plan = outcome.plan
                planning_time = outcome.planning_time_ms
        except Exception as error:
            if "timeout" in str(error).lower():
                raise SqlTimeoutError(
                    "Запрос превысил допустимое время выполнения"
                ) from error
            raise

        stats = TimingStats(
            samples=samples,
            minimum_ms=min(samples),
            median_ms=statistics.median(samples),
            maximum_ms=max(samples),
        )
        logger.info(
            "замер завершён",
            runs=stats.runs,
            median_ms=round(stats.median_ms, 3),
            minimum_ms=round(stats.minimum_ms, 3),
            maximum_ms=round(stats.maximum_ms, 3),
        )
        return ExplainOutcome(stats=stats, plan=plan, planning_time_ms=planning_time)
