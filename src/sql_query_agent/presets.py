"""Библиотека предустановленных запросов из внешнего YAML-файла.

Библиотека — свойство конкретной демонстрационной базы, а не логики агента,
поэтому она живёт в файле, а не в коде. Путь задаётся настройкой
`SQA_PRESETS__PATH`, значение по умолчанию — `resources/presets.yaml`.

Отсутствующий или пустой файл даёт пустую библиотеку, и это не ошибка: агент
пригоден для любой базы, где демонстрационных пресетов просто нет.
Повреждённая запись (нет `id`, `title` или `sql`) — ошибка загрузки: такой
пресет пользователю показывать нельзя.

Команда создания индекса в описании пресета не хранится: индекс предлагает
модель по плану выполнения. Случай «индекс не помогает» остаётся доступен
через свободный ввод.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from sql_query_agent.config import DEFAULT_PRESETS_PATH, get_settings
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

REQUIRED_FIELDS = ("id", "title", "sql")


class PresetsError(ValueError):
    """Библиотеку пресетов не удалось загрузить."""


class PresetQuery(BaseModel):
    """Предустановленный запрос: подпись, текст и пояснение."""

    id: str
    title: str
    sql: str
    note: str = Field(default="", description="Чем интересен этот запрос")


def _record_error(index: int, reason: str) -> PresetsError:
    """Понятная ошибка по повреждённой записи."""

    return PresetsError(f"Пресет №{index + 1} повреждён: {reason}")


def _build_preset(index: int, record: Any) -> PresetQuery:
    """Собрать пресет из словаря с проверкой обязательных полей."""

    if not isinstance(record, dict):
        raise _record_error(index, "ожидался словарь с полями id, title, sql")

    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = record.get(field)
        if value is None or not str(value).strip():
            raise _record_error(index, f"не задано обязательное поле `{field}`")
        values[field] = str(value)

    note = record.get("note")
    values["note"] = "" if note is None else str(note)
    return PresetQuery(**values)


def load_presets(path: str | Path | None = None) -> list[PresetQuery]:
    """Загрузить библиотеку предустановленных запросов из YAML-файла.

    Отсутствующий или пустой файл даёт пустой список. Повреждённая запись
    поднимает `PresetsError` с указанием номера записи и причины.
    """

    location = Path(path if path is not None else get_settings().presets.path)
    if not location.is_file():
        logger.info("файл пресетов не найден", path=str(location))
        return []

    raw = yaml.safe_load(location.read_text(encoding="utf-8"))
    if raw is None:
        logger.info("файл пресетов пуст", path=str(location))
        return []
    if not isinstance(raw, list):
        raise PresetsError(
            f"Файл пресетов {location} должен содержать список записей"
        )

    presets = [_build_preset(index, record) for index, record in enumerate(raw)]
    seen: set[str] = set()
    for preset in presets:
        if preset.id in seen:
            raise PresetsError(f"Пресет с идентификатором `{preset.id}` встречается дважды")
        seen.add(preset.id)
    return presets


def preset_by_id(preset_id: str, path: str | Path | None = None) -> PresetQuery | None:
    """Найти предустановленный запрос по идентификатору."""

    for preset in load_presets(path):
        if preset.id == preset_id:
            return preset
    return None


__all__ = [
    "DEFAULT_PRESETS_PATH",
    "REQUIRED_FIELDS",
    "PresetQuery",
    "PresetsError",
    "load_presets",
    "preset_by_id",
]
