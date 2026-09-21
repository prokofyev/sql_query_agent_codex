"""Запись метрик прогона по его отчёту.

Метки метрик ограничены итоговым состоянием и фазой замера: значений высокой
кардинальности (текст запроса, имена таблиц) здесь нет по построению.
"""

from sql_query_agent.domain_comparison import ComparisonVerdict
from sql_query_agent.observability.metrics import RunMetrics
from sql_query_agent.run.report import RunReport


def observe_run(report: RunReport, metrics: RunMetrics) -> None:
    """Отразить завершённый прогон в метриках процесса.

    Прогон, ожидающий решения, ещё не итог: считать его в счётчике прогонов
    рано, иначе один и тот же прогон попал бы в метрики дважды.
    """

    if report.awaiting_decision:
        return

    metrics.runs.labels(status=report.status.value).inc()

    if report.comparison is None:
        return

    _observe_measure(report, metrics)

    if report.comparison.applied:
        metrics.indexes_applied.inc()
    if report.comparison.verdict is not ComparisonVerdict.SPEEDUP:
        metrics.no_speedup.inc()


def _observe_measure(report: RunReport, metrics: RunMetrics) -> None:
    """Положить длительности замеров в гистограмму по фазам.

    Замер «до» берётся из сравнения, а если его там нет — из предложения об
    индексе: эти величины описывают один и тот же замер, но попадают в отчёт
    разными путями.
    """

    comparison = report.comparison
    if comparison is None:
        return
    before = comparison.before or (report.index.before if report.index else None)
    if before is not None:
        metrics.measure_duration.labels(phase="before").observe(before.median_ms / 1000.0)
    if comparison.after is not None:
        metrics.measure_duration.labels(phase="after").observe(
            comparison.after.median_ms / 1000.0
        )


__all__ = ["observe_run"]
