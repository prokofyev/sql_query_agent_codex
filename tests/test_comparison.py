"""Тесты сравнения до и после: исходы сравнения и учёт разброса повторов."""

import statistics

from sql_query_agent.domain import TimingStats
from sql_query_agent.domain_comparison import ComparisonVerdict, compare, ranges_overlap


def _timing(samples: list[float]) -> TimingStats:
    """Статистика по списку замеров."""

    return TimingStats(
        samples=samples,
        minimum_ms=min(samples),
        median_ms=statistics.median(samples),
        maximum_ms=max(samples),
    )


def test_clear_speedup_is_reported() -> None:
    """Существенное ускорение распознаётся."""

    result = compare(_timing([100.0, 101.0, 99.0]), _timing([10.0, 10.1, 9.9]))

    assert result.verdict is ComparisonVerdict.SPEEDUP
    assert result.speedup > 9.0
    assert result.improved is True


def test_no_speedup_is_a_valid_outcome() -> None:
    """Отсутствие ускорения — полноценный результат с объяснением причины."""

    result = compare(_timing([15.0, 15.1, 14.9]), _timing([16.0, 16.1, 15.9]))

    assert result.verdict is ComparisonVerdict.NO_SPEEDUP
    assert result.improved is False
    assert "селективность" in result.reason


def test_overlapping_ranges_are_not_a_speedup() -> None:
    """Перекрывающийся разброс не даёт объявить изменение скорости.

    Это реальные величины с демо-базы для запроса по низкоселективной
    колонке: медиана «после» оказалась вдвое больше, но диапазоны полностью
    перекрываются, поэтому говорить о замедлении нельзя.
    """

    before = _timing([14.13, 13.65, 30.16, 12.21, 28.35])
    after = _timing([32.04, 14.17, 33.28, 17.30, 34.77])

    assert ranges_overlap(before, after) is True

    result = compare(before, after)

    assert result.verdict is ComparisonVerdict.NO_SPEEDUP
    assert result.improved is False
    assert "недостоверна" in result.reason


def test_slight_speedup_is_not_overstated() -> None:
    """Незначительное ускорение не объявляется существенным."""

    result = compare(_timing([100.0, 100.5, 99.5]), _timing([97.0, 97.5, 96.5]))

    assert result.verdict is ComparisonVerdict.SLIGHT_SPEEDUP
    assert "порог" in result.reason


def test_threshold_boundary_counts_as_speedup() -> None:
    """Достижение порога считается ускорением."""

    result = compare(
        _timing([110.0, 110.5, 109.5]),
        _timing([100.0, 100.5, 99.5]),
        significance_ratio=1.10,
    )

    assert result.verdict is ComparisonVerdict.SPEEDUP


def test_not_applied_index_is_reported() -> None:
    """Неприменённый индекс не выдаётся за ускорение."""

    result = compare(_timing([10.0, 10.1]), None, applied=False, error="syntax error")

    assert result.verdict is ComparisonVerdict.NOT_APPLIED
    assert result.improved is False
    assert result.reason == "syntax error"


def test_separated_ranges_do_not_overlap() -> None:
    """Раздельные диапазоны не считаются перекрывающимися."""

    assert ranges_overlap(_timing([100.0, 101.0]), _timing([10.0, 11.0])) is False
