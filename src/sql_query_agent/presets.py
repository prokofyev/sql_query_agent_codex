"""Библиотека предустановленных запросов.

Набор подобран по реальным замерам на демо-базе `products`: три запроса
ускоряются индексом, два — нет, причём по разным причинам. Второй случай
важен не меньше первого: агент должен уметь сказать «индекс не поможет», а не
предлагать индекс на каждый запрос.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ExpectedEffect(StrEnum):
    """Ожидаемый эффект от индекса."""

    SPEEDUP = "speedup"
    NO_SPEEDUP_SELECTIVITY = "no_speedup_low_selectivity"
    NO_SPEEDUP_PLAN = "no_speedup_plan_shape"


class PresetQuery(BaseModel):
    """Предустановленный запрос с ожидаемым эффектом."""

    id: str
    title: str
    sql: str
    index_ddl: str
    expected: ExpectedEffect
    note: str = Field(default="", description="Почему ожидается именно такой эффект")


FOREIGN_KEY_INDEX = "CREATE INDEX demo_sku_product_id_idx ON sku (product_id)"

PRESET_QUERIES: tuple[PresetQuery, ...] = (
    PresetQuery(
        id="point-lookup",
        title="Точечная выборка по внешнему ключу",
        sql="select * from sku where product_id = 42",
        index_ddl=FOREIGN_KEY_INDEX,
        expected=ExpectedEffect.SPEEDUP,
        note=(
            "Индекс по внешнему ключу заменяет последовательное чтение таблицы "
            "выборкой по индексу"
        ),
    ),
    PresetQuery(
        id="join-by-brand",
        title="Соединение с фильтром по бренду",
        sql=(
            "select s.sku_id from sku s "
            "join product p on p.product_id = s.product_id "
            "where p.brand_id = 7"
        ),
        index_ddl=FOREIGN_KEY_INDEX,
        expected=ExpectedEffect.SPEEDUP,
        note="Планировщик переходит от хеш-соединения к вложенному циклу с выборкой по индексу",
    ),
    PresetQuery(
        id="count-by-brands",
        title="Подсчёт по группе брендов",
        sql=(
            "select count(*) from sku s "
            "join product p on p.product_id = s.product_id "
            "where p.brand_id between 1 and 20"
        ),
        index_ddl=FOREIGN_KEY_INDEX,
        expected=ExpectedEffect.SPEEDUP,
        note="Ускорение умеренное: часть работы переходит в чтение только по индексу",
    ),
    PresetQuery(
        id="low-selectivity",
        title="Фильтр по низкоселективной колонке",
        sql="select * from sku where product_color_id = 1",
        index_ddl="CREATE INDEX demo_sku_color_idx ON sku (product_color_id)",
        expected=ExpectedEffect.NO_SPEEDUP_SELECTIVITY,
        note=(
            "В колонке всего два значения: индекс отбирает половину таблицы, "
            "и планировщик его игнорирует"
        ),
    ),
    PresetQuery(
        id="aggregate-all",
        title="Агрегат по трём таблицам",
        sql=(
            "select p.product_name, count(*) from sku s "
            "join product p on p.product_id = s.product_id "
            "join brand b on b.brand_id = p.brand_id "
            "group by 1 order by 2 desc limit 10"
        ),
        index_ddl=FOREIGN_KEY_INDEX,
        expected=ExpectedEffect.NO_SPEEDUP_PLAN,
        note="Узкое место в соединении и агрегации, а не в поиске строк по ключу",
    ),
)


def preset_by_id(preset_id: str) -> PresetQuery | None:
    """Найти предустановленный запрос по идентификатору."""

    for preset in PRESET_QUERIES:
        if preset.id == preset_id:
            return preset
    return None


def speedup_presets() -> list[PresetQuery]:
    """Запросы, которые индекс ускоряет."""

    return [p for p in PRESET_QUERIES if p.expected is ExpectedEffect.SPEEDUP]


def no_speedup_presets() -> list[PresetQuery]:
    """Запросы, которые индекс не ускоряет."""

    return [
        p
        for p in PRESET_QUERIES
        if p.expected
        in (ExpectedEffect.NO_SPEEDUP_SELECTIVITY, ExpectedEffect.NO_SPEEDUP_PLAN)
    ]
