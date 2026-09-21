"""Сессии прогонов: запуск обработки и продолжение по решению пользователя.

Сессия — это прогон графа, привязанный к `thread_id`. Обработка может
остановиться на решении пользователя (`interrupt`): тогда прогон ждёт ответа,
а состояние остаётся в checkpoint-хранилище, поэтому перезапуск процесса его
не теряет.
"""

import uuid
from typing import Any

from langgraph.types import Command

from sql_query_agent.logging_setup import get_logger
from sql_query_agent.run.report import RunReport, build_report

logger = get_logger(__name__)


def new_thread_id() -> str:
    """Идентификатор новой сессии."""

    return uuid.uuid4().hex


class SessionNotFoundError(LookupError):
    """Сессия с таким `thread_id` неизвестна."""


class SessionRunner:
    """Запуск графа агента и продолжение его по решениям пользователя."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self._known_sessions: list[str] = []

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        """Конфигурация потока для LangGraph."""

        return {"configurable": {"thread_id": thread_id}}

    async def _read(
        self, thread_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Состояние сессии и точки остановки.

        Пустое состояние означает неизвестную сессию: LangGraph возвращает
        пустой снимок и для потока, которого никогда не было.
        """

        snapshot = await self._graph.aget_state(self._config(thread_id))
        values = dict(snapshot.values or {})
        if not values:
            raise SessionNotFoundError(f"сессия {thread_id} не найдена")
        interrupts = [
            dict(item.value)
            for item in (snapshot.interrupts or [])
            if isinstance(item.value, dict)
        ]
        return values, interrupts

    async def start(self, sql: str, *, thread_id: str | None = None) -> RunReport:
        """Создать сессию и запустить обработку запроса."""

        session_id = thread_id or new_thread_id()
        await self._graph.ainvoke(
            {"original_sql": sql, "current_sql": sql},
            self._config(session_id),
        )
        if session_id not in self._known_sessions:
            self._known_sessions.append(session_id)
        logger.info("прогон запущен", thread_id=session_id)
        return await self.report(session_id)

    async def decide(self, thread_id: str, *, accepted: bool) -> RunReport:
        """Передать решение пользователя и продолжить прогон."""

        await self._read(thread_id)
        await self._graph.ainvoke(
            Command(resume={"accepted": accepted}),
            self._config(thread_id),
        )
        logger.info("решение принято", thread_id=thread_id, accepted=accepted)
        return await self.report(thread_id)

    async def report(self, thread_id: str) -> RunReport:
        """Собрать отчёт по текущему состоянию сессии."""

        values, interrupts = await self._read(thread_id)
        return build_report(thread_id, values, interrupts)

    async def history(self, limit: int = 20) -> list[RunReport]:
        """Последние прогоны процесса: свежие первыми."""

        reports: list[RunReport] = []
        for thread_id in reversed(self._known_sessions[-limit:]):
            reports.append(await self.report(thread_id))
        return reports


__all__ = ["SessionNotFoundError", "SessionRunner", "new_thread_id"]
