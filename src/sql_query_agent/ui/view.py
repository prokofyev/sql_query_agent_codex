"""Модель экрана веб-интерфейса.

Логика отображения отделена от NiceGUI: здесь только чистые функции,
превращающие отчёт о прогоне в текст, который видит пользователь. Благодаря
этому ветви «ускорение», «без ускорения», «отказано» и «проверка пропущена»
проверяются тестами без запуска браузера.
"""

from dataclasses import dataclass, field
from typing import Any

from sql_query_agent.domain_comparison import ComparisonVerdict
from sql_query_agent.run.report import RunReport, RunStatus, Stats

TITLE = "Советник по индексам"
MOUNT_PATH = "/ui"
INPUT_LABEL = "SQL-запрос"
INPUT_PLACEHOLDER = "select * from sku where product_id = 42"
SUBMIT_LABEL = "Проверить запрос"
ACCEPT_LABEL = "Принять"
DECLINE_LABEL = "Отказаться"
PRESETS_CAPTION = "Готовые запросы"
PROGRESS_CAPTION = "Выполняется: создание индекса может занять заметное время"
FIX_CAPTION = "Предложено исправление запроса"
INDEX_CAPTION = "Предложен индекс"
RESULT_CAPTION = "Результат замера"
REPLACEMENTS_CAPTION = "Замены имён"
SCHEMA_OK_CAPTION = "Проверка имён пройдена: все имена найдены в схеме"
SCHEMA_SKIPPED_CAPTION = "Проверка имён не выполнялась"
UNFIXABLE_CAPTION = "Имена не найдены, подходящих замен нет"

EMPTY_INPUT_MESSAGE = "Запрос не задан"
NO_DECISION = ""

STATUS_TEXT = {
    RunStatus.AWAITING_DECISION: "Нужно решение пользователя",
    RunStatus.FIX_DECLINED: "Отказ от исправления: обработка завершена",
    RunStatus.UNKNOWN_UNFIXABLE: "Имена не найдены, замен нет: обработка завершена",
    RunStatus.INDEX_DECLINED: "Отказ от индекса: база не изменялась",
    RunStatus.COMPLETED: "Прогон завершён",
    RunStatus.FAILED: "Прогон завершился ошибкой",
}


@dataclass(slots=True)
class RunView:
    """Готовая модель экрана: что именно видит пользователь."""

    status: str = ""
    status_text: str = ""
    message: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sql: str = ""
    fixed_sql: str | None = None
    replacements: list[str] = field(default_factory=list)
    index_ddl: str | None = None
    index_reason: str = ""
    before_ms: float | None = None
    after_ms: float | None = None
    speedup: float | None = None
    result_text: str = ""
    verdict: ComparisonVerdict | None = None
    no_speedup: bool = False
    schema_checked: bool = False
    schema_text: str = ""
    unfixable_message: str = ""
    awaiting_decision: bool = False
    step: str | None = None
    thread_id: str = ""
    busy_hint: str = ""

    @property
    def is_terminal(self) -> bool:
        """Завершён ли прогон."""

        return bool(self.status) and not self.awaiting_decision

    @property
    def needs_fix_decision(self) -> bool:
        """Ждёт ли прогон решения по исправлению."""

        return self.awaiting_decision and self.step == "schema_fix"

    @property
    def needs_index_decision(self) -> bool:
        """Ждёт ли прогон решения по индексу."""

        return self.awaiting_decision and self.step == "index_proposal"

    @property
    def has_errors(self) -> bool:
        """Есть ли сообщения об ошибке ввода."""

        return bool(self.errors)


def _speedup_text(speedup: float | None) -> str:
    """Отношение ускорения в виде текста."""

    if speedup is None:
        return ""
    return f"в {speedup:.1f} раза"


def _spread(stats: Stats | None) -> str:
    """Разброс повторов: минимум и максимум.

    Медиана без разброса вводит в заблуждение: один выброс способен сместить
    отдельный замер в разы, поэтому пользователю показывается и диапазон.
    """

    if stats is None or not stats.runs:
        return ""
    return f" (мин {stats.minimum_ms:.2f}, макс {stats.maximum_ms:.2f}, повторов {stats.runs})"


def _result_text(report: RunReport) -> tuple[str, bool]:
    """Текст результата и признак отсутствия значимого ускорения."""

    comparison = report.comparison
    if comparison is None:
        return "", False

    before = f"{comparison.before_median_ms:.2f} мс{_spread(comparison.before)}"
    after = f"{comparison.after_median_ms:.2f} мс{_spread(comparison.after)}"
    verdict = comparison.verdict
    if verdict is ComparisonVerdict.SPEEDUP:
        return (
            f"Запрос ускорился {_speedup_text(comparison.speedup)}: {before} → {after}",
            False,
        )
    if verdict is ComparisonVerdict.SLIGHT_SPEEDUP:
        return (
            f"Ускорение в пределах порога значимости: {before} → {after}",
            True,
        )
    if verdict is ComparisonVerdict.NOT_APPLIED:
        return comparison.error or "Индекс не был применён", False
    return (
        f"Значимого ускорения нет: {before} → {after}. {comparison.reason}",
        True,
    )


def _replacement_lines(report: RunReport) -> list[str]:
    """Строки списка замен для пользователя."""

    if not report.fix:
        return []
    lines: list[str] = []
    for item in report.fix.replacements:
        where = f" (таблица {item.table})" if item.table and item.kind == "column" else ""
        lines.append(f"{item.old_name}{where} → {item.new_name}")
    return lines


def build_run_view(report: RunReport | None, *, sql: str = "") -> RunView:
    """Построить модель экрана по отчёту о прогоне."""

    if report is None:
        return RunView(sql=sql)

    result_text, no_speedup = _result_text(report)
    comparison = report.comparison
    if not report.schema_checked:
        schema_text = SCHEMA_SKIPPED_CAPTION
    elif report.unfixable_message:
        schema_text = UNFIXABLE_CAPTION
    else:
        schema_text = SCHEMA_OK_CAPTION
    return RunView(
        status=report.status.value,
        status_text=STATUS_TEXT.get(report.status, report.status.value),
        errors=[report.error] if report.error else [],
        warnings=list(report.warnings),
        sql=report.current_sql or sql,
        fixed_sql=report.fix.fixed_sql if report.fix else None,
        replacements=_replacement_lines(report),
        index_ddl=report.index.ddl if report.index else None,
        index_reason=report.index.reason if report.index else "",
        before_ms=(
            comparison.before_median_ms
            if comparison
            else (report.index.before.median_ms if report.index and report.index.before else None)
        ),
        after_ms=comparison.after_median_ms if comparison else None,
        speedup=comparison.speedup if comparison else None,
        result_text=result_text,
        verdict=comparison.verdict if comparison else None,
        no_speedup=no_speedup,
        schema_checked=report.schema_checked,
        schema_text=schema_text,
        unfixable_message=report.unfixable_message,
        awaiting_decision=report.awaiting_decision,
        step=report.step,
        thread_id=report.thread_id,
        busy_hint=PROGRESS_CAPTION if report.step == "index_proposal" else "",
    )


def error_view(message: str, *, sql: str = "") -> RunView:
    """Модель экрана для ошибки ввода или обращения к API."""

    return RunView(errors=[message], sql=sql)


def pending_view(*, sql: str = "") -> RunView:
    """Модель экрана на время обработки."""

    return RunView(status_text="Обработка запроса", sql=sql, busy_hint=PROGRESS_CAPTION)


def form_enabled(sql: str, *, busy: bool) -> tuple[bool, bool]:
    """Доступность поля ввода и кнопки отправки.

    Порядок значений — поле, кнопка. Пока идёт обработка, поле тоже
    заблокировано: иначе ответ пришёл бы на прежний текст, а в поле уже
    стоял бы другой запрос.
    """

    if busy:
        return False, False
    return True, bool(sql.strip())


def decision_input_text(
    view: RunView,
    current: str,
    *,
    accepted: bool,
) -> str:
    """Текст, который должен оказаться в поле ввода после решения.

    Принятое исправление переносится в поле: дальше система обрабатывает
    именно этот запрос, и пользователь должен видеть его целиком. Отказ и
    решения по индексу поле не меняют — пользователю может понадобиться
    поправить прежний текст вручную.
    """

    if accepted and view.needs_fix_decision and view.fixed_sql:
        return view.fixed_sql
    return current


def build_preset_labels(presets: list[dict[str, Any]]) -> list[str]:
    """Подписи предустановленных запросов для выпадающего списка."""

    return [str(preset.get("title") or preset.get("id") or "") for preset in presets]


def preset_sql(presets: list[dict[str, Any]], title: str) -> str:
    """Текст предустановленного запроса по его подписи."""

    for preset in presets:
        if str(preset.get("title") or preset.get("id") or "") == title:
            return str(preset.get("sql") or "")
    return ""


__all__ = [
    "ACCEPT_LABEL",
    "DECLINE_LABEL",
    "EMPTY_INPUT_MESSAGE",
    "FIX_CAPTION",
    "INDEX_CAPTION",
    "INPUT_LABEL",
    "INPUT_PLACEHOLDER",
    "MOUNT_PATH",
    "NO_DECISION",
    "PRESETS_CAPTION",
    "PROGRESS_CAPTION",
    "REPLACEMENTS_CAPTION",
    "RESULT_CAPTION",
    "SCHEMA_OK_CAPTION",
    "SCHEMA_SKIPPED_CAPTION",
    "UNFIXABLE_CAPTION",
    "SUBMIT_LABEL",
    "TITLE",
    "RunView",
    "build_preset_labels",
    "build_run_view",
    "decision_input_text",
    "error_view",
    "form_enabled",
    "pending_view",
    "preset_sql",
]
