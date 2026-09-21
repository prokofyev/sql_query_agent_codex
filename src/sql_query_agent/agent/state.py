"""Состояние графа агента.

Ключи-кэши существуют из-за особенности LangGraph: при возобновлении после
`interrupt()` узел выполняется заново целиком. Без кэша каждый ответ
пользователя стоил бы нового обращения к модели и нового замера.
"""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Состояние одного прогона."""

    original_sql: str
    current_sql: str
    thread_id: str

    entities: list[dict[str, Any]]
    schema_result: dict[str, Any] | None
    schema_checked: bool
    warnings: list[str]

    fixed_sql: str | None
    fix_applied: bool
    fix_declined: bool

    proposal: dict[str, Any] | None
    index_declined: bool

    before_stats: dict[str, Any] | None
    before_plan_nodes: list[str]
    apply_result: dict[str, Any] | None

    decision: dict[str, Any] | None
    status: str
