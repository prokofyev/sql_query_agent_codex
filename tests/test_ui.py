"""Тесты веб-интерфейса: поле ввода, решения, результаты и предупреждения.

Проверяются чистые функции модели экрана и сценарии отправки: интерфейс
работает против того же API через `ASGITransport`, поэтому контракт один.
"""

import contextlib
from typing import Any

from sql_query_agent.domain_comparison import ComparisonVerdict
from sql_query_agent.run.report import RunStatus, build_report
from sql_query_agent.ui.app import (
    apply_preset,
    mount_ui,
    prepare_decision,
    run_decision,
    run_submit,
    show_decisions,
)
from sql_query_agent.ui.view import (
    MOUNT_PATH,
    PRESETS_CAPTION,
    PROGRESS_CAPTION,
    SCHEMA_OK_CAPTION,
    SCHEMA_SKIPPED_CAPTION,
    UNFIXABLE_CAPTION,
    RunView,
    build_preset_labels,
    build_run_view,
    decision_input_text,
    error_view,
    form_enabled,
    pending_view,
    preset_sql,
    schema_panel_text,
)
from tests.api_fakes import FakeWorld, build_test_app, build_ui_client
from tests.fakes import FakeApply, FakeMeasure, FakeModel

GOOD_SQL = "select * from sku where product_id = 1"
TYPO_SQL = "select * from sku where product_colr_id = 1"
FIXED_SQL = "select * from sku where product_color_id = 1"
MISSING_TABLE = "zzz_table"
UNFIXABLE_SQL = "select * from zzz_table"

PRESETS = [
    {"id": "point-lookup", "title": "Точечная выборка", "sql": GOOD_SQL},
    {"id": "join-by-brand", "title": "Соединение", "sql": "select 1"},
]


class _RecordingView:
    """Сборщик показанных моделей экрана и состояний формы."""

    def __init__(self) -> None:
        self.shown: list[RunView] = []
        self.busy_states: list[bool] = []

    def set_busy(self, value: bool) -> None:
        """Запомнить состояние формы."""

        self.busy_states.append(value)

    def show(self, view: RunView) -> None:
        """Запомнить показанный экран."""

        self.shown.append(view)

    @property
    def last(self) -> RunView:
        """Последний показанный экран."""

        return self.shown[-1]


def _client(world: FakeWorld) -> Any:
    """Клиент интерфейса поверх тестового приложения."""

    return build_ui_client(world)[0]


def test_form_enabled_blocks_field_while_busy() -> None:
    """Во время обработки недоступны и поле, и кнопка."""

    assert form_enabled(GOOD_SQL, busy=True) == (False, False)
    assert form_enabled("", busy=True) == (False, False)


def test_form_enabled_keeps_field_editable_when_idle() -> None:
    """В покое поле доступно даже пустым, иначе в него не ввести текст."""

    assert form_enabled("", busy=False) == (True, False)
    assert form_enabled(GOOD_SQL, busy=False) == (True, True)


def test_preset_labels_and_sql_lookup() -> None:
    """Выбор предустановленного запроса подставляет его текст."""

    labels = build_preset_labels(PRESETS)

    assert labels == ["Точечная выборка", "Соединение"]
    assert preset_sql(PRESETS, "Точечная выборка") == GOOD_SQL
    assert preset_sql(PRESETS, "нет такого") == ""


def test_empty_library_keeps_input_usable() -> None:
    """Пустая библиотека не мешает ручному вводу запроса."""

    assert build_preset_labels([]) == []
    assert preset_sql([], "Точечная выборка") == ""
    assert form_enabled(GOOD_SQL, busy=False) == (True, True)


def test_schema_panel_combines_caption_and_diagram() -> None:
    """Панель схемы содержит подпись и рисунок."""

    text = schema_panel_text(
        {"caption": "Схема products", "diagram": "+--+\n| x|\n+--+\n"}
    )

    assert text.startswith("Схема products")
    assert "+--+" in text
    assert text.endswith("+--+")


def test_schema_panel_without_caption_shows_diagram() -> None:
    """Без подписи панель показывает только рисунок."""

    assert schema_panel_text({"diagram": "+--+"}) == "+--+"


def test_schema_panel_is_empty_without_schema() -> None:
    """Пустая схема даёт пустой текст, и панель не показывается."""

    assert schema_panel_text(None) == ""
    assert schema_panel_text({}) == ""
    assert schema_panel_text({"caption": "Только подпись"}) == ""
    assert schema_panel_text({"diagram": ""}) == ""


def test_apply_preset_fills_input() -> None:
    """Выбор запроса из библиотеки подставляет текст в поле ввода."""

    class _Input:
        value = ""

    sql_input = _Input()

    result = apply_preset(sql_input, PRESETS, "Точечная выборка")

    assert result == GOOD_SQL
    assert sql_input.value == GOOD_SQL


def test_apply_preset_keeps_input_for_unknown_title() -> None:
    """Неизвестная подпись не затирает введённый текст."""

    class _Input:
        value = "select 1"

    sql_input = _Input()
    apply_preset(sql_input, PRESETS, "нет такого")

    assert sql_input.value == "select 1"


def _fix_view(sql: str = TYPO_SQL, fixed_sql: str = FIXED_SQL) -> RunView:
    """Модель экрана с предложением об исправлении."""

    return build_run_view(
        build_report(
            "t1",
            {
                "original_sql": sql,
                "current_sql": sql,
                "schema_checked": True,
                "schema_result": {
                    "unknown": [
                        {
                            "kind": "column",
                            "table": "sku",
                            "name": "product_colr_id",
                            "candidates": [{"name": "product_color_id", "score": 96.0}],
                        }
                    ]
                },
                "fixed_sql": fixed_sql,
            },
            [{"step": "schema_fix"}],
        )
    )


def _index_view() -> RunView:
    """Модель экрана с предложением об индексе."""

    return build_run_view(
        build_report(
            "t1",
            {
                "original_sql": GOOD_SQL,
                "current_sql": GOOD_SQL,
                "schema_checked": True,
                "schema_result": {"unknown": []},
                "before_stats": {"median_ms": 5.0, "minimum_ms": 4.0, "maximum_ms": 6.0, "runs": 3},
                "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
            },
            [{"step": "index_proposal"}],
        )
    )


def test_decision_input_text_accepts_fix() -> None:
    """Принятое исправление переносится в поле ввода."""

    text = decision_input_text(_fix_view(), TYPO_SQL, accepted=True)

    assert text == FIXED_SQL


def test_decision_input_text_keeps_previous_on_decline() -> None:
    """Отказ от исправления не трогает текст в поле."""

    text = decision_input_text(_fix_view(), TYPO_SQL, accepted=False)

    assert text == TYPO_SQL


def test_decision_input_text_keeps_previous_for_index() -> None:
    """Решение по индексу не трогает текст в поле."""

    assert decision_input_text(_index_view(), GOOD_SQL, accepted=True) == GOOD_SQL
    assert decision_input_text(_index_view(), GOOD_SQL, accepted=False) == GOOD_SQL


def test_decision_input_text_ignores_stale_fix_on_index_step() -> None:
    """После принятия исправления остаток исправления не переписывает поле заново."""

    report = build_report(
        "t1",
        {
            "original_sql": TYPO_SQL,
            "current_sql": FIXED_SQL,
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "column",
                        "table": "sku",
                        "name": "product_colr_id",
                        "candidates": [{"name": "product_color_id", "score": 96.0}],
                    }
                ]
            },
            "fixed_sql": FIXED_SQL,
            "fix_applied": True,
            "before_stats": {"median_ms": 5.0, "minimum_ms": 4.0, "maximum_ms": 6.0, "runs": 3},
            "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
        },
        [{"step": "index_proposal"}],
    )
    view = build_run_view(report)

    assert view.fixed_sql == FIXED_SQL
    assert view.needs_fix_decision is False
    assert decision_input_text(view, FIXED_SQL, accepted=True) == FIXED_SQL


def test_prepare_decision_fills_input_and_hides_buttons() -> None:
    """Принятие исправления подставляет текст и сразу прячет ряд решений."""

    class _Input:
        value = TYPO_SQL

    class _Row:
        visible = True

    sql_input = _Input()
    decision_row = _Row()

    text = prepare_decision(
        _fix_view(),
        sql_input,
        decision_row,
        accepted=True,
    )

    assert text == FIXED_SQL
    assert sql_input.value == FIXED_SQL
    assert decision_row.visible is False


def test_prepare_decision_on_decline_keeps_text_and_hides_buttons() -> None:
    """Отказ оставляет текст прежним, но ряд решений всё равно скрыт."""

    class _Input:
        value = TYPO_SQL

    class _Row:
        visible = True

    sql_input = _Input()
    decision_row = _Row()

    prepare_decision(_fix_view(), sql_input, decision_row, accepted=False)

    assert sql_input.value == TYPO_SQL
    assert decision_row.visible is False


def test_show_decisions_follows_view_state() -> None:
    """Видимость ряда решений задаётся моделью экрана."""

    class _Row:
        visible = False

    decision_row = _Row()

    show_decisions(decision_row, _index_view())
    assert decision_row.visible is True

    show_decisions(decision_row, RunView())
    assert decision_row.visible is False


def test_fix_proposal_view_shows_replacements() -> None:
    """Экран показывает исправленный запрос и список замен."""

    report = build_report(
        "t1",
        {
            "original_sql": TYPO_SQL,
            "current_sql": TYPO_SQL,
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "column",
                        "table": "sku",
                        "name": "product_colr_id",
                        "candidates": [{"name": "product_color_id", "score": 96.0}],
                    }
                ]
            },
            "fixed_sql": FIXED_SQL,
        },
        [{"step": "schema_fix"}],
    )

    view = build_run_view(report)

    assert view.needs_fix_decision is True
    assert view.fixed_sql == FIXED_SQL
    assert view.replacements == ["product_colr_id (таблица sku) → product_color_id"]


def test_index_proposal_view() -> None:
    """Экран показывает команду создания индекса и обоснование."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "schema_checked": True,
            "schema_result": {"unknown": []},
            "before_stats": {
                "median_ms": 12.0,
                "minimum_ms": 11.0,
                "maximum_ms": 13.0,
                "runs": 3,
            },
            "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
        },
        [{"step": "index_proposal"}],
    )

    view = build_run_view(report)

    assert view.needs_index_decision is True
    assert view.index_ddl == "CREATE INDEX i ON sku (product_id)"
    assert view.index_reason == "по фильтру"
    assert view.before_ms == 12.0
    assert view.busy_hint == PROGRESS_CAPTION


def test_speedup_result_view() -> None:
    """Ускорение показывается обеими величинами и отношением."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "status": "compared",
            "apply_result": {
                "applied": True,
                "before_median_ms": 100.0,
                "after_median_ms": 10.0,
                "speedup": 10.0,
                "verdict": "speedup",
                "improved": True,
                "reason": "Bitmap Index Scan",
                "before": {"median_ms": 100.0, "minimum_ms": 99.0, "maximum_ms": 101.0, "runs": 3},
                "after": {"median_ms": 10.0, "minimum_ms": 9.0, "maximum_ms": 11.0, "runs": 3},
            },
        },
        [],
    )

    view = build_run_view(report)

    assert view.status == RunStatus.COMPLETED.value
    assert view.no_speedup is False
    assert "в 10.0 раза" in view.result_text
    assert view.after_ms == 10.0


def test_no_speedup_result_view_is_equal_not_error() -> None:
    """Отсутствие ускорения показывается как результат, а не как ошибка."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "status": "compared",
            "apply_result": {
                "applied": True,
                "before_median_ms": 15.0,
                "after_median_ms": 14.9,
                "speedup": 1.0,
                "verdict": "no_speedup",
                "improved": False,
                "reason": "низкая селективность колонки",
                "before": {"median_ms": 15.0, "minimum_ms": 14.0, "maximum_ms": 16.0, "runs": 3},
                "after": {"median_ms": 14.9, "minimum_ms": 14.1, "maximum_ms": 15.8, "runs": 3},
            },
        },
        [],
    )

    view = build_run_view(report)

    assert view.no_speedup is True
    assert view.has_errors is False
    assert "Значимого ускорения нет" in view.result_text
    assert "низкая селективность" in view.result_text


def test_slight_speedup_is_shown_as_no_speedup() -> None:
    """Ускорение в пределах порога показывается как отсутствие ускорения."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "status": "compared",
            "apply_result": {
                "applied": True,
                "before_median_ms": 100.0,
                "after_median_ms": 95.0,
                "speedup": 1.05,
                "verdict": "slight_speedup",
                "improved": True,
                "reason": "разница может объясняться шумом измерений",
            },
        },
        [],
    )

    view = build_run_view(report)

    assert view.no_speedup is True
    assert "порога значимости" in view.result_text


def test_missing_schema_check_shows_warning_and_no_success() -> None:
    """Пропущенная проверка имён показывается предупреждением, а не успехом."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "schema_checked": False,
            "warnings": ["Модель не вызвала инструмент проверки имён"],
            "before_stats": {
                "median_ms": 5.0,
                "minimum_ms": 4.0,
                "maximum_ms": 6.0,
                "runs": 3,
            },
            "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
        },
        [{"step": "index_proposal"}],
    )

    view = build_run_view(report)

    assert view.schema_checked is False
    assert view.schema_text == SCHEMA_SKIPPED_CAPTION
    assert any("не вызвала инструмент" in item for item in view.warnings)


def test_successful_schema_check_is_reported() -> None:
    """Пройденная проверка имён показывается отдельным сообщением."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "schema_checked": True,
            "schema_result": {"unknown": []},
            "before_stats": {"median_ms": 5.0, "minimum_ms": 4.0, "maximum_ms": 6.0, "runs": 3},
            "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
        },
        [{"step": "index_proposal"}],
    )

    view = build_run_view(report)

    assert view.schema_text == SCHEMA_OK_CAPTION
    assert view.warnings == []


def test_unfixable_view_shows_message_without_fix_card() -> None:
    """Экран без замен показывает сообщение и не показывает исправление."""

    message = (
        "таблица «zzz_table» не найдена\n"
        "Подходящих замен нет: исправьте запрос вручную."
    )
    report = build_report(
        "t1",
        {
            "original_sql": UNFIXABLE_SQL,
            "current_sql": UNFIXABLE_SQL,
            "schema_checked": True,
            "schema_result": {
                "unknown": [
                    {
                        "kind": "table",
                        "table": MISSING_TABLE,
                        "name": MISSING_TABLE,
                        "candidates": [],
                    }
                ]
            },
            "unfixable_message": message,
            "status": "unknown_unfixable",
        },
        [],
    )

    view = build_run_view(report)

    assert view.unfixable_message == message
    assert view.fixed_sql is None
    assert view.replacements == []
    assert view.index_ddl is None
    assert view.awaiting_decision is False
    assert view.needs_fix_decision is False
    assert view.has_errors is False
    assert view.is_terminal is True
    assert view.schema_text == UNFIXABLE_CAPTION


def test_unfixable_view_keeps_form_available() -> None:
    """После завершения без замен поле ввода снова доступно."""

    view = build_run_view(
        build_report(
            "t1",
            {
                "original_sql": UNFIXABLE_SQL,
                "current_sql": UNFIXABLE_SQL,
                "schema_checked": True,
                "schema_result": {
                    "unknown": [
                        {
                            "kind": "table",
                            "table": MISSING_TABLE,
                            "name": MISSING_TABLE,
                            "candidates": [],
                        }
                    ]
                },
                "unfixable_message": "таблица «zzz_table» не найдена",
                "status": "unknown_unfixable",
            },
            [],
        )
    )

    enabled, can_submit = form_enabled(view.sql, busy=False)

    assert view.is_terminal is True
    assert enabled is True
    assert can_submit is True


def test_declined_index_view_is_terminal() -> None:
    """Отказ от индекса завершает экран и не оставляет решений."""

    report = build_report(
        "t1",
        {
            "original_sql": GOOD_SQL,
            "current_sql": GOOD_SQL,
            "status": "index_declined",
            "index_declined": True,
            "schema_checked": True,
            "schema_result": {"unknown": []},
            "before_stats": {"median_ms": 5.0, "minimum_ms": 4.0, "maximum_ms": 6.0, "runs": 3},
            "proposal": {"ddl": "CREATE INDEX i ON sku (product_id)", "reason": "по фильтру"},
        },
        [],
    )

    view = build_run_view(report)

    assert view.is_terminal is True
    assert view.awaiting_decision is False
    assert view.result_text == ""


def test_pending_and_error_views() -> None:
    """Статус обработки и ошибка ввода показываются разными моделями."""

    pending = pending_view(sql=GOOD_SQL)
    failure = error_view("Запрос не задан", sql="")

    assert pending.busy_hint == PROGRESS_CAPTION
    assert pending.status == ""
    assert failure.has_errors is True
    assert failure.errors == ["Запрос не задан"]


async def test_submit_shows_status_then_proposal() -> None:
    """Отправка показывает статус до ожидания, а затем предложение."""

    world = FakeWorld(model=FakeModel(fixed_sql=FIXED_SQL))
    recorder = _RecordingView()

    view = await run_submit(
        TYPO_SQL,
        _client(world),
        set_busy=recorder.set_busy,
        show=recorder.show,
    )

    assert recorder.shown[0].busy_hint == PROGRESS_CAPTION
    assert recorder.busy_states == [True, False]
    assert view.needs_fix_decision is True
    assert view.fixed_sql == FIXED_SQL


async def test_accepted_fix_becomes_the_measured_request() -> None:
    """Принятый исправленный запрос — тот, что система обрабатывает дальше."""

    world = FakeWorld(model=FakeModel(fixed_sql=FIXED_SQL))
    recorder = _RecordingView()
    client = _client(world)

    started = await run_submit(
        TYPO_SQL, client, set_busy=recorder.set_busy, show=recorder.show
    )
    text = decision_input_text(started, TYPO_SQL, accepted=True)
    accepted = await run_decision(
        started.thread_id,
        client,
        accepted=True,
        set_busy=recorder.set_busy,
        show=recorder.show,
    )

    assert text == FIXED_SQL
    assert accepted.sql == FIXED_SQL
    assert accepted.needs_index_decision is True


async def test_declined_fix_keeps_input_text() -> None:
    """Отказ от исправления не подменяет текст в поле."""

    world = FakeWorld(model=FakeModel(fixed_sql=FIXED_SQL))
    recorder = _RecordingView()
    client = _client(world)

    started = await run_submit(
        TYPO_SQL, client, set_busy=recorder.set_busy, show=recorder.show
    )

    assert decision_input_text(started, TYPO_SQL, accepted=False) == TYPO_SQL


async def test_declining_fix_returns_input_availability() -> None:
    """После отказа поле ввода снова доступно для нового запроса."""

    world = FakeWorld(model=FakeModel(fixed_sql=FIXED_SQL))
    recorder = _RecordingView()
    client = _client(world)

    started = await run_submit(
        TYPO_SQL, client, set_busy=recorder.set_busy, show=recorder.show
    )
    declined = await run_decision(
        started.thread_id,
        client,
        accepted=False,
        set_busy=recorder.set_busy,
        show=recorder.show,
    )

    assert declined.awaiting_decision is False
    assert form_enabled(GOOD_SQL, busy=False) == (True, True)


async def test_run_submit_unblocks_form_after_failure() -> None:
    """Сбой обращения к API не оставляет форму заблокированной."""

    class _FailingClient:
        async def start(self, sql: str) -> RunView:
            """Сымитировать сбой."""

            raise RuntimeError("сервис недоступен")

    recorder = _RecordingView()

    with contextlib.suppress(RuntimeError):
        await run_submit(
            TYPO_SQL,
            _FailingClient(),  # type: ignore[arg-type]
            set_busy=recorder.set_busy,
            show=recorder.show,
        )

    assert recorder.busy_states == [True, False]


async def test_client_reports_invalid_input_as_error_view() -> None:
    """Ошибка API превращается в модель экрана с сообщением."""

    client, _ = build_ui_client()

    view = await client.start("delete from brand")

    assert view.has_errors is True
    assert "на чтение" in (view.errors[0] if view.errors else "")


async def test_client_loads_presets() -> None:
    """Клиент читает библиотеку предустановленных запросов."""

    client, _ = build_ui_client()

    presets = await client.presets()

    assert len(presets) >= 10
    assert build_preset_labels(presets)[0]
    assert all("expected" not in preset for preset in presets)


async def test_client_loads_schema() -> None:
    """Клиент читает схему базы для панели интерфейса."""

    client, _ = build_ui_client()

    schema = await client.schema()

    assert "sku" in schema.get("diagram", "")
    assert schema.get("caption")


async def test_client_with_empty_schema_returns_no_schema(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Пустой файл схемы приходит клиенту как пустая схема, а не ошибка."""

    path = tmp_path / "schema.yaml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("SQA_SCHEMA__PATH", str(path))

    client, _ = build_ui_client()

    schema = await client.schema()

    assert schema_panel_text(schema) == ""


async def test_client_with_empty_library_returns_no_presets(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Пустая библиотека приходит клиенту как пустой список, а не ошибка."""

    path = tmp_path / "presets.yaml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("SQA_PRESETS__PATH", str(path))

    client, _ = build_ui_client()

    presets = await client.presets()

    assert presets == []
    assert build_preset_labels(presets) == []
    assert form_enabled("", busy=False) == (True, False)


async def test_client_decodes_comparison_from_api() -> None:
    """Ответ API о сравнении превращается в модель экрана с обеими ветвями."""

    world = FakeWorld(
        model=FakeModel(entities=[{"table": "sku", "columns": ["product_id"]}]),
        measure=FakeMeasure(),
        apply=FakeApply(before_ms=100.0, after_ms=10.0),
    )
    client, _ = build_ui_client(world)
    started = await client.start(GOOD_SQL)
    proposed = await client.decide(started.thread_id, accepted=True)
    finished = await client.decide(proposed.thread_id, accepted=True)

    assert finished.status == RunStatus.COMPLETED.value
    assert finished.no_speedup is False
    assert finished.verdict is ComparisonVerdict.SPEEDUP


async def test_client_decodes_unfixable_message_from_api() -> None:
    """Клиент доносит сообщение о ненайденных именах до модели экрана."""

    world = FakeWorld(
        model=FakeModel(entities=[{"table": MISSING_TABLE, "columns": []}]),
    )
    client, _ = build_ui_client(world)

    view = await client.start(UNFIXABLE_SQL)

    assert view.status == RunStatus.UNKNOWN_UNFIXABLE.value
    assert MISSING_TABLE in view.unfixable_message
    assert view.fixed_sql is None
    assert view.awaiting_decision is False
    assert view.is_terminal is True
    assert view.schema_text == UNFIXABLE_CAPTION


__all__: list[Any] = []


async def test_mounted_page_renders_input_and_presets(mounted_ui_app: Any) -> None:
    """Смонтированная страница отдаёт поле ввода и список запросов.

    Приложение с интерфейсом собирается один раз на весь прогон
    (фикстура `mounted_ui_app`): NiceGUI не допускает второго монтирования в
    одном процессе.
    """

    import httpx

    from sql_query_agent.ui.view import (
        INPUT_LABEL,
        MOUNT_PATH,
        SCHEMA_PANEL_CAPTION,
        SUBMIT_LABEL,
    )

    transport = httpx.ASGITransport(app=mounted_ui_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"{MOUNT_PATH}/")

    assert response.status_code == 200
    assert INPUT_LABEL in response.text
    assert SUBMIT_LABEL in response.text
    assert PRESETS_CAPTION in response.text
    assert SCHEMA_PANEL_CAPTION in response.text
    assert "product_size" in response.text
    assert "100 000" in response.text


def test_mount_ui_connects_client_to_application(monkeypatch: Any) -> None:
    """Интерфейс монтируется в переданное приложение, а не в отдельное.

    Проверяется связка «API и UI в одном процессе»: клиент интерфейса ходит в
    то же приложение FastAPI, а NiceGUI монтируется именно в него. Настоящее
    монтирование подменяется: в одном процессе оно допускается один раз.
    """

    import nicegui.ui

    calls: list[tuple[Any, str]] = []

    def _run_with(app: Any, *, mount_path: str = "/", **_kwargs: Any) -> None:
        """Запомнить приложение и путь монтирования."""

        calls.append((app, mount_path))

    monkeypatch.setattr(nicegui.ui, "run_with", _run_with)
    app = build_test_app(FakeWorld())

    mount_ui(app, _client(FakeWorld()), presets=PRESETS)

    assert calls == [(app, MOUNT_PATH)]
