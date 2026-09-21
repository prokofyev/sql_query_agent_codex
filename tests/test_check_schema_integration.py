"""Интеграционные тесты проверки имён на реальной базе products."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from sql_query_agent.config import Settings
from sql_query_agent.db.pool import create_pool, open_pool
from sql_query_agent.tools.check_schema import SchemaChecker

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(require_postgres: None) -> AsyncIterator[AsyncConnectionPool]:
    """Пул подключений к целевой базе."""

    settings = Settings(_env_file=None)
    connection_pool = create_pool(settings.database.dsn)
    await open_pool(connection_pool)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


@pytest.fixture
def checker(pool: AsyncConnectionPool) -> SchemaChecker:
    """Проверка имён на схеме public."""

    return SchemaChecker(pool, schema="public", threshold=65.0, suggestion_limit=3)


async def test_existing_names_pass(checker: SchemaChecker) -> None:
    """Реальные имена демо-базы проходят проверку."""

    result = await checker.run(
        [{"table": "sku", "columns": ["sku_id", "product_id", "product_color_id"]}]
    )

    assert result["ok"] is True
    assert result["unknown"] == []


async def test_column_typo_has_candidates(checker: SchemaChecker) -> None:
    """Опечатка в колонке находит реальную колонку базы."""

    result = await checker.run(
        [{"table": "sku", "columns": ["product_colr_id"]}]
    )

    assert result["ok"] is False
    unknown = result["unknown"][0]
    assert unknown["kind"] == "column"
    names = [candidate["name"] for candidate in unknown["candidates"]]
    assert "product_color_id" in names


async def test_table_typo_has_candidates_from_catalog(checker: SchemaChecker) -> None:
    """Опечатка в имени таблицы находит кандидатов без опоры на текст ошибок СУБД."""

    result = await checker.run([{"table": "skuu", "columns": ["sku_id"]}])

    assert result["ok"] is False
    assert result["unknown_tables"] == ["skuu"]
    unknown = result["unknown"][0]
    assert unknown["kind"] == "table"
    names = [candidate["name"] for candidate in unknown["candidates"]]
    assert "sku" in names


async def test_catalog_is_cached_between_calls(checker: SchemaChecker) -> None:
    """Каталог загружается один раз и переиспользуется."""

    first = await checker.catalog()
    second = await checker.catalog()

    assert first is second
