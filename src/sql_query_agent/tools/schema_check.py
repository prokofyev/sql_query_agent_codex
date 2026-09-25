"""Проверка имён таблиц и колонок со подбором похожих названий.

Инструмент детерминированный: он не вызывает модель. Модель передаёт ему
таблицы, алиасы и колонки, извлечённые из текста запроса, а инструмент сам
решает, какой таблице принадлежит колонка, и возвращает ненайденные имена с
кандидатами на замену и неоднозначные колонки.
"""

from rapidfuzz import fuzz, process

from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.domain import (
    NameCandidate,
    SchemaCheckResult,
    SchemaEntities,
    UnknownName,
    UnknownNameKind,
)


def unfixable_names(unknown: list[UnknownName]) -> list[UnknownName]:
    """Ненайденные имена, для которых не нашлось ни одного кандидата."""

    return [item for item in unknown if not item.is_fixable]


def can_fix_all(unknown: list[UnknownName]) -> bool:
    """Есть ли чем исправить запрос: кандидаты есть у всех ненайденных имён."""

    return bool(unknown) and not unfixable_names(unknown)


def describe_unfixable(unknown: list[UnknownName]) -> str:
    """Сообщение о ненайденных именах, которые нечем заменить.

    Имена берутся из результата проверки, то есть ровно из запроса: текст
    сообщения не придумывает ничего своего.
    """

    missing = unfixable_names(unknown)
    if not missing:
        return ""

    lines: list[str] = []
    for item in missing:
        if item.kind is UnknownNameKind.TABLE:
            lines.append(f"таблица «{item.name}» не найдена")
        else:
            lines.append(_missing_column_message(item))
    lines.append("Подходящих замен нет: исправьте запрос вручную.")
    return "\n".join(lines)


def _missing_column_message(item: UnknownName) -> str:
    """Строка о ненайденной колонке с перечислением таблиц, где её искали."""

    searched = item.searched_tables or ([item.table] if item.table else [])
    if len(searched) == 1:
        return f"колонка «{item.name}» не найдена в таблице «{searched[0]}»"
    if searched:
        rendered = ", ".join(f"«{table}»" for table in searched)
        return f"колонка «{item.name}» не найдена в таблицах {rendered}"
    return f"колонка «{item.name}» не найдена"


def suggest_names(
    value: str,
    variants: list[str],
    *,
    threshold: float,
    limit: int,
) -> list[NameCandidate]:
    """Подобрать похожие имена среди существующих.

    Схожесть считается по `token_set_ratio`: он устойчив к перестановке
    частей имени, что часто встречается в опечатках вида `colr` и `color`.
    """

    if not variants:
        return []

    matches = process.extract(
        value,
        variants,
        scorer=fuzz.token_set_ratio,
        limit=limit,
    )
    return [
        NameCandidate(name=name, score=float(score))
        for name, score, _index in matches
        if score >= threshold
    ]


def check_entities(
    catalog: SchemaCatalog,
    entities: SchemaEntities,
    *,
    threshold: float,
    suggestion_limit: int,
) -> SchemaCheckResult:
    """Сверить имена со схемой и подобрать замены для ненайденных.

    Принадлежность колонки таблице определяется здесь, а не моделью: сначала
    по квалификатору, затем по единственной таблице запроса, в которой такая
    колонка есть. Совпадение в нескольких таблицах запроса — неоднозначность,
    которую исправляет квалификатор.
    """

    unknown: list[UnknownName] = []
    ambiguous_names: set[str] = set()
    unknown_tables: list[str] = []
    checked_tables = 0
    checked_columns = 0
    known_table_names = catalog.table_names()
    alias_map = _alias_map(entities.aliases)
    missing_candidates: dict[str, list[NameCandidate]] = {}

    query_tables: list[str] = []
    for table in entities.tables:
        checked_tables += 1
        if catalog.has_table(table):
            query_tables.append(table)
            continue
        unknown_tables.append(table)
        candidates = suggest_names(
            table,
            known_table_names,
            threshold=threshold,
            limit=suggestion_limit,
        )
        missing_candidates[table] = candidates
        unknown.append(
            UnknownName(
                kind=UnknownNameKind.TABLE,
                table=table,
                name=table,
                candidates=candidates,
            )
        )

    pool_tables = _pool_tables(catalog, query_tables, missing_candidates)
    pool_columns = _columns_of(catalog, pool_tables)

    for raw in entities.columns:
        qualifier, name = split_qualified(raw)
        if qualifier is not None:
            checked_columns += 1
            resolved = alias_map.get(qualifier, qualifier)
            if resolved not in missing_candidates and not catalog.has_table(resolved):
                # Квалификатор не назван среди таблиц запроса и не существует
                # в схеме: проверяем его как ещё одну отсутствующую таблицу.
                unknown_tables.append(resolved)
                candidates = suggest_names(
                    resolved,
                    known_table_names,
                    threshold=threshold,
                    limit=suggestion_limit,
                )
                missing_candidates[resolved] = candidates
                unknown.append(
                    UnknownName(
                        kind=UnknownNameKind.TABLE,
                        table=resolved,
                        name=resolved,
                        candidates=candidates,
                    )
                )
            candidates_of = _candidate_tables(catalog, resolved, query_tables, missing_candidates)
            known_columns = _columns_of(catalog, candidates_of)
            if name in known_columns:
                continue
            unknown.append(
                UnknownName(
                    kind=UnknownNameKind.COLUMN,
                    table=resolved,
                    name=name,
                    candidates=suggest_names(
                        name,
                        sorted(known_columns or pool_columns),
                        threshold=threshold,
                        limit=suggestion_limit,
                    ),
                    searched_tables=[resolved],
                )
            )
            continue

        # Проверять колонки не по чему: в запросе нет ни одной таблицы, которую
        # можно найти в схеме, поэтому колонки ненайденными не объявляются.
        if not pool_columns:
            continue

        checked_columns += 1
        owners = _owners(catalog, query_tables, name)
        if len(owners) > 1:
            if name not in ambiguous_names:
                ambiguous_names.add(name)
                unknown.append(
                    UnknownName(
                        kind=UnknownNameKind.COLUMN,
                        table="",
                        name=name,
                        candidates=[
                            NameCandidate(name=f"{table}.{name}", score=100.0)
                            for table in owners
                        ],
                        searched_tables=list(owners),
                        ambiguous=True,
                    )
                )
            continue
        if owners:
            continue
        if name in pool_columns:
            # Колонка найдена среди колонок таблиц-кандидатов отсутствующей
            # таблицы: проверять её как ненайденную не нужно.
            continue
        searched = list(pool_tables)
        unknown.append(
            UnknownName(
                kind=UnknownNameKind.COLUMN,
                table=searched[0] if len(searched) == 1 else "",
                name=name,
                candidates=suggest_names(
                    name,
                    sorted(pool_columns),
                    threshold=threshold,
                    limit=suggestion_limit,
                ),
                searched_tables=searched,
            )
        )

    return SchemaCheckResult(
        ok=not unknown,
        checked_tables=checked_tables,
        checked_columns=checked_columns,
        unknown=unknown,
        unknown_tables=unknown_tables,
    )


def split_qualified(column: str) -> tuple[str | None, str]:
    """Разделить запись колонки на квалификатор и имя.

    `product.brand_id` даёт пару `product` и `brand_id`, а `brand_name` —
    отсутствие квалификатора. Кавычки по краям отбрасываются: в запросе имя
    может быть записано как `"brand"."brand_id"`.
    """

    text = column.strip()
    if "." not in text:
        return None, _unquote(text)
    qualifier, _, name = text.rpartition(".")
    qualifier = _unquote(qualifier.strip())
    name = _unquote(name.strip())
    if qualifier and name:
        return qualifier, name
    return None, _unquote(text)


def _unquote(value: str) -> str:
    """Убрать обрамляющие двойные кавычки, если они есть."""

    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _alias_map(aliases: list[str]) -> dict[str, str]:
    """Разобрать пары «алиас=настоящая таблица» в словарь."""

    pairs: dict[str, str] = {}
    for item in aliases:
        alias, separator, table = item.partition("=")
        if not separator:
            continue
        alias = _unquote(alias.strip())
        table = _unquote(table.strip())
        if alias and table:
            pairs[alias] = table
    return pairs


def _pool_tables(
    catalog: SchemaCatalog,
    query_tables: list[str],
    missing_candidates: dict[str, list[NameCandidate]],
) -> list[str]:
    """Таблицы, колонки которых служат пулом для проверки и подбора замен."""

    tables = list(query_tables)
    for candidates in missing_candidates.values():
        for candidate in candidates:
            if catalog.has_table(candidate.name) and candidate.name not in tables:
                tables.append(candidate.name)
    return tables


def _owners(catalog: SchemaCatalog, tables: list[str], column: str) -> list[str]:
    """Таблицы запроса, в которых есть колонка, в порядке появления в запросе."""

    owning = set(catalog.tables_with_column(column))
    return [table for table in tables if table in owning]


def _candidate_tables(
    catalog: SchemaCatalog,
    table: str,
    query_tables: list[str],
    missing_candidates: dict[str, list[NameCandidate]],
) -> list[str]:
    """Таблицы, по колонкам которых проверяется колонка заданной таблицы."""

    if catalog.has_table(table):
        return [table]
    return [candidate.name for candidate in missing_candidates.get(table, [])]


def _columns_of(catalog: SchemaCatalog, tables: list[str]) -> set[str]:
    """Все колонки перечисленных таблиц."""

    columns: set[str] = set()
    for table in tables:
        columns.update(catalog.column_names(table))
    return columns
