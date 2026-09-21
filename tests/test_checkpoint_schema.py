"""Интеграционные тесты схемы checkpoint'ов.

Проверяют, что setup идемпотентен и что таблицы появляются именно в схеме
`agent`, а не в `public`.
"""

import pytest

from sql_query_agent.config import Settings
from sql_query_agent.db.checkpoint import (
    checkpoint_tables,
    open_checkpointer,
    with_search_path,
)

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def test_search_path_is_added_to_dsn() -> None:
    """Схема попадает в строку подключения как параметр options."""

    dsn = with_search_path("postgresql://user@localhost:5432/products", "agent")

    assert "options=" in dsn
    assert "search_path" in dsn
    assert "agent" in dsn


def test_search_path_preserves_existing_query() -> None:
    """Существующие параметры строки подключения не теряются."""

    dsn = with_search_path(
        "postgresql://user@localhost:5432/products?sslmode=disable", "agent"
    )

    assert "sslmode=disable" in dsn
    assert "search_path" in dsn


async def test_setup_creates_tables_in_agent_schema(require_postgres: None) -> None:
    """Таблицы checkpoint'ов создаются в схеме agent."""

    settings = Settings(_env_file=None)
    dsn = settings.database.dsn
    schema = settings.database.agent_schema

    async with open_checkpointer(dsn, schema):
        tables = await checkpoint_tables(dsn, schema)

    assert tables >= EXPECTED_TABLES


async def test_setup_is_idempotent(require_postgres: None) -> None:
    """Повторный setup не падает и не создаёт дублей."""

    settings = Settings(_env_file=None)
    dsn = settings.database.dsn
    schema = settings.database.agent_schema

    async with open_checkpointer(dsn, schema):
        first = await checkpoint_tables(dsn, schema)
    async with open_checkpointer(dsn, schema):
        second = await checkpoint_tables(dsn, schema)

    assert first == second
