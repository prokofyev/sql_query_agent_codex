"""Отчёт о прогоне: единая модель для API, журнала и веб-интерфейса.

Отчёт собирается из состояния графа и данных `interrupt()`: обработка
останавливается на решении пользователя, поэтому «ожидание решения» — такое
же штатное состояние прогона, как и завершение.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sql_query_agent.domain_comparison import ComparisonVerdict

FIX_STEP = "schema_fix"
INDEX_STEP = "index_proposal"

FIX_NOT_REQUIRED = "not_required"
DECISION_ACCEPTED = "accepted"
DECISION_DECLINED = "declined"
INDEX_NOT_OFFERED = "not_offered"

IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


class RunStatus(StrEnum):
    """Итоговое состояние прогона."""

    AWAITING_DECISION = "awaiting_decision"
    FIX_DECLINED = "fix_declined"
    UNKNOWN_UNFIXABLE = "unknown_unfixable"
    INDEX_DECLINED = "index_declined"
    COMPLETED = "compared"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Завершён ли прогон: запись в журнал делается только для таких."""

        return self is not RunStatus.AWAITING_DECISION


class Replacement(BaseModel):
    """Замена ненайденного имени на существующее."""

    kind: str
    table: str
    old_name: str
    new_name: str


class FixProposal(BaseModel):
    """Предложение об исправлении запроса."""

    original_sql: str
    fixed_sql: str
    replacements: list[Replacement] = Field(default_factory=list)


class Stats(BaseModel):
    """Замер одной фазы: минимум, медиана, максимум и число повторов."""

    minimum_ms: float = 0.0
    median_ms: float = 0.0
    maximum_ms: float = 0.0
    runs: int = 0


class IndexProposal(BaseModel):
    """Предложение об индексе."""

    ddl: str
    reason: str = ""
    before: Stats | None = None


class ComparisonResult(BaseModel):
    """Результат сравнения замеров до и после применения индекса."""

    applied: bool = False
    error: str | None = None
    index_ddl: str = ""
    before_median_ms: float = 0.0
    after_median_ms: float = 0.0
    speedup: float | None = None
    verdict: ComparisonVerdict = ComparisonVerdict.NOT_APPLIED
    improved: bool = False
    no_speedup: bool = False
    reason: str = ""
    before: Stats | None = None
    after: Stats | None = None


class RunReport(BaseModel):
    """Всё, что нужно знать о прогоне вызывающей стороне и журналу."""

    thread_id: str
    status: RunStatus
    step: str | None = None
    original_sql: str
    current_sql: str
    schema_checked: bool = False
    unknown: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unfixable_message: str = ""
    fix: FixProposal | None = None
    index: IndexProposal | None = None
    comparison: ComparisonResult | None = None
    decisions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @property
    def awaiting_decision(self) -> bool:
        """Ждёт ли прогон решения пользователя."""

        return self.status is RunStatus.AWAITING_DECISION


def _stats(raw: dict[str, Any] | None) -> Stats | None:
    """Преобразовать замер из состояния в модель отчёта."""

    if not raw:
        return None
    return Stats(
        minimum_ms=float(raw.get("minimum_ms") or 0.0),
        median_ms=float(raw.get("median_ms") or 0.0),
        maximum_ms=float(raw.get("maximum_ms") or 0.0),
        runs=int(raw.get("runs") or 0),
    )


def find_replacements(
    unknown: list[dict[str, Any]],
    original_sql: str,
    fixed_sql: str,
) -> list[Replacement]:
    """Определить, какие имена модель действительно заменила.

    Замена считается выполненной, если предложенное инструментом имя есть в
    исправленном запросе и отсутствовало в исходном. Список строится по факту
    текста, а не по обещаниям модели: иначе пользователю показывались бы
    замены, которых в запросе нет.
    """

    replacements: list[Replacement] = []
    for item in unknown:
        name = str(item.get("name") or "")
        if not name or contains_identifier(fixed_sql, name):
            continue
        for candidate in item.get("candidates") or []:
            candidate_name = str(candidate.get("name") or "")
            if (
                candidate_name
                and contains_identifier(fixed_sql, candidate_name)
                and not contains_identifier(original_sql, candidate_name)
            ):
                replacements.append(
                    Replacement(
                        kind=str(item.get("kind") or ""),
                        table=str(item.get("table") or ""),
                        old_name=name,
                        new_name=candidate_name,
                    )
                )
                break
    return replacements


def contains_identifier(text: str, name: str) -> bool:
    """Встречается ли имя в тексте отдельным идентификатором, а не подстрокой.

    Соседние символы не должны быть символами идентификатора: иначе `sku`
    внутри `sku2` считалось бы присутствием `sku`, а замена `sku2` на `sku`
    не попала бы в список замен.
    """

    if not name:
        return False
    start = text.find(name)
    while start != -1:
        end = start + len(name)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before not in IDENTIFIER_CHARS and after not in IDENTIFIER_CHARS:
            return True
        start = text.find(name, start + 1)
    return False


def _decisions(values: dict[str, Any]) -> dict[str, str]:
    """Решения пользователя по двум точкам остановки."""

    if values.get("fix_declined"):
        fix = DECISION_DECLINED
    elif values.get("fix_applied"):
        fix = DECISION_ACCEPTED
    else:
        fix = FIX_NOT_REQUIRED

    if values.get("index_declined"):
        index = DECISION_DECLINED
    elif values.get("apply_result"):
        index = DECISION_ACCEPTED
    else:
        index = INDEX_NOT_OFFERED
    return {"fix": fix, "index": index}


def _status(values: dict[str, Any], step: str | None) -> RunStatus:
    """Итоговое состояние прогона по состоянию графа и точке остановки."""

    if step is not None:
        return RunStatus.AWAITING_DECISION
    raw = str(values.get("status") or "")
    if raw == "fix_declined":
        return RunStatus.FIX_DECLINED
    if raw == "unknown_unfixable":
        return RunStatus.UNKNOWN_UNFIXABLE
    if raw == "index_declined":
        return RunStatus.INDEX_DECLINED
    if raw == "compared":
        return RunStatus.COMPLETED
    if raw in ("measure_failed", "apply_failed"):
        return RunStatus.FAILED
    return RunStatus.FAILED if not values.get("before_stats") else RunStatus.COMPLETED


def _comparison(raw: dict[str, Any] | None) -> ComparisonResult | None:
    """Преобразовать результат применения индекса в модель отчёта."""

    if not raw:
        return None
    verdict = ComparisonVerdict(
        str(raw.get("verdict")) if raw.get("verdict") else "index_not_applied"
    )
    return ComparisonResult(
        applied=bool(raw.get("applied")),
        error=raw.get("error"),
        index_ddl=str(raw.get("index_ddl") or ""),
        before_median_ms=float(raw.get("before_median_ms") or 0.0),
        after_median_ms=float(raw.get("after_median_ms") or 0.0),
        speedup=raw.get("speedup"),
        verdict=verdict,
        improved=bool(raw.get("improved")),
        # «Без ускорения» — всё, что не дотянуло до значимого ускорения:
        # ускорение в пределах порога тоже не считается ускорением.
        no_speedup=verdict is not ComparisonVerdict.SPEEDUP,
        reason=str(raw.get("reason") or ""),
        before=_stats(raw.get("before")),
        after=_stats(raw.get("after")),
    )


def build_report(
    thread_id: str,
    values: dict[str, Any],
    interrupts: list[dict[str, Any]] | None = None,
) -> RunReport:
    """Собрать отчёт по состоянию графа и данным прерываний."""

    stopped = list(interrupts or [])
    step = str(stopped[0].get("step")) if stopped else None
    original_sql = str(values.get("original_sql") or values.get("current_sql") or "")
    current_sql = str(values.get("current_sql") or original_sql)
    status = _status(values, step)

    fix: FixProposal | None = None
    fixed_sql = values.get("fixed_sql")
    if fixed_sql:
        fix = FixProposal(
            original_sql=original_sql,
            fixed_sql=str(fixed_sql),
            replacements=find_replacements(
                list((values.get("schema_result") or {}).get("unknown") or []),
                original_sql if step == FIX_STEP else current_sql,
                str(fixed_sql),
            ),
        )

    index: IndexProposal | None = None
    proposal = values.get("proposal")
    if proposal:
        index = IndexProposal(
            ddl=str(proposal.get("ddl") or ""),
            reason=str(proposal.get("reason") or ""),
            before=_stats(values.get("before_stats")),
        )

    warnings = [str(item) for item in (values.get("warnings") or [])]
    error = next((warning for warning in warnings if status is RunStatus.FAILED), None)
    return RunReport(
        thread_id=thread_id,
        status=status,
        step=step,
        original_sql=original_sql,
        current_sql=current_sql,
        schema_checked=bool(values.get("schema_checked")),
        unknown=list((values.get("schema_result") or {}).get("unknown") or []),
        warnings=warnings,
        unfixable_message=str(values.get("unfixable_message") or ""),
        fix=fix,
        index=index,
        comparison=_comparison(values.get("apply_result")),
        decisions=_decisions(values),
        error=error,
    )
