"""Контейнер зависимостей HTTP-слоя.

Сборка зависимостей отделена от приложения: тесты подставляют граф с
подставной моделью и замером, а рабочее приложение — реальные пул, модель и
журнал.
"""

from dataclasses import dataclass, field
from typing import Any

from sql_query_agent.config import Settings, get_settings
from sql_query_agent.observability.metrics import RunMetrics, current_metrics
from sql_query_agent.run.journal import RunJournal
from sql_query_agent.run.service import RunService


@dataclass
class AppDeps:
    """Зависимости приложения."""

    service: RunService
    settings: Settings = field(default_factory=get_settings)
    metrics: RunMetrics = field(default_factory=current_metrics)
    journal: RunJournal | None = None
    closeables: list[Any] = field(default_factory=list)

    async def aclose(self) -> None:
        """Закрыть ресурсы, открытые приложением."""

        for resource in reversed(self.closeables):
            close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
            if close is None:
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["AppDeps"]
