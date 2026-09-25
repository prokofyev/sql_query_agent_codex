"""Тесты проверки имён: сверка с каталогом и подбор похожих названий."""

import pytest

from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.domain import (
    NameCandidate,
    SchemaEntities,
    UnknownName,
    UnknownNameKind,
)
from sql_query_agent.tools.schema_check import (
    can_fix_all,
    check_entities,
    describe_unfixable,
    split_qualified,
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


def _check(catalog: SchemaCatalog, entities: SchemaEntities):
    """Проверка имён с общими для тестов порогом и лимитом."""

    return check_entities(
        catalog,
        entities,
        threshold=THRESHOLD,
        suggestion_limit=LIMIT,
    )


def test_catalog_finds_tables_with_column(catalog: SchemaCatalog) -> None:
    """Каталог находит все таблицы, в которых есть колонка."""

    assert catalog.tables_with_column("brand_id") == ["brand", "product"]
    assert catalog.tables_with_column("brand_name") == ["brand"]
    assert catalog.tables_with_column("no_such_column") == []


def test_existing_names_pass(catalog: SchemaCatalog) -> None:
    """Существующие имена проходят проверку без замечаний."""

    result = _check(
        catalog,
        SchemaEntities(tables=["product"], columns=["product_id", "product_name"]),
    )

    assert result.ok is True
    assert result.unknown == []
    assert result.checked_tables == 1
    assert result.checked_columns == 2


def test_missing_column_is_reported(catalog: SchemaCatalog) -> None:
    """Отсутствующая колонка попадает в результат с таблицей, где её искали."""

    result = _check(catalog, SchemaEntities(tables=["product"], columns=["product_nam"]))

    assert result.ok is False
    assert len(result.unknown) == 1
    missed = result.unknown[0]
    assert missed.kind is UnknownNameKind.COLUMN
    assert missed.table == "product"
    assert missed.searched_tables == ["product"]
    assert missed.name == "product_nam"


def test_missing_table_is_reported(catalog: SchemaCatalog) -> None:
    """Отсутствующая таблица попадает и в общий список, и в список таблиц."""

    result = _check(catalog, SchemaEntities(tables=["skuu"], columns=["sku_id"]))

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

    result = _check(catalog, SchemaEntities(tables=["prodct"], columns=["product_id"]))

    assert result.unknown_tables == ["prodct"]
    candidate_names = [c.name for c in result.unknown[0].candidates]
    assert "product" in candidate_names


def test_column_belongs_to_joined_table(catalog: SchemaCatalog) -> None:
    """Колонка `brand_name` находится в подключённой таблице `brand`, а не в `product`.

    Это исходная ошибка: модель приписывала колонку `product`, инструмент
    искал её только там и находил ложного кандидата `brand_id`.
    """

    result = _check(
        catalog,
        SchemaEntities(
            tables=["product", "brand"],
            aliases=["b=brand"],
            columns=["brand_name", "product_name"],
        ),
    )

    assert result.ok is True
    assert result.unknown == []


def test_qualified_column_is_checked_in_its_table(catalog: SchemaCatalog) -> None:
    """Колонка с квалификатором проверяется по таблице квалификатора.

    Алиас разворачивается в настоящее имя таблицы, поэтому `p.brand_name`
    проверяется по колонкам `product`, а не по колонкам `brand`.
    """

    result = _check(
        catalog,
        SchemaEntities(
            tables=["product", "brand"],
            aliases=["p=product"],
            columns=["brand.brand_id", "p.brand_name"],
        ),
    )

    assert [item.name for item in result.unknown] == ["brand_name"]
    missed = result.unknown[0]
    assert missed.table == "product"
    assert missed.searched_tables == ["product"]


def test_unknown_qualifier_is_reported_as_missing_table(catalog: SchemaCatalog) -> None:
    """Квалификатор, которого нет ни в запросе, ни в схеме, — отдельная находка."""

    result = _check(
        catalog,
        SchemaEntities(tables=["brand"], columns=["prodct.product_id"]),
    )

    # Колонка найдена среди колонок таблицы-кандидата, поэтому ненайденной
    # остаётся только таблица.
    assert [item.name for item in result.unknown] == ["prodct"]
    assert result.unknown[0].kind is UnknownNameKind.TABLE


def test_column_of_unknown_qualifier_without_candidates_is_reported(
    catalog: SchemaCatalog,
) -> None:
    """Колонка отсутствующей таблицы проверяется по колонкам кандидатов."""

    result = _check(
        catalog,
        SchemaEntities(tables=["brand"], columns=["prodct.product_nam"]),
    )

    kinds = {(item.kind, item.name) for item in result.unknown}
    assert (UnknownNameKind.TABLE, "prodct") in kinds
    assert (UnknownNameKind.COLUMN, "product_nam") in kinds


def test_ambiguous_column_is_reported_with_candidate_qualifiers(
    catalog: SchemaCatalog,
) -> None:
    """Колонка из двух таблиц запроса помечается неоднозначной и исправима."""

    result = _check(
        catalog,
        SchemaEntities(tables=["product", "brand"], columns=["brand_id", "product_name"]),
    )

    assert result.ok is False
    assert len(result.unknown) == 1
    ambiguous = result.ambiguous
    assert [item.name for item in ambiguous] == ["brand_id"]
    assert ambiguous[0].searched_tables == ["product", "brand"]
    candidates = [candidate.name for candidate in ambiguous[0].candidates]
    assert candidates == ["product.brand_id", "brand.brand_id"]
    assert result.is_fixable is True


def test_ambiguous_column_is_reported_once(catalog: SchemaCatalog) -> None:
    """Повторное вхождение неоднозначной колонки не дублирует запись."""

    result = _check(
        catalog,
        SchemaEntities(
            tables=["product", "brand"],
            columns=["brand_id", "brand_id", "product_name"],
        ),
    )

    assert [item.name for item in result.ambiguous] == ["brand_id"]


def test_column_with_qualifier_is_not_ambiguous(catalog: SchemaCatalog) -> None:
    """Квалифицированная колонка не считается неоднозначной."""

    result = _check(
        catalog,
        SchemaEntities(tables=["product", "brand"], columns=["product.brand_id"]),
    )

    assert result.ambiguous == []
    assert result.ok is True


def test_typo_in_column_is_found_across_query_tables(catalog: SchemaCatalog) -> None:
    """Кандидаты ищутся по колонкам всех таблиц запроса."""

    result = _check(
        catalog,
        SchemaEntities(tables=["product", "brand"], columns=["brand_nam"]),
    )

    missed = result.unknown[0]
    assert missed.name == "brand_nam"
    assert missed.searched_tables == ["product", "brand"]
    names = [candidate.name for candidate in missed.candidates]
    assert "brand_name" in names


def test_split_qualified_parses_qualifier() -> None:
    """Квалификатор отделяется от имени колонки, кавычки отбрасываются."""

    assert split_qualified("brand_name") == (None, "brand_name")
    assert split_qualified("product.brand_id") == ("product", "brand_id")
    assert split_qualified('"brand"."brand_id"') == ("brand", "brand_id")


def _column(name: str, candidates: list[str]) -> UnknownName:
    """Ненайденная колонка с заданными именами кандидатов."""

    return UnknownName(
        kind=UnknownNameKind.COLUMN,
        table="sku",
        name=name,
        candidates=[NameCandidate(name=item, score=90.0) for item in candidates],
        searched_tables=["sku"],
    )


def _table(name: str, candidates: list[str]) -> UnknownName:
    """Ненайденная таблица с заданными именами кандидатов."""

    return UnknownName(
        kind=UnknownNameKind.TABLE,
        table=name,
        name=name,
        candidates=[NameCandidate(name=item, score=90.0) for item in candidates],
    )


def test_fixable_names_are_those_with_candidates() -> None:
    """Неисправимыми считаются только имена без кандидатов."""

    unknown = [_column("product_colr_id", ["product_color_id"]), _table("prodcts", [])]

    assert [item.name for item in unfixable_names(unknown)] == ["prodcts"]
    assert can_fix_all(unknown) is False


def test_message_describes_missing_table() -> None:
    """Сообщение о ненайденной таблице не путает её с колонкой."""

    message = describe_unfixable([_table("prodcts", [])])

    assert "таблица «prodcts» не найдена" in message
    assert "колонка" not in message


def test_message_describes_missing_column_with_table() -> None:
    """Сообщение о ненайденной колонке указывает и колонку, и таблицу."""

    message = describe_unfixable([_column("product_colr_id", [])])

    assert "колонка «product_colr_id» не найдена в таблице «sku»" in message
    assert "Подходящих замен нет" in message


def test_message_lists_every_searched_table() -> None:
    """Когда колонку искали в нескольких таблицах, сообщение перечисляет их."""

    item = _column("brand_nam", [])
    item.searched_tables = ["product", "brand"]

    message = describe_unfixable([item])

    assert "колонка «brand_nam» не найдена в таблицах «product», «brand»" in message


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
        SchemaEntities(tables=["product"], columns=["product_colr_id"]),
        threshold=100.0,
        suggestion_limit=LIMIT,
    )

    missed = result.unknown[0]
    assert missed.name == "product_colr_id"
    assert missed.candidates == []
    assert result.is_fixable is False


def test_missing_table_checks_its_columns(catalog: SchemaCatalog) -> None:
    """Опечатка и в таблице, и в колонке даёт оба ненайденных имени."""

    result = _check(catalog, SchemaEntities(tables=["sku2"], columns=["product_id2"]))

    found = {(item.kind, item.name) for item in result.unknown}
    assert found == {
        (UnknownNameKind.TABLE, "sku2"),
        (UnknownNameKind.COLUMN, "product_id2"),
    }
    assert result.checked_columns == 1


def test_missing_table_column_gets_candidates_from_candidates_table(
    catalog: SchemaCatalog,
) -> None:
    """Кандидаты колонки отсутствующей таблицы берутся из колонок таблиц-кандидатов."""

    result = _check(catalog, SchemaEntities(tables=["sku2"], columns=["product_id2"]))

    column = next(item for item in result.unknown if item.kind is UnknownNameKind.COLUMN)
    assert column.searched_tables == ["sku"]
    names = [candidate.name for candidate in column.candidates]
    assert names[0] == "product_id"
    assert set(names) <= {"sku_id", "product_id", "product_size_id", "product_color_id"}
    assert can_fix_all(result.unknown) is True


def test_missing_table_column_present_in_candidates_is_not_reported(
    catalog: SchemaCatalog,
) -> None:
    """Колонка, найденная среди колонок таблиц-кандидатов, ненайденной не считается."""

    result = _check(catalog, SchemaEntities(tables=["sku2"], columns=["product_id"]))

    assert [item.kind for item in result.unknown] == [UnknownNameKind.TABLE]
    assert result.unknown_tables == ["sku2"]


def test_missing_table_without_candidates_skips_its_columns(catalog: SchemaCatalog) -> None:
    """Без кандидатов у таблицы её колонки ненайденными не объявляются."""

    result = _check(catalog, SchemaEntities(tables=["zzz_unknown"], columns=["qqq"]))

    assert [item.kind for item in result.unknown] == [UnknownNameKind.TABLE]
    assert result.checked_columns == 0


def test_missing_table_column_without_candidates_blocks_fixing(
    catalog: SchemaCatalog,
) -> None:
    """Колонка отсутствующей таблицы без кандидатов делает запрос неисправимым."""

    result = _check(catalog, SchemaEntities(tables=["sku2"], columns=["qqq"]))

    column = next(item for item in result.unknown if item.kind is UnknownNameKind.COLUMN)
    assert column.candidates == []
    assert result.is_fixable is False
    assert can_fix_all(result.unknown) is False
