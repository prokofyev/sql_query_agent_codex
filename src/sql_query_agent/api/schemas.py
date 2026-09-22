"""Схемы HTTP-слоя: запросы, ответы и конверт ошибки."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sql_query_agent.presets import PresetQuery
from sql_query_agent.run.report import RunReport


class PresetSchema(BaseModel):
    """Предустановленный запрос для интерфейса."""

    id: str
    title: str
    sql: str
    expected: str
    note: str = ""

    @classmethod
    def from_domain(cls, preset: PresetQuery) -> "PresetSchema":
        """Построить схему по описанию запроса."""

        return cls(
            id=preset.id,
            title=preset.title,
            sql=preset.sql,
            expected=preset.expected.value,
            note=preset.note,
        )


class PresetsResponse(BaseModel):
    """Библиотека предустановленных запросов."""

    presets: list[PresetSchema] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    """Запрос на обработку SQL-запроса."""

    model_config = ConfigDict(extra="forbid")

    sql: str


class DecisionRequest(BaseModel):
    """Решение пользователя по предложению агента."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool


class ComparisonSchema(BaseModel):
    """Сравнение замеров до и после применения индекса."""

    applied: bool
    error: str | None = None
    index_ddl: str = ""
    before_median_ms: float = 0.0
    after_median_ms: float = 0.0
    speedup: float | None = None
    verdict: str
    improved: bool = False
    no_speedup: bool = False
    reason: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class StatsSchema(BaseModel):
    """Замер одной фазы: минимум, медиана, максимум, число повторов."""

    minimum_ms: float = 0.0
    median_ms: float = 0.0
    maximum_ms: float = 0.0
    runs: int = 0


class FixSchema(BaseModel):
    """Предложение об исправлении запроса."""

    original_sql: str
    fixed_sql: str
    replacements: list[dict[str, Any]] = Field(default_factory=list)


class IndexSchema(BaseModel):
    """Предложение об индексе."""

    ddl: str
    reason: str = ""
    before: StatsSchema | None = None


class RunReportSchema(BaseModel):
    """Отчёт о прогоне для внешних клиентов и интерфейса."""

    thread_id: str
    status: str
    step: str | None = None
    awaiting_decision: bool = False
    original_sql: str
    current_sql: str
    schema_checked: bool = False
    unknown: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unfixable_message: str = ""
    fix: FixSchema | None = None
    index: IndexSchema | None = None
    comparison: ComparisonSchema | None = None
    decisions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def from_domain(cls, report: RunReport) -> "RunReportSchema":
        """Построить схему по отчёту о прогоне."""

        comparison = report.comparison
        return cls(
            thread_id=report.thread_id,
            status=report.status.value,
            step=report.step,
            awaiting_decision=report.awaiting_decision,
            original_sql=report.original_sql,
            current_sql=report.current_sql,
            schema_checked=report.schema_checked,
            unknown=list(report.unknown),
            warnings=list(report.warnings),
            unfixable_message=report.unfixable_message,
            fix=(
                FixSchema(
                    original_sql=report.fix.original_sql,
                    fixed_sql=report.fix.fixed_sql,
                    replacements=[
                        item.model_dump(mode="json") for item in report.fix.replacements
                    ],
                )
                if report.fix
                else None
            ),
            index=(
                IndexSchema(
                    ddl=report.index.ddl,
                    reason=report.index.reason,
                    before=(
                        StatsSchema(**report.index.before.model_dump(mode="json"))
                        if report.index.before
                        else None
                    ),
                )
                if report.index
                else None
            ),
            comparison=(
                ComparisonSchema(
                    applied=comparison.applied,
                    error=comparison.error,
                    index_ddl=comparison.index_ddl,
                    before_median_ms=comparison.before_median_ms,
                    after_median_ms=comparison.after_median_ms,
                    speedup=comparison.speedup,
                    verdict=comparison.verdict.value,
                    improved=comparison.improved,
                    no_speedup=comparison.no_speedup,
                    reason=comparison.reason,
                    before=(
                        comparison.before.model_dump(mode="json") if comparison.before else None
                    ),
                    after=(
                        comparison.after.model_dump(mode="json") if comparison.after else None
                    ),
                )
                if comparison
                else None
            ),
            decisions=dict(report.decisions),
            error=report.error,
        )


class HistoryResponse(BaseModel):
    """История прогонов: отчёты в порядке от свежих к старым."""

    runs: list[RunReportSchema] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Ответ проверки жизнеспособности."""

    status: str = "ok"


class ErrorResponse(BaseModel):
    """Единый конверт ошибки."""

    code: str
    message: str


__all__ = [
    "ComparisonSchema",
    "DecisionRequest",
    "ErrorResponse",
    "FixSchema",
    "HealthResponse",
    "HistoryResponse",
    "IndexSchema",
    "PresetSchema",
    "PresetsResponse",
    "RunReportSchema",
    "StartRunRequest",
    "StatsSchema",
]
