"""Тесты схемы базы из внешнего YAML-файла."""

from pathlib import Path

import pytest

from sql_query_agent.config import DEFAULT_SCHEMA_PATH
from sql_query_agent.schema_diagram import (
    DatabaseSchema,
    SchemaError,
    load_schema,
)

DIAGRAM = "+------+\n| sku  |\n+------+\n"


def _write(path: Path, text: str) -> Path:
    """Записать временный файл схемы."""

    path.write_text(text, encoding="utf-8")
    return path


def test_default_schema_file_is_loaded() -> None:
    """Файл схемы по умолчанию читается загрузчиком без ошибок."""

    schema = load_schema(DEFAULT_SCHEMA_PATH)

    assert schema is not None
    assert schema.diagram
    assert "sku" in schema.diagram
    assert "brand" in schema.diagram
    assert schema.caption


def test_diagram_keeps_tables_links_and_row_counts() -> None:
    """Рисунок содержит таблицы, связи и число строк."""

    schema = load_schema(DEFAULT_SCHEMA_PATH)

    assert schema is not None
    for table in ("brand", "product", "sku", "product_size", "product_color"):
        assert table in schema.diagram
    assert "->" in schema.diagram
    assert "PK" in schema.diagram
    assert "100 000" in schema.diagram


def test_schema_is_loaded_from_file(tmp_path: Path) -> None:
    """Схема собирается из подписи и рисунка файла."""

    path = _write(
        tmp_path / "schema.yaml",
        "caption: Подпись\n\ndiagram: |\n  +--+\n  | x|\n  +--+\n",
    )

    schema = load_schema(path)

    assert schema == DatabaseSchema(caption="Подпись", diagram="+--+\n| x|\n+--+")


def test_caption_is_optional(tmp_path: Path) -> None:
    """Подпись необязательна: без неё схема всё равно читается."""

    path = _write(tmp_path / "schema.yaml", "diagram: |\n  +--+\n  | x|\n  +--+\n")

    schema = load_schema(path)

    assert schema is not None
    assert schema.caption == ""


def test_missing_file_gives_no_schema(tmp_path: Path) -> None:
    """Отсутствующий файл — «схемы нет» без исключения."""

    assert load_schema(tmp_path / "no-such-file.yaml") is None


def test_empty_file_gives_no_schema(tmp_path: Path) -> None:
    """Пустой файл — «схемы нет» без исключения."""

    path = _write(tmp_path / "schema.yaml", "")

    assert load_schema(path) is None


def test_file_without_diagram_is_an_error(tmp_path: Path) -> None:
    """Файл без рисунка даёт понятную ошибку загрузки."""

    path = _write(tmp_path / "schema.yaml", "caption: Только подпись\n")

    with pytest.raises(SchemaError) as error:
        load_schema(path)

    assert "diagram" in str(error.value)


def test_blank_diagram_is_an_error(tmp_path: Path) -> None:
    """Пустой рисунок считается ошибкой, а не «схемы нет»."""

    path = _write(tmp_path / "schema.yaml", "diagram: |\n\n")

    with pytest.raises(SchemaError):
        load_schema(path)


def test_non_mapping_document_is_an_error(tmp_path: Path) -> None:
    """Документ не в форме словаря даёт ошибку загрузки."""

    path = _write(tmp_path / "schema.yaml", "- строка\n")

    with pytest.raises(SchemaError):
        load_schema(path)


def test_path_from_settings_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь к файлу берётся из настроек `SQA_SCHEMA__PATH`."""

    path = _write(tmp_path / "schema.yaml", "diagram: |\n  +--+\n  | x|\n  +--+\n")
    monkeypatch.setenv("SQA_SCHEMA__PATH", str(path))

    from sql_query_agent.config import reset_settings_cache

    reset_settings_cache()

    schema = load_schema()

    assert schema is not None
    assert "| x|" in schema.diagram


__all__: list[str] = []
