"""Проверка имён таблиц и колонок со подбором похожих названий.

Инструмент детерминированный: он не вызывает модель. Модель вызывает его,
передавая список имён, извлечённых из текста запроса, и получает обратно
ненайденные имена с кандидатами на замену.
"""

from rapidfuzz import fuzz, process

from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.domain import (
    ColumnNames,
    NameCandidate,
    SchemaCheckResult,
    UnknownName,
    UnknownNameKind,
)


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
    entities: list[ColumnNames],
    *,
    threshold: float,
    suggestion_limit: int,
) -> SchemaCheckResult:
    """Сверить имена со схемой и подобрать замены для ненайденных."""

    unknown: list[UnknownName] = []
    unknown_tables: list[str] = []
    checked_tables = 0
    checked_columns = 0
    known_table_names = catalog.table_names()

    for entity in entities:
        checked_tables += 1
        if catalog.has_table(entity.table):
            checked_columns += len(entity.columns)
            for column in entity.columns:
                if catalog.has_column(entity.table, column):
                    continue
                unknown.append(
                    UnknownName(
                        kind=UnknownNameKind.COLUMN,
                        table=entity.table,
                        name=column,
                        candidates=suggest_names(
                            column,
                            catalog.column_names(entity.table),
                            threshold=threshold,
                            limit=suggestion_limit,
                        ),
                    )
                )
            continue

        unknown_tables.append(entity.table)
        unknown.append(
            UnknownName(
                kind=UnknownNameKind.TABLE,
                table=entity.table,
                name=entity.table,
                candidates=suggest_names(
                    entity.table,
                    known_table_names,
                    threshold=threshold,
                    limit=suggestion_limit,
                ),
            )
        )

    return SchemaCheckResult(
        ok=not unknown,
        checked_tables=checked_tables,
        checked_columns=checked_columns,
        unknown=unknown,
        unknown_tables=unknown_tables,
    )
