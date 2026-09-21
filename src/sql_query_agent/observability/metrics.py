"""Метрики Prometheus для прогонов.

Метки ограничены значениями с низкой кардинальностью: исходы, этапы, виды
операций. Текст SQL-запроса и имена таблиц в метки не попадают — иначе число
рядов метрик росло бы с каждым запросом пользователя.
"""

from prometheus_client import CollectorRegistry, Counter, Histogram

MEASURE_BUCKETS = (0.5, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0)


class RunMetrics:
    """Метрики прогонов агента."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.runs = Counter(
            "sqa_runs_total",
            "Число прогонов по итоговому статусу",
            ["status"],
            registry=registry,
        )
        self.indexes_applied = Counter(
            "sqa_indexes_applied_total",
            "Число применённых индексов",
            registry=registry,
        )
        self.no_speedup = Counter(
            "sqa_no_speedup_total",
            "Число прогонов без значимого ускорения",
            registry=registry,
        )
        self.measure_duration = Histogram(
            "sqa_measure_seconds",
            "Длительность замеров запроса",
            ["phase"],
            buckets=MEASURE_BUCKETS,
            registry=registry,
        )
        self.gigachat_calls = Counter(
            "sqa_gigachat_calls_total",
            "Число обращений к GigaChat по виду операции",
            ["operation"],
            registry=registry,
        )
        self.gigachat_errors = Counter(
            "sqa_gigachat_errors_total",
            "Число ошибок обращений к GigaChat",
            ["operation"],
            registry=registry,
        )


_registry = CollectorRegistry()
_metrics = RunMetrics(_registry)


def current_metrics() -> RunMetrics:
    """Метрики процесса по умолчанию."""

    return _metrics


def metrics_registry() -> CollectorRegistry:
    """Реестр метрик процесса."""

    return _registry
