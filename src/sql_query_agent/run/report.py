"""Отчёт о прогоне: единая модель для API, журнала и веб-интерфейса.

Отчёт собирается из состояния графа и данных `interrupt()`: обработка
останавливается на решении пользователя, поэтому «ожидание решения» — такое
же штатное состояние прогона, как и завершение.
"""

from difflib import SequenceMatcher
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


def tokenize(sql: str) -> list[str]:
    """Разбить текст запроса на токены: идентификаторы и знаки по одному.

    Идентификатор начинается с буквы или подчёркивания, поэтому число вроде
    `42` и слово `sku2` — цельные токены, а точка в `product.brand_id` —
    отдельный токен. Сравнение идёт по токенам, а не по подстрокам: иначе
    добавление квалификатора выглядело бы как вставка символа внутри имени.
    """

    def is_identifier_char(char: str) -> bool:
        """Символ может быть частью имени: буква, цифра или подчёркивание."""

        return char == "_" or char.isalnum()

    tokens: list[str] = []
    current: list[str] = []
    for char in sql:
        if is_identifier_char(char):
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
        if not char.isspace():
            tokens.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _words(tokens: list[str]) -> list[str]:
    """Только имена: знаки препинания, кавычки и числа отбрасываются."""

    return [token for token in tokens if token[:1].isalpha() or token[:1] == "_"]


def confirm_replacements(
    claimed: list[dict[str, Any]],
    original_sql: str,
    fixed_sql: str,
) -> list[Replacement]:
    """Оставить только те заявленные замены, которые подтверждает текст.

    Модель называет замены сама, но верить ей на слово нельзя: в случае
    «менять нечего» она заявляет несуществующую правку. Поэтому замена
    попадает в список, только если различие токенов исходного и исправленного
    запросов её объясняет.
    """

    if not claimed or original_sql == fixed_sql:
        return []

    original_tokens = tokenize(original_sql)
    fixed_tokens = tokenize(fixed_sql)
    changes = [
        (tag, original_tokens[i1:i2], fixed_tokens[j1:j2])
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, original_tokens, fixed_tokens
        ).get_opcodes()
        if tag != "equal"
    ]
    if not changes:
        return []

    replacements: list[Replacement] = []
    for item in claimed:
        old_name = str(item.get("old_name") or "")
        new_name = str(item.get("new_name") or "")
        if not old_name or not new_name or old_name.strip() == new_name.strip():
            continue
        if not _diff_supports(changes, old_name, new_name):
            continue
        replacements.append(
            Replacement(
                kind=str(item.get("kind") or ""),
                table=str(item.get("table") or ""),
                old_name=old_name,
                new_name=new_name,
            )
        )
    return replacements


def _diff_supports(
    changes: list[tuple[str, list[str], list[str]]],
    old_name: str,
    new_name: str,
) -> bool:
    """Объясняет ли различие текстов эту замену.

    Различие считается подтверждением в двух случаях: имя заменено целиком
    (`product_colr_id` → `product_color_id`) или к неизменённому имени
    добавлен квалификатор (`brand_id` → `product.brand_id`). Частичный
    квалификатор и «замена», которой в тексте нет, не подтверждаются.
    """

    old_words = _words(tokenize(old_name))
    new_words = _words(tokenize(new_name))
    if not old_words or not new_words:
        return False

    for _tag, old_segment, new_segment in changes:
        changed_old = _words(old_segment)
        changed_new = _words(new_segment)
        if changed_old == old_words and changed_new == new_words:
            return True
        if (
            not changed_old
            and changed_new == new_words[:-1]
            and new_words[-1] == old_words[-1]
            and changed_new
        ):
            return True
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
            replacements=confirm_replacements(
                list(values.get("fix_replacements") or []),
                original_sql,
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
