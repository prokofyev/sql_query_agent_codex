"""Состояние графа агента.

LangGraph передаёт данные между узлами только через состояние: узел
возвращает часть ключей, следующий их читает. Здесь же лежит и то, что нужно
снаружи в точке остановки: из состояния собирается отчёт для API, журнала и
интерфейса, а на его ключах держатся маршрутизаторы графа.

Отдельная причина хранить дорогие результаты — `interrupt()`: при
возобновлении LangGraph перезапускает узел с `interrupt()` целиком. Поэтому
вызовы модели и замеры живут в узлах без `interrupt()`, а их результат
передаётся подтверждающему узлу через состояние (см. `nodes.py`).
"""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Состояние одного прогона."""

    original_sql: str
    current_sql: str

    schema_result: dict[str, Any] | None
    schema_checked: bool
    warnings: list[str]

    fixed_sql: str | None
    fix_replacements: list[dict[str, Any]]
    fix_applied: bool
    fix_declined: bool
    unfixable_message: str | None

    proposal: dict[str, Any] | None
    index_declined: bool

    before_stats: dict[str, Any] | None
    before_plan_nodes: list[str]
    apply_result: dict[str, Any] | None

    status: str
