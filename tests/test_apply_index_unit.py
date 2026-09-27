"""Тесты инструмента применения индекса без базы: форма результата и лог."""

from typing import Any

import pytest

from sql_query_agent.db.explain import ExplainOutcome
from sql_query_agent.db.index_apply import IndexApplyOutcome
from sql_query_agent.domain import TimingStats
from sql_query_agent.tools.apply_index import ApplyIndexTool


def _outcome(
    *,
    applied: bool,
    after_ms: float | None,
    error: str | None = None,
) -> IndexApplyOutcome:
    """Собрать результат применения индекса с заданными замерами."""

    before = ExplainOutcome(
        stats=TimingStats(samples=[10.0], minimum_ms=10.0, median_ms=10.0, maximum_ms=10.0),
        plan={},
    )
    after = None
    if after_ms is not None:
        after = ExplainOutcome(
            stats=TimingStats(
                samples=[after_ms],
                minimum_ms=after_ms,
                median_ms=after_ms,
                maximum_ms=after_ms,
            ),
            plan={},
        )
    return IndexApplyOutcome(
        before=before,
        after=after,
        index_ddl="CREATE INDEX ON sku (product_id)",
        applied=applied,
        error=error,
    )


class _StubApplier:
    """Подставное применение индекса: отдаёт заранее заданный результат."""

    def __init__(self, outcome: IndexApplyOutcome) -> None:
        self._outcome = outcome

    async def apply(self, _sql: str, _index_ddl: str) -> IndexApplyOutcome:
        """Вернуть заданный результат."""

        return self._outcome


async def test_rejected_index_is_logged(capsys: pytest.CaptureFixture[str]) -> None:
    """Отказ применения индекса виден в логе с причиной."""

    tool = ApplyIndexTool(
        _StubApplier(_outcome(applied=False, after_ms=None, error="permission denied"))  # type: ignore[arg-type]
    )

    result: dict[str, Any] = await tool.run("select 1", "CREATE INDEX ON sku (product_id)")

    output = capsys.readouterr().out
    assert result["applied"] is False
    assert "результат применения индекса" in output
    assert "permission denied" in output


async def test_applied_index_is_logged_with_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    """Применённый индекс виден в логе вместе с итогом сравнения."""

    tool = ApplyIndexTool(
        _StubApplier(_outcome(applied=True, after_ms=5.0))  # type: ignore[arg-type]
    )

    result: dict[str, Any] = await tool.run("select 1", "CREATE INDEX ON sku (product_id)")

    output = capsys.readouterr().out
    assert result["applied"] is True
    assert "результат применения индекса" in output
    assert str(result["verdict"]) in output
