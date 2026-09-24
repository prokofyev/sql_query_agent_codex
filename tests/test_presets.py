"""Тесты библиотеки предустановленных запросов из внешнего YAML-файла."""

from pathlib import Path

import pytest

from sql_query_agent.config import DEFAULT_PRESETS_PATH
from sql_query_agent.db.explain import prepare_statement
from sql_query_agent.presets import (
    PresetQuery,
    PresetsError,
    load_presets,
    preset_by_id,
)


@pytest.fixture
def library() -> list[PresetQuery]:
    """Библиотека из файла по умолчанию."""

    return load_presets(DEFAULT_PRESETS_PATH)


def _write(path: Path, text: str) -> Path:
    """Записать временный файл пресетов."""

    path.write_text(text, encoding="utf-8")
    return path


def test_default_library_has_twelve_queries(library: list[PresetQuery]) -> None:
    """В библиотеке двенадцать запросов."""

    assert len(library) == 12


def test_every_preset_is_a_single_read_query(library: list[PresetQuery]) -> None:
    """Каждый предустановленный запрос проходит проверку ввода."""

    for preset in library:
        assert prepare_statement(preset.sql)


def test_every_preset_has_title_and_note(library: list[PresetQuery]) -> None:
    """У каждого запроса есть подпись и пояснение."""

    for preset in library:
        assert preset.title
        assert preset.note


def test_no_preset_carries_index_ddl(library: list[PresetQuery]) -> None:
    """Описание пресета не содержит команды создания индекса."""

    for preset in library:
        fields = set(type(preset).model_fields)

        assert "index_ddl" not in fields
        assert "create index" not in preset.sql.lower()


def test_no_preset_carries_expected_effect(library: list[PresetQuery]) -> None:
    """Описание пресета не содержит ожидаемого эффекта."""

    for preset in library:
        assert "expected" not in set(type(preset).model_fields)


def test_preset_ids_are_unique(library: list[PresetQuery]) -> None:
    """Идентификаторы запросов уникальны."""

    ids = [preset.id for preset in library]

    assert len(ids) == len(set(ids))


def test_library_covers_different_query_shapes(library: list[PresetQuery]) -> None:
    """Библиотека покрывает разные формы выборки, включая диапазон."""

    sql = [preset.sql.lower() for preset in library]

    assert any("count(*)" in text for text in sql)
    assert any("order by" in text and "limit" in text for text in sql)
    assert any(" in (" in text for text in sql)
    assert any("join product p" in text for text in sql)
    assert any(text.count("join ") >= 2 for text in sql)
    assert any("group by" in text for text in sql)
    assert any("row_number() over" in text for text in sql)
    assert any("between" in text or " >= now()" in text for text in sql)


def test_library_uses_different_index_levers(library: list[PresetQuery]) -> None:
    """Библиотека содержит запросы, ускоряемые разными колонками."""

    sql = " ".join(preset.sql.lower() for preset in library)

    assert "product_id" in sql
    assert "product_size_id" in sql
    assert "added_at" in sql
    assert "brand_id" in sql


def test_preset_lookup_by_id(library: list[PresetQuery]) -> None:
    """Поиск по идентификатору возвращает нужный запрос."""

    preset = preset_by_id("count-by-product")

    assert preset is not None
    assert "product_id = 42" in preset.sql
    assert preset_by_id("no-such-preset") is None


def test_missing_file_gives_empty_library(tmp_path: Path) -> None:
    """Отсутствующий файл — пустая библиотека без исключения."""

    assert load_presets(tmp_path / "no-such-file.yaml") == []


def test_empty_file_gives_empty_library(tmp_path: Path) -> None:
    """Пустой файл — пустая библиотека без исключения."""

    path = _write(tmp_path / "presets.yaml", "")

    assert load_presets(path) == []


def test_file_without_records_gives_empty_library(tmp_path: Path) -> None:
    """Файл с пустым списком — пустая библиотека."""

    path = _write(tmp_path / "presets.yaml", "[]\n")

    assert load_presets(path) == []


def test_records_are_loaded_from_file(tmp_path: Path) -> None:
    """Записи читаются из файла с сохранением порядка и пояснений."""

    path = _write(
        tmp_path / "presets.yaml",
        "".join(
            [
                "- id: first\n  title: Первый\n  sql: select 1\n",
                "- id: second\n  title: Второй\n  sql: select 2\n  note: Пояснение\n",
            ]
        ),
    )

    presets = load_presets(path)

    assert [preset.id for preset in presets] == ["first", "second"]
    assert presets[1].note == "Пояснение"


@pytest.mark.parametrize("missing", ["id", "title", "sql"])
def test_record_without_required_field_is_an_error(tmp_path: Path, missing: str) -> None:
    """Запись без обязательного поля даёт понятную ошибку загрузки."""

    fields = {"id": "x", "title": "Заголовок", "sql": "select 1"}
    del fields[missing]
    body = "".join(f"  {key}: {value}\n" for key, value in fields.items())
    path = _write(tmp_path / "presets.yaml", "-" + body[1:])

    with pytest.raises(PresetsError) as error:
        load_presets(path)

    assert missing in str(error.value)
    assert "Пресет №1" in str(error.value)


def test_duplicate_ids_are_an_error(tmp_path: Path) -> None:
    """Дублирующиеся идентификаторы дают ошибку загрузки."""

    body = "- id: same\n  title: Раз\n  sql: select 1\n- id: same\n  title: Два\n  sql: select 2\n"
    path = _write(tmp_path / "presets.yaml", body)

    with pytest.raises(PresetsError):
        load_presets(path)


def test_non_list_document_is_an_error(tmp_path: Path) -> None:
    """Документ не в форме списка даёт ошибку загрузки."""

    path = _write(tmp_path / "presets.yaml", "id: scalar\n")

    with pytest.raises(PresetsError):
        load_presets(path)


def test_path_from_settings_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь к файлу берётся из настроек `SQA_PRESETS__PATH`."""

    path = _write(tmp_path / "presets.yaml", "- id: x\n  title: X\n  sql: select 1\n")
    monkeypatch.setenv("SQA_PRESETS__PATH", str(path))

    from sql_query_agent.config import reset_settings_cache

    reset_settings_cache()

    assert [preset.id for preset in load_presets()] == ["x"]


__all__: list[str] = []
