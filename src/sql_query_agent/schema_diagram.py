"""Схема базы для интерфейса из внешнего YAML-файла.

Рисунок схемы в псевдографике — свойство конкретной демонстрационной базы, а
не логики агента, поэтому он живёт в файле рядом с пресетами. Путь задаётся
настройкой `SQA_SCHEMA__PATH`, значение по умолчанию — `resources/schema.yaml`.

Отсутствующий или пустой файл означает «схемы нет»: панель в интерфейсе не
показывается, и это не ошибка. Повреждённый файл (нет поля `diagram`) —
ошибка загрузки: частичный рисунок вводит в заблуждение сильнее, чем его
отсутствие.

Число строк у каждой таблицы — статичный снимок в самом рисунке, а не живое
значение из базы. При перезаливке данных или смене базы файл перерисовывают.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel

from sql_query_agent.config import DEFAULT_SCHEMA_PATH, get_settings
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

DIAGRAM_FIELD = "diagram"


class SchemaError(ValueError):
    """Схему базы не удалось загрузить."""


class DatabaseSchema(BaseModel):
    """Схема базы для панели интерфейса: подпись и рисунок в псевдографике."""

    caption: str = ""
    diagram: str


def load_schema(path: str | Path | None = None) -> DatabaseSchema | None:
    """Загрузить схему базы из YAML-файла.

    Отсутствующий или пустой файл даёт `None` («схемы нет»). Повреждённый
    файл поднимает `SchemaError` с понятным текстом.
    """

    location = Path(path if path is not None else get_settings().schema_view.path)
    if not location.is_file():
        logger.info("файл схемы не найден", path=str(location))
        return None

    raw = yaml.safe_load(location.read_text(encoding="utf-8"))
    if raw is None:
        logger.info("файл схемы пуст", path=str(location))
        return None
    if not isinstance(raw, dict):
        raise SchemaError(f"Файл схемы {location} должен содержать словарь с полем `diagram`")

    diagram = raw.get(DIAGRAM_FIELD)
    if diagram is None or not str(diagram).strip():
        raise SchemaError(f"В файле схемы {location} не задано обязательное поле `diagram`")

    caption = raw.get("caption")
    return DatabaseSchema(
        caption="" if caption is None else str(caption),
        diagram=str(diagram).strip("\n"),
    )


__all__ = [
    "DIAGRAM_FIELD",
    "DEFAULT_SCHEMA_PATH",
    "DatabaseSchema",
    "SchemaError",
    "load_schema",
]
