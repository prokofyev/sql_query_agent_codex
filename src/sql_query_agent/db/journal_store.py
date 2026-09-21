"""Журнал прогонов в схеме `metrics` целевой базы.

Таблица создаётся идемпотентно при первом обращении: отдельной миграции у
учебного проекта нет, а схема демо-данных (`public`) не трогается вовсе.

Ошибка записи не должна ломать обработку запроса пользователя: поглощение
сбоя живёт в службе прогонов — в одном месте, а не в каждом хранилище.
"""

import json
from typing import Any

from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.db.schemas import create_schema
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.run.journal import RunRecord

logger = get_logger(__name__)

RUNS_TABLE = "runs"

TABLE_PLACEHOLDER = "{{table}}"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {{table}} (
    id bigserial PRIMARY KEY,
    thread_id text NOT NULL,
    status text NOT NULL,
    step text,
    original_sql text NOT NULL,
    current_sql text NOT NULL,
    schema_checked boolean NOT NULL DEFAULT false,
    unknown jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    fix_ddl text,
    index_ddl text,
    decisions jsonb NOT NULL DEFAULT '{}'::jsonb,
    before_stats jsonb,
    after_stats jsonb,
    verdict text,
    speedup double precision,
    no_speedup boolean NOT NULL DEFAULT false,
    reason text NOT NULL DEFAULT '',
    error text,
    finished_at timestamptz NOT NULL DEFAULT now()
)
"""

_INSERT_SQL = """
INSERT INTO {{table}} (
    thread_id, status, step, original_sql, current_sql, schema_checked,
    unknown, warnings, fix_ddl, index_ddl, decisions, before_stats,
    after_stats, verdict, speedup, no_speedup, reason, error, finished_at
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb,
    %s::jsonb, %s, %s, %s, %s, %s, %s
)
"""

_SELECT_SQL = """
SELECT thread_id, status, step, original_sql, current_sql, schema_checked,
       unknown, warnings, fix_ddl, index_ddl, decisions, before_stats,
       after_stats, verdict, speedup, no_speedup, reason, error, finished_at
FROM {{table}}
ORDER BY id DESC
LIMIT %s
"""


def _table_name(schema: str) -> sql.Composed:
    """Квалифицированное имя таблицы журнала."""

    return sql.SQL("{}.{}").format(
        sql.Identifier(schema), sql.Identifier(RUNS_TABLE)
    )


def _statement(template: str, schema: str) -> sql.Composed:
    """Подставить имя таблицы журнала в шаблон запроса.

    Подстановка идёт по явной метке, а не через `sql.SQL.format`: шаблоны
    содержат `%s` — заполнители параметров, и `format` принял бы их за свои.
    """

    table = _table_name(schema).as_string()
    return sql.SQL(template.replace(TABLE_PLACEHOLDER, table))


class PostgresRunJournal:
    """Журнал прогонов в целевой базе."""

    def __init__(self, pool: AsyncConnectionPool, *, schema: str = "metrics") -> None:
        self._pool = pool
        self._schema = schema
        self._ready = False

    async def _ensure_table(self) -> None:
        """Создать схему и таблицу журнала при первом обращении."""

        if self._ready:
            return
        async with self._pool.connection() as connection:
            await connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self._schema)
                )
            )
            await connection.execute(_statement(_CREATE_TABLE_SQL, self._schema))
        self._ready = True

    async def record(self, entry: RunRecord) -> None:
        """Записать audit-строку прогона."""

        await self._ensure_table()
        async with self._pool.connection() as connection:
            await connection.execute(
                _statement(_INSERT_SQL, self._schema),
                (
                    entry.thread_id,
                    entry.status,
                    entry.step,
                    entry.original_sql,
                    entry.current_sql,
                    entry.schema_checked,
                    _json(entry.unknown),
                    _json(entry.warnings),
                    entry.fix_ddl,
                    entry.index_ddl,
                    _json(entry.decisions),
                    _json(entry.before_stats) if entry.before_stats else None,
                    _json(entry.after_stats) if entry.after_stats else None,
                    entry.verdict,
                    entry.speedup,
                    entry.no_speedup,
                    entry.reason,
                    entry.error,
                    entry.finished_at,
                ),
            )

    async def history(self, limit: int = 20) -> list[RunRecord]:
        """Последние записи журнала: свежие первыми."""

        await self._ensure_table()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                _statement(_SELECT_SQL, self._schema),
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]


def _json(value: Any) -> str:
    """Сериализовать значение для jsonb-колонки."""

    return json.dumps(value, ensure_ascii=False, default=str)


def _row_to_record(row: Any) -> RunRecord:
    """Собрать запись журнала из строки выборки."""

    return RunRecord(
        thread_id=row[0],
        status=row[1],
        step=row[2],
        original_sql=row[3],
        current_sql=row[4],
        schema_checked=row[5],
        unknown=row[6] or [],
        warnings=row[7] or [],
        fix_ddl=row[8],
        index_ddl=row[9],
        decisions=row[10] or {},
        before_stats=row[11],
        after_stats=row[12],
        verdict=row[13],
        speedup=row[14],
        no_speedup=row[15],
        reason=row[16] or "",
        error=row[17],
        finished_at=row[18],
    )


async def prepare_journal(
    dsn: str,
    *,
    schema: str = "metrics",
) -> None:
    """Заранее создать схему журнала, не дожидаясь первого прогона."""

    await create_schema(dsn, schema)


__all__ = ["RUNS_TABLE", "PostgresRunJournal", "prepare_journal"]
