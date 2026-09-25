"""Сборка тестового приложения с подставными моделью, замером и журналом."""

from typing import Any

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import CollectorRegistry

from sql_query_agent.agent.graph import build_graph
from sql_query_agent.api.app import create_app
from sql_query_agent.api.deps import AppDeps
from sql_query_agent.config import Settings
from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.observability.metrics import RunMetrics
from sql_query_agent.run.journal import RunJournal
from sql_query_agent.run.service import RunService
from sql_query_agent.run.session import SessionRunner
from tests.fakes import FakeApply, FakeChecker, FakeMeasure, FakeModel

CATALOG = SchemaCatalog.from_rows(
    [
        ("brand", "brand_id"),
        ("brand", "brand_name"),
        ("product", "product_id"),
        ("product", "brand_id"),
        ("sku", "sku_id"),
        ("sku", "product_id"),
        ("sku", "product_color_id"),
    ]
)


class FakeWorld:
    """Подставные модель, замер, индекс и журнал одного приложения."""

    def __init__(
        self,
        *,
        model: FakeModel | None = None,
        measure: FakeMeasure | None = None,
        apply: FakeApply | None = None,
    ) -> None:
        self.model = model or FakeModel(
            entities={"tables": ["sku"], "columns": ["product_id"]},
        )
        self.measure = measure or FakeMeasure()
        self.apply = apply or FakeApply()
        self.journal = RunJournal()
        self.registry = CollectorRegistry()
        self.metrics = RunMetrics(self.registry)


def build_test_app(world: FakeWorld) -> Any:
    """Собрать приложение с подставными зависимостями."""

    graph = build_graph(
        model=world.model,
        checker=FakeChecker(CATALOG),
        measure_tool=world.measure,
        apply_tool=world.apply,
        checkpointer=InMemorySaver(),
    )
    service = RunService(
        SessionRunner(graph),
        journal=world.journal,
        metrics=world.metrics,
    )
    deps = AppDeps(
        service=service,
        settings=Settings(_env_file=None),
        metrics=world.metrics,
        journal=world.journal,
    )
    return create_app(deps, with_lifespan=False)


def build_test_client(world: FakeWorld | None = None) -> tuple[httpx.AsyncClient, FakeWorld]:
    """HTTP-клиент поверх тестового приложения."""

    current = world or FakeWorld()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_test_app(current), raise_app_exceptions=False),
        base_url="http://test",
    )
    return client, current


def build_ui_client(world: FakeWorld | None = None) -> tuple[Any, FakeWorld]:
    """Клиент веб-интерфейса поверх тестового приложения."""

    from sql_query_agent.ui.client import AdvisorApiClient

    current = world or FakeWorld()
    return AdvisorApiClient(build_test_app(current)), current


async def decide(
    client: httpx.AsyncClient,
    run: dict[str, Any],
    *,
    accepted: bool,
    step: str | None = None,
) -> Any:
    """Отправить решение по этапу, на котором прогон остановлен.

    Этап берётся из отчёта: так же поступает веб-интерфейс, отвечая на то
    предложение, которое пользователь видел. Явный этап нужен там, где
    проверяется запоздалое решение: клиент отвечает по тому предложению,
    которое видел до этого.
    """

    return await client.post(
        f"/runs/{run['thread_id']}/decision",
        json={"accepted": accepted, "step": step or run["step"]},
    )


__all__ = [
    "CATALOG",
    "FakeWorld",
    "build_test_app",
    "build_test_client",
    "build_ui_client",
    "decide",
]
