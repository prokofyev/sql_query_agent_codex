"""Библиотека предустановленных запросов.

Двенадцать реалистичных запросов, каждый из которых на демо-базе `products`
ускоряется одним и тем же индексом по `sku.product_id`. Формы запросов разные
(фильтр, сортировка с ограничением, список значений, соединения, агрегат,
оконные функции), но рычаг оптимизации на этой схеме один: только у
`product_id` достаточно различных значений, чтобы индекс окупился.

Команда создания индекса умышленно не хранится в пресете: индекс предлагает
модель по плану выполнения, а не описание пресета. Библиотека показывает, что
агент умеет находить ускорение; случай «индекс не помогает» остаётся доступен
через свободный ввод и требованием `index-optimization`.
"""

from pydantic import BaseModel, Field


class PresetQuery(BaseModel):
    """Предустановленный запрос: подпись, текст и пояснение."""

    id: str
    title: str
    sql: str
    note: str = Field(default="", description="Чем интересен этот запрос")


PRESET_QUERIES: tuple[PresetQuery, ...] = (
    PresetQuery(
        id="count-by-product",
        title="Сколько SKU у товара",
        sql="select count(*) from sku where product_id = 42",
        note="Точечный фильтр по внешнему ключу: индекс заменяет чтение всей таблицы",
    ),
    PresetQuery(
        id="page-by-product",
        title="Страница SKU товара",
        sql=(
            "select sku_id, product_size_id, product_color_id from sku "
            "where product_id = 42 order by sku_id limit 20"
        ),
        note="Выборка страницы с сортировкой: индекс отдаёт строки уже упорядоченными по ключу",
    ),
    PresetQuery(
        id="count-by-products",
        title="Сводка по нескольким товарам",
        sql=(
            "select product_id, count(*) from sku "
            "where product_id in (1, 2, 3, 4, 5, 6, 7, 8) group by 1 order by 2 desc"
        ),
        note="Фильтр по списку значений: индекс обслуживает каждое значение без полного чтения",
    ),
    PresetQuery(
        id="product-size-filter",
        title="Товар и размер",
        sql="select sku_id from sku where product_id = 42 and product_size_id = 2",
        note="Два условия: селективность задаёт ключ, второе условие фильтрует найденные строки",
    ),
    PresetQuery(
        id="size-breakdown",
        title="Раскладка по размерам",
        sql=(
            "select product_size_id, count(*) from sku "
            "where product_id = 42 group by 1 order by 2 desc"
        ),
        note=(
            "Агрегат по отфильтрованному набору: группировка работает "
            "с десятками строк вместо тысяч"
        ),
    ),
    PresetQuery(
        id="join-product-name",
        title="Название товара по SKU",
        sql=(
            "select p.product_name, s.sku_id from sku s "
            "join product p on p.product_id = s.product_id where s.product_id = 42"
        ),
        note=(
            "Соединение с товаром по ключу: планировщик переходит "
            "к вложенному циклу с выборкой по индексу"
        ),
    ),
    PresetQuery(
        id="join-size-breakdown",
        title="Раскладка по размерам через соединение",
        sql=(
            "select ps.product_size_name, count(*) from sku s "
            "join product_size ps on ps.product_size_id = s.product_size_id "
            "where s.product_id = 42 group by 1"
        ),
        note="Соединение и группировка: узкое место в отборе строк снимает индекс по ключу",
    ),
    PresetQuery(
        id="join-color-breakdown",
        title="Раскладка по цветам",
        sql=(
            "select c.product_color_name, count(*) from sku s "
            "join product_color c on c.product_color_id = s.product_color_id "
            "where s.product_id = 42 group by 1"
        ),
        note="Соединение со справочником цвета: индекс сокращает вход соединения до одного товара",
    ),
    PresetQuery(
        id="join-brand-count",
        title="Бренд товара по SKU",
        sql=(
            "select b.brand_name, count(*) from sku s "
            "join product p on p.product_id = s.product_id "
            "join brand b on b.brand_id = p.brand_id "
            "where s.product_id = 42 group by 1"
        ),
        note="Цепочка из трёх таблиц: ускорение даёт отбор строк в самой большой таблице",
    ),
    PresetQuery(
        id="window-row-number",
        title="Нумерация SKU внутри товара",
        sql=(
            "select product_id, sku_id, "
            "row_number() over (partition by product_id order by sku_id) as rn "
            "from sku where product_id in (1, 2, 3, 4, 5, 6, 7, 8) "
            "order by product_id, rn limit 50"
        ),
        note="Оконная нумерация по разделам: индекс уменьшает вход оконной функции",
    ),
    PresetQuery(
        id="window-top-n-per-product",
        title="Первые SKU каждого товара",
        sql=(
            "select product_id, sku_id from ("
            "select product_id, sku_id, "
            "row_number() over (partition by product_id order by sku_id) as rn "
            "from sku where product_id in (1, 2, 3, 4)) t "
            "where rn <= 5 order by product_id, sku_id"
        ),
        note="Топ-N на товар: индекс нужен и для нумерации, и для внешнего отбора строк",
    ),
    PresetQuery(
        id="window-count-over",
        title="Число SKU рядом с каждой строкой",
        sql=(
            "select sku_id, count(*) over (partition by product_id) as cnt "
            "from sku where product_id = 42 order by sku_id limit 20"
        ),
        note="Оконный счётчик: индекс сокращает раздел окна до строк одного товара",
    ),
)


def preset_by_id(preset_id: str) -> PresetQuery | None:
    """Найти предустановленный запрос по идентификатору."""

    for preset in PRESET_QUERIES:
        if preset.id == preset_id:
            return preset
    return None
