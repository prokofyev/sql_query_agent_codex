"""Тесты библиотеки предустановленных запросов."""

from sql_query_agent.db.explain import prepare_statement
from sql_query_agent.presets import (
    PRESET_QUERIES,
    ExpectedEffect,
    no_speedup_presets,
    preset_by_id,
    speedup_presets,
)


def test_library_has_five_queries() -> None:
    """В библиотеке пять запросов."""

    assert len(PRESET_QUERIES) == 5


def test_library_has_three_speedup_queries() -> None:
    """Не меньше трёх запросов ускоряются индексом."""

    assert len(speedup_presets()) >= 3


def test_library_has_two_no_speedup_queries() -> None:
    """Не меньше двух запросов не ускоряются индексом."""

    assert len(no_speedup_presets()) >= 2


def test_no_speedup_reasons_are_different() -> None:
    """Причины отсутствия ускорения различаются: селективность и форма плана."""

    reasons = {preset.expected for preset in no_speedup_presets()}

    assert ExpectedEffect.NO_SPEEDUP_SELECTIVITY in reasons
    assert ExpectedEffect.NO_SPEEDUP_PLAN in reasons


def test_every_preset_is_a_single_read_query() -> None:
    """Каждый предустановленный запрос проходит проверку ввода."""

    for preset in PRESET_QUERIES:
        assert prepare_statement(preset.sql)


def test_every_preset_has_index_and_note() -> None:
    """У каждого запроса есть команда индекса и пояснение."""

    for preset in PRESET_QUERIES:
        assert preset.index_ddl.upper().startswith("CREATE INDEX")
        assert preset.note
        assert preset.title


def test_preset_ids_are_unique() -> None:
    """Идентификаторы запросов уникальны."""

    ids = [preset.id for preset in PRESET_QUERIES]

    assert len(ids) == len(set(ids))


def test_preset_lookup_by_id() -> None:
    """Поиск по идентификатору возвращает нужный запрос."""

    preset = preset_by_id("point-lookup")

    assert preset is not None
    assert preset.expected is ExpectedEffect.SPEEDUP
    assert preset_by_id("no-such-preset") is None
