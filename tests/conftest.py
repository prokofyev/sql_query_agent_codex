"""Общие фикстуры тестов.

Интеграционные тесты работают против локальной базы `products`. Если база
недоступна, они пропускаются: без PostgreSQL прогон остаётся зелёным.

Тесты с реальной моделью GigaChat помечены `llm` и запускаются только с
флагом `--run-llm`, чтобы обычный прогон не тратил обращения к модели.
"""

from collections.abc import Iterator

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Добавить флаг запуска тестов с реальной моделью."""

    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Запустить тесты, требующие реальной модели GigaChat.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Исключить LLM-тесты, если флаг не передан."""

    if config.getoption("--run-llm"):
        return
    skip = pytest.mark.skip(reason="нужен флаг --run-llm")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    """Заглушка для совместимости с asyncio-режимом pytest-asyncio."""

    return "asyncio"


@pytest.fixture(scope="session")
def database_dsn() -> str:
    """Адрес целевой базы из настроек."""

    from sql_query_agent.config import Settings

    return Settings(_env_file=None).database.dsn


@pytest.fixture(scope="session")
def postgres_available(database_dsn: str) -> bool:
    """Доступна ли целевая база: иначе интеграционные тесты пропускаются."""

    import psycopg

    try:
        with psycopg.connect(database_dsn, connect_timeout=2) as connection:
            connection.execute("select 1")
    except Exception:
        return False
    return True


@pytest.fixture
def require_postgres(postgres_available: bool) -> None:
    """Пропустить тест, если база недоступна."""

    if not postgres_available:
        pytest.skip("локальный PostgreSQL с базой products недоступен")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Не давать настройкам протекать между тестами."""

    from sql_query_agent.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(scope="session")
def mounted_ui_app() -> Iterator[object]:
    """Приложение с один раз смонтированным интерфейсом.

    NiceGUI монтируется в глобальное приложение процесса и после первого
    монтирования запрещает добавлять middleware, поэтому смонтировать
    интерфейс дважды в одном прогоне нельзя. Приложение собирается здесь
    ровно один раз, а тесты интерфейса берут готовое.
    """

    from sql_query_agent.ui.app import mount_ui
    from sql_query_agent.ui.client import AdvisorApiClient
    from tests.api_fakes import FakeWorld, build_test_app

    app = build_test_app(FakeWorld())
    mount_ui(app, AdvisorApiClient(app))
    yield app
