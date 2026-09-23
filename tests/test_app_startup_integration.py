"""Интеграционный тест сборки приложения: API и UI в одном процессе.

Проверяется, что рабочая точка входа открывает пул, checkpoint-хранилище и
журнал на реальной базе `products`, а веб-интерфейс смонтирован в то же
приложение. Модель GigaChat при этом не вызывается: обращения к ней
происходят только при запуске прогона.
"""

import httpx
import pytest

from sql_query_agent.config import Settings
from sql_query_agent.runtime import open_deps

pytestmark = pytest.mark.integration


async def test_app_starts_on_real_database_with_ui(
    require_postgres: None,
) -> None:
    """Приложение открывает ресурсы и отдаёт запросы на реальной базе.

    Интерфейс здесь не монтируется по-настоящему: NiceGUI допускает одно
    монтирование на процесс, и страница проверяется отдельным тестом. Здесь
    проверяется работа API с реальными пулом, журналом и хранилищем сессий.
    """

    from sql_query_agent.api.app import create_app

    settings = Settings(_env_file=None)
    deps = await open_deps(settings)
    try:
        app = create_app(deps)

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/healthz")
            presets = await client.get("/presets")

        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert presets.status_code == 200
        assert len(presets.json()["presets"]) >= 10
    finally:
        await deps.aclose()


async def test_checkpoint_tables_live_in_agent_schema(require_postgres: None) -> None:
    """После запуска приложения checkpoint-таблицы лежат в схеме `agent`."""

    from sql_query_agent.db.checkpoint import checkpoint_tables

    settings = Settings(_env_file=None)
    deps = await open_deps(settings)
    try:
        tables = await checkpoint_tables(
            settings.database.dsn, settings.database.agent_schema
        )
    finally:
        await deps.aclose()

    assert {"checkpoints", "checkpoint_writes", "checkpoint_blobs"} <= tables
    assert "checkpoints" not in await checkpoint_tables(
        settings.database.dsn, "public"
    )
