"""Тесты доменных моделей проверки имён: признак исправимости.

Свойства считаются по уже полученному результату проверки, поэтому база и
каталог здесь не нужны: это чистая арифметика над списком ненайденных имён.
"""

from sql_query_agent.domain import (
    NameCandidate,
    SchemaCheckResult,
    SchemaEntities,
    UnknownName,
    UnknownNameKind,
)


def _unknown(*, candidates: list[NameCandidate] | None = None) -> UnknownName:
    """Ненайденное имя с заданными кандидатами."""

    return UnknownName(
        kind=UnknownNameKind.COLUMN,
        table="sku",
        name="product_colr_id",
        candidates=candidates or [],
    )


def test_name_with_candidate_is_fixable() -> None:
    """Имя с хотя бы одним кандидатом считается исправимым."""

    item = _unknown(candidates=[NameCandidate(name="product_color_id", score=96.0)])

    assert item.is_fixable is True


def test_name_without_candidates_is_unfixable() -> None:
    """Имя без кандидатов исправить нечем."""

    assert _unknown().is_fixable is False


def test_result_with_only_fixable_names_is_fixable() -> None:
    """Результат исправим, когда замены есть у всех ненайденных имён."""

    result = SchemaCheckResult(
        ok=False,
        unknown=[
            _unknown(candidates=[NameCandidate(name="product_color_id", score=96.0)]),
            _unknown(candidates=[NameCandidate(name="product_size_id", score=88.0)]),
        ],
    )

    assert result.unfixable == []
    assert result.is_fixable is True


def test_result_with_one_unfixable_name_is_not_fixable() -> None:
    """Одного имени без кандидатов достаточно, чтобы результат был неисправим."""

    result = SchemaCheckResult(
        ok=False,
        unknown=[
            _unknown(candidates=[NameCandidate(name="product_color_id", score=96.0)]),
            _unknown(),
        ],
    )

    assert [item.name for item in result.unfixable] == ["product_colr_id"]
    assert result.is_fixable is False


def test_result_with_clean_schema_is_not_a_fix_case() -> None:
    """Без ненайденных имён исправлять нечего, и это не случай исправления."""

    result = SchemaCheckResult(ok=True, checked_tables=1, checked_columns=2)

    assert result.unfixable == []
    assert result.is_fixable is False


def test_entities_keep_names_as_written() -> None:
    """Аргумент инструмента сохраняет имена ровно так, как их дала модель."""

    entities = SchemaEntities(
        tables=["skuu"],
        aliases=["p=product"],
        columns=["p.product_id", "brand_name"],
    )

    assert entities.tables == ["skuu"]
    assert entities.aliases == ["p=product"]
    assert entities.columns == ["p.product_id", "brand_name"]


def test_entities_tolerate_none_instead_of_list() -> None:
    """Модель иногда присылает `null` вместо пустого списка."""

    entities = SchemaEntities.model_validate({"tables": None, "aliases": None, "columns": None})

    assert entities.tables == []
    assert entities.aliases == []
    assert entities.columns == []


def test_result_is_fixable_with_only_ambiguous_column() -> None:
    """Неоднозначная колонка исправима: её лечит квалификатор."""

    result = SchemaCheckResult(
        ok=False,
        unknown=[
            UnknownName(
                kind=UnknownNameKind.COLUMN,
                table="",
                name="brand_id",
                candidates=[NameCandidate(name="product.brand_id", score=100.0)],
                ambiguous=True,
            )
        ],
    )

    assert [item.name for item in result.ambiguous] == ["brand_id"]
    assert result.is_fixable is True
