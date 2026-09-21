"""Тесты проверки имён: сверка с каталогом и подбор похожих названий."""

import pytest

from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.domain import ColumnNames, UnknownNameKind
from sql_query_agent.tools.schema_check import check_entities, suggest_names

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
