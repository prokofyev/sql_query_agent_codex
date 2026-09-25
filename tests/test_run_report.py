"""Тесты отчёта о прогоне: статусы, решения, замены и сравнение."""

from sql_query_agent.domain_comparison import ComparisonVerdict
from sql_query_agent.run.report import (
    RunStatus,
    build_report,
    confirm_replacements,
    tokenize,
)

UNKNOWN = [
    {
        "kind": "column",
        "table": "sku",
        "name": "product_colr_id",
        "candidates": [
            {"name": "product_color_id", "score": 96.8},
            {"name": "product_id", "score": 80.0},
        ],
    }
]
ORIGINAL = "select * from sku where product_colr_id = 1"
FIXED = "select * from sku where product_color_id = 1"

UNFIXABLE = [
    {
        "kind": "table",
        "table": "prodcts",
        "name": "prodcts",
        "candidates": [],
    }
]
UNFIXABLE_MESSAGE = (
    "таблица «prodcts» не найдена\nПодходящих замен нет: исправьте запрос вручную."
)


def test_unfixable_run_is_terminal_and_not_an_error() -> None:
    """Прогон без замен завершён, а не провален: сообщение есть, ошибки нет."""

    report = build_report(
        "t1",
        {
            "original_sql": "select * from prodcts",
            "current_sql": "select * from prodcts",
            "schema_checked": True,
            "schema_result": {"unknown": UNFIXABLE},
            "unfixable_message": UNFIXABLE_MESSAGE,
            "status": "unknown_unfixable",
        },
        [],
    )

    assert report.status is RunStatus.UNKNOWN_UNFIXABLE
    assert report.status.is_terminal is True
    assert report.awaiting_decision is False
    assert report.error is None
    assert report.unfixable_message == UNFIXABLE_MESSAGE
    assert report.unknown == UNFIXABLE


def test_unfixable_run_has_no_fix_or_index_proposal() -> None:
    """Прогон без замен не показывает ни исправления, ни предложения об индексе."""

    report = build_report(
        "t2",
        {
            "original_sql": "select * from prodcts",
            "current_sql": "select * from prodcts",
            "schema_checked": True,
            "schema_result": {"unknown": UNFIXABLE},
            "unfixable_message": UNFIXABLE_MESSAGE,
            "status": "unknown_unfixable",
        },
        [],
    )

    assert report.fix is None
    assert report.index is None
    assert report.comparison is None
    assert report.decisions == {"fix": "not_required", "index": "not_offered"}


def test_fixable_run_has_no_unfixable_message() -> None:
    """В исправимом прогоне сообщения о неисправимых именах нет."""

    report = build_report("t3", _values(fixed_sql=FIXED), [{"step": "schema_fix"}])

    assert report.unfixable_message == ""


TYPOS_BOTH = [
    {
        "kind": "table",
        "table": "sku2",
        "name": "sku2",
        "candidates": [{"name": "sku", "score": 85.7}],
    },
    {
        "kind": "column",
        "table": "sku2",
        "name": "product_id2",
        "candidates": [{"name": "product_id", "score": 95.2}],
    },
]
BOTH_ORIGINAL = "select * from sku2 where product_id2 = 42"
BOTH_FIXED = "select * from sku where product_id = 42"

# Замены в том виде, в каком их заявляет модель: старый формат `unknown`
# остаётся у инструмента, а заявленные замены приходят отдельно.
BOTH_CLAIMED = [
    {"old_name": "sku2", "new_name": "sku", "kind": "table"},
    {"old_name": "product_id2", "new_name": "product_id", "kind": "column"},
]


def test_tokenize_keeps_identifiers_whole() -> None:
    """Токенизатор не рвёт имена и не склеивает их со знаками."""

    assert tokenize("select * from sku2 where a = 1") == [
        "select",
        "*",
        "from",
        "sku2",
        "where",
        "a",
        "=",
        "1",
    ]
    assert tokenize("p.product_id") == ["p", ".", "product_id"]


def test_substring_names_are_reported_as_replacements() -> None:
    """Замена с подстрочным новым именем попадает в список замен."""

    replacements = confirm_replacements(BOTH_CLAIMED, BOTH_ORIGINAL, BOTH_FIXED)

    assert [(item.old_name, item.new_name) for item in replacements] == [
        ("sku2", "sku"),
        ("product_id2", "product_id"),
    ]
    assert [item.kind for item in replacements] == ["table", "column"]


def test_added_qualifier_is_confirmed_as_replacement() -> None:
    """Квалификация колонки подтверждается, хотя имя колонки осталось прежним."""

    claimed = [
        {
            "old_name": "brand_id",
            "new_name": "product.brand_id",
            "kind": "column",
        }
    ]
    original = "select brand_id from product join brand on brand.brand_id = product.brand_id"
    fixed = "select product.brand_id from product join brand on brand.brand_id = product.brand_id"

    replacements = confirm_replacements(claimed, original, fixed)

    assert [(item.old_name, item.new_name) for item in replacements] == [
        ("brand_id", "product.brand_id")
    ]


def test_unchanged_query_has_no_replacements() -> None:
    """Совпадение исправленного и исходного запросов даёт пустой список замен."""

    assert confirm_replacements(BOTH_CLAIMED, BOTH_ORIGINAL, BOTH_ORIGINAL) == []


def test_claimed_replacement_absent_from_text_is_dropped() -> None:
    """Заявленная замена, которой нет в тексте, не попадает в список.

    Так ведёт себя модель в случае «менять нечего»: она заявляет правку,
    которой не делала.
    """

    claimed = [
        {"old_name": "brand_name", "new_name": "brand.brand_name", "kind": "column"}
    ]

    assert confirm_replacements(claimed, BOTH_ORIGINAL, BOTH_ORIGINAL) == []


def test_partial_fix_reports_only_applied_replacement() -> None:
    """Если модель заменила только таблицу, в списке замен только она."""

    partially_fixed = "select * from sku where product_id2 = 42"

    replacements = confirm_replacements(BOTH_CLAIMED, BOTH_ORIGINAL, partially_fixed)

    assert [(item.old_name, item.new_name) for item in replacements] == [("sku2", "sku")]


def _values(**overrides: object) -> dict[str, object]:
    """Базовое состояние прогона с переопределениями."""

    values: dict[str, object] = {
        "original_sql": ORIGINAL,
        "current_sql": ORIGINAL,
        "schema_checked": True,
        "schema_result": {"unknown": UNKNOWN},
        "fix_replacements": [
            {
                "old_name": "product_colr_id",
                "new_name": "product_color_id",
                "kind": "column",
                "table": "sku",
            }
        ],
    }
    values.update(overrides)
    return values


def test_awaiting_fix_decision() -> None:
    """Остановка на исправлении даёт статус ожидания решения."""

    report = build_report(
        "t1",
        _values(fixed_sql=FIXED),
        [{"step": "schema_fix"}],
    )

    assert report.status is RunStatus.AWAITING_DECISION
    assert report.awaiting_decision is True
    assert report.step == "schema_fix"
    assert report.status.is_terminal is False


def test_replacements_are_confirmed_by_text() -> None:
    """Замены показываются, потому что текст запроса их подтверждает."""

    report = build_report("t1", _values(fixed_sql=FIXED), [{"step": "schema_fix"}])

    assert report.fix is not None
    assert [item.new_name for item in report.fix.replacements] == ["product_color_id"]
    assert report.fix.replacements[0].old_name == "product_colr_id"
    assert report.fix.replacements[0].kind == "column"


def test_missing_replacement_is_not_reported() -> None:
    """Если модель ничего не заменила, список замен пуст."""

    report = build_report("t1", _values(fixed_sql=ORIGINAL), [{"step": "schema_fix"}])

    assert report.fix is not None
    assert report.fix.replacements == []


def test_reported_replacement_ignores_unconfirmed_claims() -> None:
    """В отчёт попадают только подтверждённые замены, а не все заявленные."""

    report = build_report(
        "t1",
        _values(
            fixed_sql=FIXED,
            fix_replacements=[
                {
                    "old_name": "product_colr_id",
                    "new_name": "product_color_id",
                    "kind": "column",
                },
                {
                    "old_name": "product_id",
                    "new_name": "sku_id",
                    "kind": "column",
                },
            ],
        ),
        [{"step": "schema_fix"}],
    )

    assert report.fix is not None
    assert [item.new_name for item in report.fix.replacements] == ["product_color_id"]


def test_declined_fix_is_terminal() -> None:
    """Отказ от исправления завершает прогон."""

    report = build_report(
        "t1",
        _values(fix_declined=True, status="fix_declined"),
        [],
    )

    assert report.status is RunStatus.FIX_DECLINED
    assert report.awaiting_decision is False
    assert report.decisions["fix"] == "declined"
    assert report.decisions["index"] == "not_offered"


def test_declined_index_is_terminal_and_keeps_step_free() -> None:
    """Отказ от индекса завершает прогон без применения."""

    report = build_report(
        "t1",
        _values(
            fixed_sql=FIXED,
            fix_applied=True,
            index_declined=True,
            status="index_declined",
            before_stats={"median_ms": 10.0, "minimum_ms": 9.0, "maximum_ms": 11.0, "runs": 3},
            proposal={"ddl": "CREATE INDEX i ON sku (product_color_id)", "reason": "по фильтру"},
        ),
        [],
    )

    assert report.status is RunStatus.INDEX_DECLINED
    assert report.decisions == {"fix": "accepted", "index": "declined"}
    assert report.comparison is None
    assert report.index is not None


def test_completed_run_without_speedup_is_marked() -> None:
    """Случай без ускорения попадает в отчёт как обычный результат."""

    report = build_report(
        "t1",
        _values(
            status="compared",
            apply_result={
                "applied": True,
                "before_median_ms": 15.0,
                "after_median_ms": 14.9,
                "speedup": 1.007,
                "verdict": "no_speedup",
                "improved": False,
                "reason": "Низкая селективность",
                "before": {"median_ms": 15.0, "minimum_ms": 14.0, "maximum_ms": 16.0, "runs": 3},
                "after": {"median_ms": 14.9, "minimum_ms": 14.1, "maximum_ms": 15.8, "runs": 3},
            },
        ),
        [],
    )

    assert report.status is RunStatus.COMPLETED
    assert report.comparison is not None
    assert report.comparison.no_speedup is True
    assert report.comparison.verdict is ComparisonVerdict.NO_SPEEDUP


def test_slight_speedup_counts_as_no_speedup() -> None:
    """Ускорение в пределах порога значимости не считается ускорением."""

    report = build_report(
        "t1",
        _values(
            status="compared",
            apply_result={
                "applied": True,
                "before_median_ms": 100.0,
                "after_median_ms": 95.0,
                "speedup": 1.05,
                "verdict": "slight_speedup",
                "improved": True,
            },
        ),
        [],
    )

    assert report.comparison is not None
    assert report.comparison.no_speedup is True


def test_failed_run_is_terminal() -> None:
    """Ошибка замера завершает прогон со статусом ошибки."""

    report = build_report(
        "t1",
        _values(status="measure_failed", warnings=["Запрос превысил время выполнения"]),
        [],
    )

    assert report.status is RunStatus.FAILED
    assert report.error == "Запрос превысил время выполнения"


def test_report_without_interrupts_and_without_measure_is_failed() -> None:
    """Прогон без замера и без решения считается незавершённым."""

    report = build_report("t1", _values(), [])

    assert report.status is RunStatus.FAILED
