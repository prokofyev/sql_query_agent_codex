"""Применение индекса с последующим откатом.

Индекс создаётся внутри транзакции, там же выполняется повторный замер, после
чего транзакция откатывается. База остаётся в исходном состоянии: индекс не
сохраняется, демо-данные не меняются.

Откат выполняется явным `rollback()`: менеджер транзакции `psycopg`
фиксирует изменения при обычном выходе из блока, поэтому полагаться на него
нельзя — индекс остался бы в базе.
"""

from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.db.explain import (
    ExplainOutcome,
    QueryMeasurer,
    SqlTimeoutError,
    prepare_statement,
)
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

STATEMENT_TIMEOUT_SQL = "SELECT set_config('statement_timeout', %s, true)"
LOCK_TIMEOUT_SQL = "SELECT set_config('lock_timeout', %s, true)"


class IndexApplyError(RuntimeError):
    """Индекс не удалось применить."""


@dataclass(slots=True)
class IndexApplyOutcome:
    """Результат применения индекса."""

    before: ExplainOutcome
    after: ExplainOutcome | None
    index_ddl: str
    applied: bool
    error: str | None = None

    @property
    def speedup(self) -> float | None:
        """Отношение исходного замера к замеру с индексом."""

        if not self.applied or self.after is None or self.after.stats.median_ms <= 0:
            return None
        return self.before.stats.median_ms / self.after.stats.median_ms


class IndexApplier:
    """Создание индекса в транзакции с замером и откатом."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        measurer: QueryMeasurer,
        *,
        statement_timeout_ms: int = 5_000,
        lock_timeout_ms: int = 1_000,
    ) -> None:
        self._pool = pool
        self._measurer = measurer
        self._statement_timeout_ms = statement_timeout_ms
        self._lock_timeout_ms = lock_timeout_ms

    async def baseline(self, sql_text: str) -> ExplainOutcome:
        """Замер исходного запроса без индекса."""

        return await self._measurer.measure(sql_text)

    async def apply(self, sql_text: str, index_ddl: str) -> IndexApplyOutcome:
        """Создать индекс, замерить запрос и откатить транзакцию.

        Откат гарантирован: `rollback()` вызывается и при успехе, и при
        ошибке, поэтому индекс не остаётся в базе ни в одном случае.
        """

        sql = prepare_statement(sql_text)
        before = await self._measurer.measure(sql)
        after: ExplainOutcome | None = None
        error: str | None = None
        applied = False

        connection: AsyncConnection = await self._pool.getconn()
        try:
            await connection.execute(
                STATEMENT_TIMEOUT_SQL, (str(self._statement_timeout_ms),)
            )
            await connection.execute(LOCK_TIMEOUT_SQL, (str(self._lock_timeout_ms),))
            await connection.execute(index_ddl)
            after = await self._measurer.measure_on_connection(connection, sql)
            applied = True
        except SqlTimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - текст ошибки показывается пользователю
            error = str(exc)
            logger.warning("применение индекса не удалось", error=error)
        finally:
            try:
                await connection.rollback()
            finally:
                await self._pool.putconn(connection)

        if applied:
            logger.info(
                "индекс применён и откачен",
                before_ms=round(before.stats.median_ms, 3),
                after_ms=round(after.stats.median_ms, 3) if after else None,
            )
        return IndexApplyOutcome(
            before=before,
            after=after,
            index_ddl=index_ddl,
            applied=applied,
            error=error,
        )
