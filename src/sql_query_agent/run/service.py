"""Служба прогонов: запуск, решения, отчёт и журнал.

Служба связывает три вещи, которые должны срабатывать в одном месте:
граф агента (сессия), метрики процесса и audit-журнал. Иначе метрика или
запись в журнал легко теряются на одной из ветвей обработки.
"""

from typing import Any

from sql_query_agent.logging_setup import get_logger
from sql_query_agent.observability.metrics import RunMetrics
from sql_query_agent.observability.run_metrics import observe_run
from sql_query_agent.run.journal import RunJournal, record_from_report
from sql_query_agent.run.report import RunReport
from sql_query_agent.run.session import SessionRunner

logger = get_logger(__name__)


class RunService:
    """Прогоны агента с учётом метрик и записью в журнал."""

    def __init__(
        self,
        runner: SessionRunner,
        *,
        journal: RunJournal | None = None,
        metrics: RunMetrics | None = None,
    ) -> None:
        self._runner = runner
        self._journal = journal
        self._metrics = metrics
        self._recorded: set[str] = set()

    @property
    def runner(self) -> SessionRunner:
        """Доступ к сессиям: нужен для истории процесса."""

        return self._runner

    async def start(self, sql: str, *, thread_id: str | None = None) -> RunReport:
        """Запустить обработку запроса."""

        report = await self._runner.start(sql, thread_id=thread_id)
        return await self._finalize(report)

    async def decide(self, thread_id: str, *, accepted: bool, step: str) -> RunReport:
        """Передать решение пользователя и продолжить прогон."""

        report = await self._runner.decide(thread_id, accepted=accepted, step=step)
        return await self._finalize(report)

    async def report(self, thread_id: str) -> RunReport:
        """Отчёт по текущему состоянию сессии."""

        return await self._runner.report(thread_id)

    async def history(self, limit: int = 20) -> list[RunReport]:
        """История прогонов процесса."""

        return await self._runner.history(limit)

    async def journal_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Записи audit-журнала, если журнал подключён."""

        if self._journal is None:
            return []
        try:
            records = await self._journal.history(limit)
        except Exception as error:  # noqa: BLE001 - интерфейс не должен падать
            logger.warning("чтение журнала не удалось", error=str(error))
            return []
        return [entry.model_dump(mode="json") for entry in records]

    async def _finalize(self, report: RunReport) -> RunReport:
        """Отразить итог прогона в метриках и журнале.

        Прогон, ожидающий решения, ещё не итог: его нельзя ни считать в
        метриках, ни записывать в журнал — иначе один прогон дал бы две записи.
        """

        if report.awaiting_decision:
            return report

        if report.thread_id in self._recorded:
            return report
        self._recorded.add(report.thread_id)

        if self._metrics is not None:
            observe_run(report, self._metrics)

        if self._journal is not None:
            await self._write(report)
        return report

    async def _write(self, report: RunReport) -> None:
        """Записать прогон в журнал, не прерывая обработку при сбое.

        Результат прогона важнее записи о нём: пользователь уже получил
        ответ, поэтому ошибка журнала остаётся в логах.
        """

        try:
            await self._journal.record(record_from_report(report))
        except Exception as error:  # noqa: BLE001 - сбой журнала не должен ломать прогон
            logger.warning("запись в журнал не удалась", error=str(error))
            return
        logger.info("прогон записан в журнал", thread_id=report.thread_id)


__all__ = ["RunService"]
