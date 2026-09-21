"""Сравнение замеров до и после применения индекса.

Отсутствие ускорения — полноценный результат, а не ошибка: индекс может не
помочь, если колонка низкоселективна или узкое место находится в соединении
или агрегации.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from sql_query_agent.domain import TimingStats

NO_SPEEDUP_REASON = (
    "Индекс не дал ускорения. Вероятная причина: низкая селективность колонки "
    "или узкое место в соединении либо агрегации, а не в поиске строк"
)
NOISY_REASON = (
    "Разница между замерами недостоверна: разброс повторов перекрывается. "
    "Индекс заметного ускорения не дал"
)
SLIGHT_SPEEDUP_REASON = (
    "Ускорение есть, но оно в пределах порога значимости: "
    "разница может объясняться шумом измерений"
)


class ComparisonVerdict(StrEnum):
    """Итог сравнения."""

    NOT_APPLIED = "index_not_applied"
    NO_SPEEDUP = "no_speedup"
    SLIGHT_SPEEDUP = "slight_speedup"
    SPEEDUP = "speedup"


class Comparison(BaseModel):
    """Результат сравнения двух замеров."""

    verdict: ComparisonVerdict
    before_median_ms: float
    after_median_ms: float
    speedup: float = Field(default=0.0)
    before: TimingStats | None = None
    after: TimingStats | None = None
    reason: str = ""

    @property
    def improved(self) -> bool:
        """Считается ли результат ускорением."""

        return self.verdict in (ComparisonVerdict.SPEEDUP, ComparisonVerdict.SLIGHT_SPEEDUP)


def ranges_overlap(before: TimingStats, after: TimingStats) -> bool:
    """Перекрываются ли диапазоны замеров.

    Если разброс повторов накладывается, разница между медианами не
    доказывает изменения: на демо-данных разброс достигал двукратного, из-за
    чего наивное сравнение медиан показывало «замедление» там, где индекс
    просто не влиял на план.
    """

    return not (after.maximum_ms < before.minimum_ms or before.maximum_ms < after.minimum_ms)


def _classify(
    before: TimingStats,
    after: TimingStats,
    speedup: float,
    significance_ratio: float,
) -> tuple[ComparisonVerdict, str]:
    """Определить итог и пояснение по отношению скорости и разбросу."""

    if ranges_overlap(before, after):
        return ComparisonVerdict.NO_SPEEDUP, NOISY_REASON
    if speedup >= significance_ratio:
        return ComparisonVerdict.SPEEDUP, f"Запрос ускорился примерно в {speedup:.1f} раза"
    if speedup > 1.0:
        return ComparisonVerdict.SLIGHT_SPEEDUP, SLIGHT_SPEEDUP_REASON
    return ComparisonVerdict.NO_SPEEDUP, NO_SPEEDUP_REASON


def compare(
    before: TimingStats,
    after: TimingStats | None,
    *,
    significance_ratio: float = 1.10,
    applied: bool = True,
    error: str | None = None,
) -> Comparison:
    """Сравнить замеры и определить итог.

    Порог значимости отделяет настоящее ускорение от шума измерений:
    ускорение меньше порога показывается, но не объявляется существенным.
    """

    if not applied or after is None:
        return Comparison(
            verdict=ComparisonVerdict.NOT_APPLIED,
            before_median_ms=before.median_ms,
            after_median_ms=before.median_ms,
            before=before,
            reason=error or "Индекс не был применён",
        )

    speedup = before.median_ms / after.median_ms if after.median_ms > 0 else float("inf")
    verdict, reason = _classify(before, after, speedup, significance_ratio)
    return Comparison(
        verdict=verdict,
        before_median_ms=before.median_ms,
        after_median_ms=after.median_ms,
        speedup=speedup,
        before=before,
        after=after,
        reason=reason,
    )
