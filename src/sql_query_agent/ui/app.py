"""Веб-интерфейс NiceGUI, смонтированный в то же ASGI-приложение.

Страница ходит в API внутрипроцессно через `AdvisorApiClient`, поэтому
контракт ровно один, а интерфейс можно тестировать без запуска сервера.

Логика экрана живёт в чистых функциях (`ui.view`), а здесь остаётся только
разметка и порядок действий: показать статус, дождаться ответа, показать
предложение, разблокировать ввод.
"""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from sql_query_agent.logging_setup import get_logger
from sql_query_agent.ui.client import AdvisorApiClient
from sql_query_agent.ui.view import (
    ACCEPT_LABEL,
    DECLINE_LABEL,
    FIX_CAPTION,
    INDEX_CAPTION,
    INPUT_LABEL,
    INPUT_PLACEHOLDER,
    MOUNT_PATH,
    PRESETS_CAPTION,
    REPLACEMENTS_CAPTION,
    RESULT_CAPTION,
    SUBMIT_LABEL,
    TITLE,
    UNFIXABLE_CAPTION,
    RunView,
    build_preset_labels,
    decision_input_text,
    form_enabled,
    pending_view,
    preset_sql,
)

logger = get_logger(__name__)


async def run_submit(
    sql: str,
    client: AdvisorApiClient,
    *,
    set_busy: Callable[[bool], None],
    show: Callable[[RunView], None],
) -> RunView:
    """Отправить запрос и показать результат.

    Порядок показа задан здесь, а не в разметке: статус появляется до
    ожидания, а разблокировка выполняется в `finally`, чтобы сбой не оставил
    форму заблокированной.
    """

    set_busy(True)
    show(pending_view(sql=sql))
    try:
        view = await client.start(sql)
    finally:
        set_busy(False)
    show(view)
    return view


async def run_decision(
    thread_id: str,
    client: AdvisorApiClient,
    *,
    accepted: bool,
    set_busy: Callable[[bool], None],
    show: Callable[[RunView], None],
) -> RunView:
    """Передать решение пользователя и показать результат."""

    set_busy(True)
    try:
        view = await client.decide(thread_id, accepted=accepted)
    finally:
        set_busy(False)
    show(view)
    return view


def apply_preset(sql_input: Any, presets: list[dict[str, Any]], title: str) -> str:
    """Подставить текст предустановленного запроса в поле ввода."""

    sql = preset_sql(presets, title)
    if sql:
        sql_input.value = sql
    return sql


def prepare_decision(
    view: RunView,
    sql_input: Any,
    decision_row: Any,
    *,
    accepted: bool,
) -> str:
    """Подготовить экран к решению пользователя.

    Принятое исправление переносится в поле ввода, а ряд решений скрывается
    сразу, до обращения к серверу: иначе по уже решённому предложению можно
    было бы нажать второй раз, пока идёт запрос.
    """

    current = sql_input.value or ""
    text = decision_input_text(view, current, accepted=accepted)
    if text != current:
        sql_input.value = text
    decision_row.visible = False
    return text


def show_decisions(decision_row: Any, view: RunView) -> None:
    """Показать ряд решений, если модель экрана ждёт решения."""

    decision_row.visible = view.awaiting_decision


def register_pages(
    client: AdvisorApiClient,
    *,
    presets: list[dict[str, Any]] | None = None,
) -> None:
    """Зарегистрировать страницы интерфейса."""

    from nicegui import ui

    @ui.page("/")
    def index() -> None:
        """Страница ввода запроса и решений по предложениям агента."""

        ui.page_title(TITLE)
        state: dict[str, Any] = {
            "busy": False,
            "thread_id": "",
            "view": RunView(),
            "presets": list(presets or []),
        }

        with ui.card().classes("w-full"):
            sql_input = ui.textarea(
                label=INPUT_LABEL,
                placeholder=INPUT_PLACEHOLDER,
                value="",
            ).classes("w-full font-mono")
            preset_select = ui.select(
                options=build_preset_labels(state["presets"]),
                label=PRESETS_CAPTION,
            ).classes("w-full")
            preset_select.on_value_change(
                lambda event: apply_preset(sql_input, state["presets"], event.value or "")
            )
            submit = ui.button(SUBMIT_LABEL)
            progress = ui.linear_progress(show_value=False).props("indeterminate")
            progress.visible = False

        with ui.card().classes("w-full"):
            status_label = ui.label().classes("text-base")
            warnings_label = ui.label().classes("text-sm text-orange-700 whitespace-pre-line")
            message_label = ui.label().classes("text-sm text-red-700")
            unfixable_card = ui.column().classes("gap-1")
            with unfixable_card:
                ui.label(UNFIXABLE_CAPTION).classes("text-sm text-gray-500")
                unfixable_label = ui.label().classes("whitespace-pre-line")
            fix_card = ui.column().classes("gap-1")
            with fix_card:
                ui.label(FIX_CAPTION).classes("text-sm text-gray-500")
                fix_label = ui.label().classes("font-mono whitespace-pre-wrap")
                ui.label(REPLACEMENTS_CAPTION).classes("text-sm text-gray-500")
                replacements_label = ui.label().classes("whitespace-pre-line")
            decision_row = ui.row().classes("gap-2")
            with decision_row:
                accept = ui.button(ACCEPT_LABEL)
                decline = ui.button(DECLINE_LABEL).props("flat")
            index_card = ui.column().classes("gap-1")
            with index_card:
                ui.label(INDEX_CAPTION).classes("text-sm text-gray-500")
                index_label = ui.label().classes("font-mono whitespace-pre-wrap")
                reason_label = ui.label().classes("text-sm text-gray-700")
            result_card = ui.column().classes("gap-1")
            with result_card:
                ui.label(RESULT_CAPTION).classes("text-sm text-gray-500")
                result_label = ui.label().classes("text-base whitespace-pre-line")

        def render(view: RunView) -> None:
            """Показать модель экрана."""

            state["thread_id"] = view.thread_id or state["thread_id"]
            state["view"] = view
            status_label.text = view.status_text
            warnings_label.text = "\n".join(view.warnings)
            message_label.text = "\n".join(view.errors)
            unfixable_card.visible = bool(view.unfixable_message)
            unfixable_label.text = view.unfixable_message
            fix_card.visible = bool(view.fixed_sql)
            fix_label.text = view.fixed_sql or ""
            replacements_label.text = "\n".join(view.replacements)
            index_card.visible = bool(view.index_ddl)
            index_label.text = view.index_ddl or ""
            reason_label.text = view.index_reason
            result_card.visible = bool(view.result_text)
            result_label.text = view.result_text
            show_decisions(decision_row, view)
            accept.text = ACCEPT_LABEL

        def sync_form() -> None:
            """Привести доступность поля и кнопки к текущему состоянию."""

            sql_input.enabled, submit.enabled = form_enabled(
                sql_input.value or "", busy=state["busy"]
            )
            submit.props(f"loading={str(state['busy']).lower()}")
            progress.visible = state["busy"]

        def set_busy(value: bool) -> None:
            """Запомнить, что обработка идёт, и обновить форму."""

            state["busy"] = value
            sync_form()

        async def submit_run() -> None:
            """Отправить запрос на обработку."""

            await run_submit(
                sql_input.value or "",
                client,
                set_busy=set_busy,
                show=render,
            )

        async def decide(accepted: bool) -> None:
            """Передать решение по текущему предложению."""

            if not state["thread_id"]:
                return
            prepare_decision(
                state["view"],
                sql_input,
                decision_row,
                accepted=accepted,
            )
            await run_decision(
                state["thread_id"],
                client,
                accepted=accepted,
                set_busy=set_busy,
                show=render,
            )

        submit.on_click(submit_run)
        accept.on_click(lambda: decide(True))
        decline.on_click(lambda: decide(False))
        sql_input.on_value_change(sync_form)

        async def load_presets() -> None:
            """Загрузить библиотеку запросов при открытии страницы."""

            loaded = await client.presets()
            if loaded:
                state["presets"] = loaded
                preset_select.options = build_preset_labels(loaded)
                preset_select.update()

        ui.timer(0.1, load_presets, once=True)
        render(RunView())
        sync_form()

    return None


def mount_ui(
    app: FastAPI,
    client: AdvisorApiClient,
    *,
    presets: list[dict[str, Any]] | None = None,
) -> None:
    """Смонтировать NiceGUI в существующее приложение FastAPI."""

    from nicegui import ui

    register_pages(client, presets=presets)
    ui.run_with(app, mount_path=MOUNT_PATH, title=TITLE, storage_secret="sql-index-advisor")


__all__ = [
    "apply_preset",
    "mount_ui",
    "prepare_decision",
    "register_pages",
    "run_decision",
    "run_submit",
    "show_decisions",
]
