"""Тесты метрик: счётчики растут, метки не содержат текста запроса."""

import contextlib

from prometheus_client import CollectorRegistry, generate_latest

from sql_query_agent.observability.metrics import RunMetrics


class _Registry:
    """Реестр метрик, созданный для одного теста."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.metrics = RunMetrics(self.registry)

    def text(self) -> str:
        """Отрендерить метрики в текст Prometheus."""

        return generate_latest(self.registry).decode("utf-8")


def test_runs_counter_increases() -> None:
    """Счётчик прогонов растёт по статусу."""

    registry = _Registry()

    registry.metrics.runs.labels(status="compared").inc()
    registry.metrics.runs.labels(status="compared").inc()

    assert 'sqa_runs_total{status="compared"} 2.0' in registry.text()


def test_index_and_no_speedup_counters() -> None:
    """Счётчики индексов и случаев без ускорения считаются отдельно."""

    registry = _Registry()

    registry.metrics.indexes_applied.inc()
    registry.metrics.no_speedup.inc()

    rendered = registry.text()
    assert "sqa_indexes_applied_total 1.0" in rendered
    assert "sqa_no_speedup_total 1.0" in rendered


def test_measure_histogram_accepts_durations() -> None:
    """Гистограмма длительности принимает замеры по фазам."""

    registry = _Registry()

    registry.metrics.measure_duration.labels(phase="before").observe(12.5)
    registry.metrics.measure_duration.labels(phase="after").observe(0.5)

    assert 'sqa_measure_seconds_count{phase="before"} 1.0' in registry.text()


def test_unfixable_run_counts_as_run_without_measurements() -> None:
    """Прогон без замен считается в счётчике прогонов и не трогает гистограмму."""

    from sql_query_agent.observability.run_metrics import observe_run
    from sql_query_agent.run.report import build_report

    registry = _Registry()
    report = build_report(
        "t-unfixable",
        {
            "original_sql": "select * from prodcts",
            "current_sql": "select * from prodcts",
            "schema_checked": True,
            "schema_result": {"unknown": []},
            "status": "unknown_unfixable",
        },
        [],
    )

    observe_run(report, registry.metrics)

    rendered = registry.text()
    assert 'sqa_runs_total{status="unknown_unfixable"} 1.0' in rendered
    assert "sqa_measure_seconds_count" not in rendered
    assert "sqa_indexes_applied_total 0.0" in rendered


def test_labels_have_no_sql_text() -> None:
    """Метки ограничены низкой кардинальностью и не содержат текста запроса."""

    registry = _Registry()

    registry.metrics.runs.labels(status="compared").inc()
    registry.metrics.gigachat_calls.labels(operation="extract").inc()

    rendered = registry.text().lower()
    assert "select" not in rendered
    assert "sku" not in rendered


def test_gigachat_error_counter() -> None:
    """Ошибки обращений к модели считаются по виду операции."""

    registry = _Registry()

    registry.metrics.gigachat_errors.labels(operation="fix").inc()

    assert 'sqa_gigachat_errors_total{operation="fix"} 1.0' in registry.text()


def test_default_registry_is_shared() -> None:
    """Метрики процесса берутся из общего реестра."""

    from sql_query_agent.observability.metrics import current_metrics, metrics_registry

    assert current_metrics() is current_metrics()
    assert metrics_registry() is not None


def test_run_metrics_expose_registry_for_export() -> None:
    """Реестр метрик доступен для отдачи на эндпоинте."""

    registry = CollectorRegistry()

    metrics = RunMetrics(registry)

    assert metrics.registry is registry


async def test_metered_advisor_counts_calls_by_operation() -> None:
    """Обёртка модели считает обращения и ошибки по видам операций."""

    from langchain_core.messages import AIMessage

    from sql_query_agent.agent.llm import IndexProposal, SqlFix
    from sql_query_agent.observability.model_metrics import MeteredAdvisor

    class _Model:
        async def extract_identifiers(self, sql: str, tools: list[object]) -> AIMessage:
            return AIMessage(content="")

        async def propose_fix(self, sql: str, unknown: list[dict[str, object]]) -> SqlFix:
            return SqlFix(sql="select 1")

        async def propose_index(
            self, sql: str, plan_nodes: list[str], stats: dict[str, object]
        ) -> IndexProposal:
            return IndexProposal(ddl="CREATE INDEX i ON sku (product_id)", reason="причина")

    class _FailingModel(_Model):
        async def propose_fix(self, sql: str, unknown: list[dict[str, object]]) -> SqlFix:
            raise RuntimeError("модель недоступна")

    registry = CollectorRegistry()
    metrics = RunMetrics(registry)
    advisor = MeteredAdvisor(_Model(), metrics)

    await advisor.extract_identifiers("select 1", [])
    await advisor.propose_fix("select 1", [])
    await advisor.propose_index("select 1", [], {})

    rendered = generate_latest(registry).decode()
    assert 'sqa_gigachat_calls_total{operation="extract"} 1.0' in rendered
    assert 'sqa_gigachat_calls_total{operation="fix"} 1.0' in rendered
    assert 'sqa_gigachat_calls_total{operation="index"} 1.0' in rendered

    failing = MeteredAdvisor(_FailingModel(), metrics)
    with contextlib.suppress(RuntimeError):
        await failing.propose_fix("select 1", [])

    rendered = generate_latest(registry).decode()
    assert 'sqa_gigachat_errors_total{operation="fix"} 1.0' in rendered


def test_metered_advisor_is_identity_without_metrics() -> None:
    """Без метрик модель не оборачивается."""

    from sql_query_agent.observability.model_metrics import metered_advisor

    class _Model:
        pass

    model = _Model()

    assert metered_advisor(model, None) is model  # type: ignore[arg-type]
