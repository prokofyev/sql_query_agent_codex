"""Тесты проверки имён: сверка с каталогом и подбор похожих названий."""

import pytest

from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.domain import ColumnNames, NameCandidate, UnknownName, UnknownNameKind
from sql_query_agent.tools.schema_check import (
    can_fix_all,
    check_entities,
    describe_unfixable,
    suggest_names,
    unfixable_names,
)

THRESHOLD = 65.0
LIMIT = 3


@pytest.fixture
def catalog() -> SchemaCatalog:
    """Каталог, повторяющий структуру демо-базы products."""

    return SchemaCatalog.from_rows(
        [
            ("brand", "brand_id"),
            ("brand", "brand_name"),
            ("product", "product_id"),
            ("product", "product_name"),
            ("product", "brand_id"),
            ("sku", "sku_id"),
            ("sku", "product_id"),
            ("sku", "product_size_id"),
            ("sku", "product_color_id"),
        ]
    )


def test_existing_names_pass(catalog: SchemaCatalog) -> None:
    """Существующие имена проходят проверку без замечаний."""

    result = check_entities(
        catalog,
        [ColumnNames(table="product", columns=["product_id", "product_name"])],
        threshold=THRESHOLD,
        suggestion_limit=LIMIT,
    )

    assert result.ok is True
    assert result.unknown == []
    assert result.checked_tables == 1
    assert result.checked_columns == 2


def test_missing_column_is_reported(catalog: SchemaCatalog) -> None:
    """Отсутствующая колонка попадает в результат с относящейся таблицей."""

    result = check_entities(
        catalog,
        [ColumnNames(table="product", columns=["product_nam"])],
        threshold=THRESHOLD,
        suggestion_limit=LIMIT,
    )

    assert result.ok is False
    assert len(result.unknown) == 1
    missed = result.unknown[0]
    assert missed.kind is UnknownNameKind.COLUMN
    assert missed.table == "product"
    assert missed.name == "product_nam"


def test_missing_table_is_reported(catalog: SchemaCatalog) -> None:
    """Отсутствующая таблица попадает и в общий список, и в список таблиц."""

    result = check_entities(
        catalog,
        [ColumnNames(table="skuu", columns=["sku_id"])],
        threshold=THRESHOLD,
        suggestion_limit=LIMIT,
    )

    assert result.ok is False
    assert result.unknown_tables == ["skuu"]
    assert result.unknown[0].kind is UnknownNameKind.TABLE
    assert result.unknown[0].table == "skuu"


def test_typo_in_column_finds_existing_name() -> None:
    """Для опечатки `product_colr` находится `product_color`."""

    candidates = suggest_names(
        "product_colr",
        ["product_color_id", "product_size_id", "sku_id"],
        threshold=THRESHOLD,
        limit=LIMIT,
    )

    assert candidates
    assert candidates[0].name == "product_color_id"
    assert candidates[0].score >= THRESHOLD


def test_unrelated_name_has_no_candidates() -> None:
    """Для имени без близких совпадений кандидатов нет."""

    candidates = suggest_names(
        "zzzzzzzz",
        ["product_color_id", "product_size_id"],
        threshold=THRESHOLD,
        limit=LIMIT,
    )

    assert candidates == []


def test_candidate_limit_is_respected(catalog: SchemaCatalog) -> None:
    """Число предложенных кандидатов не превышает лимит."""

    candidates = suggest_names(
        "product_id",
        ["product_id", "product_name", "product_color_id", "product_size_id"],
        threshold=0.0,
        limit=2,
    )

    assert len(candidates) == 2


def test_typo_in_table_is_detected_without_db_error(catalog: SchemaCatalog) -> None:
    """Опечатка в имени таблицы распознаётся по каталогу, без обращения к СУБД."""

    result = check_entities(
        catalog,
        [ColumnNames(table="prodct", columns=["product_id"])],
        threshold=THRESHOLD,
        suggestion_limit=LIMIT,
    )

    assert result.unknown_tables == ["prodct"]
    candidate_names = [c.name for c in result.unknown[0].candidates]
    assert "product" in candidate_names


def _column(name: str, candidates: list[str]) -> UnknownName:
    """Ненайденная колонка с заданными именами кандидатов."""

    return UnknownName(
        kind=UnknownNameKind.COLUMN,
        table="sku",
        name=name,
        candidates=[NameCandidate(name=item, score=90.0) for item in candidates],
    )


def _table(name: str, candidates: list[str]) -> UnknownName:
    """Ненайденная таблица с заданными именами кандидатов."""

    return UnknownName(
        kind=UnknownNameKind.TABLE,
        table=name,
        name=name,
        candidates=[NameCandidate(name=item, score=90.0) for item in candidates],
    )


def test_all_names_with_candidates_are_fixable() -> None:
    """Все ненайденные имена с кандидатами — запрос исправим."""

    unknown = [_column("product_colr_id", ["product_color_id"]), _table("skuu", ["sku"])]

    assert unfixable_names(unknown) == []
    assert can_fix_all(unknown) is True
    assert describe_unfixable(unknown) == ""


def test_one_name_without_candidates_blocks_fixing() -> None:
    """Имя без кандидатов делает запрос неисправимым."""

    unknown = [_column("product_colr_id", ["product_color_id"]), _table("zzz", [])]

    assert [item.name for item in unfixable_names(unknown)] == ["zzz"]
    assert can_fix_all(unknown) is False


def test_all_names_without_candidates_are_unfixable() -> None:
    """Если кандидатов нет ни у кого, исправлять нечего."""

    unknown = [_table("zzz", []), _column("qqq", [])]

    assert [item.name for item in unfixable_names(unknown)] == ["zzz", "qqq"]
    assert can_fix_all(unknown) is False


def test_no_unknown_names_is_not_a_fix_case() -> None:
    """Пустой список ненайденных имён — это не случай исправления."""

    assert can_fix_all([]) is False


def test_message_describes_missing_table() -> None:
    """Сообщение о ненайденной таблице называет её и говорит об отсутствии замен."""

    message = describe_unfixable([_table("prodcts", [])])

    assert "таблица «prodcts» не найдена" in message
    assert "Подходящих замен нет" in message
    assert "колонка" not in message


def test_message_describes_missing_column_with_table() -> None:
    """Сообщение о ненайденной колонке указывает и колонку, и таблицу."""

    message = describe_unfixable([_column("product_colr_id", [])])

    assert "колонка «product_colr_id» не найдена в таблице «sku»" in message
    assert "Подходящих замен нет" in message


def test_message_lists_every_unfixable_name() -> None:
    """Сообщение перечисляет все имена без кандидатов."""

    message = describe_unfixable(
        [
            _table("prodcts", []),
            _column("qqq", []),
            _column("product_colr_id", ["product_color_id"]),
        ]
    )

    lines = message.splitlines()
    assert lines[0] == "таблица «prodcts» не найдена"
    assert lines[1] == "колонка «qqq» не найдена в таблице «sku»"
    assert len(lines) == 3


def test_message_has_no_invented_names() -> None:
    """В сообщении нет имён, которых не было во входных данных."""

    message = describe_unfixable([_table("prodcts", [])])

    assert "product" not in message


def test_no_candidates_with_high_threshold(catalog: SchemaCatalog) -> None:
    """Порог выше любого совпадения оставляет имя без кандидатов и без замен."""

    result = check_entities(
        catalog,
        [ColumnNames(table="product", columns=["product_colr_id"])],
        threshold=100.0,
        suggestion_limit=LIMIT,
    )

    missed = result.unknown[0]
    assert missed.name == "product_colr_id"
    assert missed.candidates == []
    assert result.is_fixable is False
