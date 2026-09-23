"""Тесты библиотеки предустановленных запросов."""

from sql_query_agent.db.explain import prepare_statement
from sql_query_agent.presets import PRESET_QUERIES, preset_by_id


def test_library_has_twelve_queries() -> None:
    """В библиотеке двенадцать запросов."""

    assert len(PRESET_QUERIES) == 12


def test_every_preset_is_a_single_read_query() -> None:
    """Каждый предустановленный запрос проходит проверку ввода."""

    for preset in PRESET_QUERIES:
        assert prepare_statement(preset.sql)


def test_every_preset_has_title_and_note() -> None:
    """У каждого запроса есть подпись и пояснение."""

    for preset in PRESET_QUERIES:
        assert preset.title
        assert preset.note


def test_no_preset_carries_index_ddl() -> None:
    """Описание пресета не содержит команды создания индекса."""

    for preset in PRESET_QUERIES:
        fields = set(type(preset).model_fields)

        assert "index_ddl" not in fields
        assert "create index" not in preset.sql.lower()


def test_no_preset_carries_expected_effect() -> None:
    """Описание пресета не содержит ожидаемого эффекта."""

    for preset in PRESET_QUERIES:
        assert "expected" not in set(type(preset).model_fields)


def test_preset_ids_are_unique() -> None:
    """Идентификаторы запросов уникальны."""

    ids = [preset.id for preset in PRESET_QUERIES]

    assert len(ids) == len(set(ids))


def test_library_covers_different_query_shapes() -> None:
    """Библиотека покрывает разные формы выборки."""

    sql = [preset.sql.lower() for preset in PRESET_QUERIES]

    assert any("count(*)" in text and "group by" not in text for text in sql)
    assert any("order by" in text and "limit" in text for text in sql)
    assert any(" in (" in text for text in sql)
    assert any("join product p" in text for text in sql)
    assert any(text.count("join ") >= 2 for text in sql)
    assert any("group by" in text for text in sql)
    assert any(" over (" in text for text in sql)
    assert any("row_number() over" in text for text in sql)
    assert any("count(*) over" in text for text in sql)


def test_preset_lookup_by_id() -> None:
    """Поиск по идентификатору возвращает нужный запрос."""

    preset = preset_by_id("count-by-product")

    assert preset is not None
    assert "product_id = 42" in preset.sql
    assert preset_by_id("no-such-preset") is None
