"""Адаптеры языковой модели.

Модель отвечает за три вещи: извлечение имён из текста запроса (через вызов
инструмента), однократное исправление запроса и предложение индекса. Всё
остальное — детерминированные инструменты.

Интерфейс `AdvisorModel` намеренно узкий: тесты подменяют модель, не поднимая
GigaChat, и проверяют граф без обращений к сети.
"""

from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from sql_query_agent.config import GigaChatSettings
from sql_query_agent.logging_setup import get_logger

logger = get_logger(__name__)

EXTRACTION_SYSTEM_PROMPT = """Ты проверяешь имена объектов в SQL-запросе.

Найди в запросе все таблицы и относящиеся к ним колонки и вызови инструмент \
check_schema, передав их списком.

Важно: передавай имена ровно так, как они написаны в запросе. Не исправляй \
опечатки и не догадывайся, что имелось в виду, — исправлением занимается \
инструмент. Если запрос обращается к таблице через алиас, указывай настоящее \
имя таблицы."""

FIX_SYSTEM_PROMPT = """Ты исправляешь опечатки в именах объектов SQL-запроса.

Тебе дан исходный запрос и список ненайденных имён с похожими существующими \
названиями. Исправь в запросе только имена, заменив их на предложенные \
варианты. Ничего больше в запросе не меняй: не переписывай логику, не \
добавляй и не удаляй условия, не меняй порядок и состав колонок.

Верни полный текст исправленного запроса."""

INDEX_SYSTEM_PROMPT = """Ты предлагаешь индекс для ускорения SQL-запроса в PostgreSQL.

Тебе дан запрос, план его выполнения и замеры времени. Предложи одну команду \
CREATE INDEX, которая может ускорить этот запрос, и объясни, на каком \
основании выбран такой индекс.

Если по плану видно, что узкое место не в поиске строк (например, \
последовательное чтение неизбежно или основное время уходит на соединение и \
агрегацию), скажи об этом прямо и предложи индекс, который действительно \
меняет план, либо честно укажи, что индекс вряд ли поможет."""


class SqlFix(BaseModel):
    """Исправленный запрос."""

    sql: str = Field(description="Полный текст SQL-запроса с исправленными именами")


class IndexProposal(BaseModel):
    """Предложение по индексу."""

    ddl: str = Field(description="Команда CREATE INDEX целиком")
    reason: str = Field(description="Почему этот индекс должен ускорить запрос")


class AdvisorModel(Protocol):
    """Что агент ожидает от языковой модели."""

    async def extract_identifiers(self, sql: str, tools: list[Any]) -> AIMessage:
        """Вернуть ответ модели, возможно с вызовом инструмента."""

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> str:
        """Вернуть исправленный запрос."""

    async def propose_index(
        self,
        sql: str,
        plan_nodes: list[str],
        stats: dict[str, Any],
    ) -> IndexProposal:
        """Вернуть предложение по индексу."""


def format_unknown(unknown: list[dict[str, Any]]) -> str:
    """Описать ненайденные имена и кандидатов для промпта."""

    lines: list[str] = []
    for item in unknown:
        candidates = item.get("candidates") or []
        rendered = ", ".join(
            f"{candidate['name']} ({candidate['score']:.0f})" for candidate in candidates
        )
        kind = "таблица" if item.get("kind") == "table" else "колонка"
        where = "" if item.get("kind") == "table" else f" в таблице {item.get('table')}"
        lines.append(
            f"- {kind}{where}: «{item['name']}» не найдено. Похожие: {rendered or 'нет'}"
        )
    return "\n".join(lines)


class GigaChatAdvisor:
    """Реализация советника поверх langchain_gigachat."""

    def __init__(self, settings: GigaChatSettings, *, chat: Any | None = None) -> None:
        self._settings = settings
        self._chat = chat or self._build_chat(settings)

    @staticmethod
    def _build_chat(settings: GigaChatSettings) -> Any:
        """Собрать клиент GigaChat."""

        from langchain_gigachat import GigaChat

        kwargs: dict[str, Any] = {
            "scope": settings.scope,
            "timeout": settings.timeout_seconds,
            "verify_ssl_certs": settings.verify_ssl,
            "max_retries": settings.max_retries,
            "model": settings.model,
        }
        credentials = settings.credentials.get_secret_value()
        if credentials:
            kwargs["credentials"] = credentials
        return GigaChat(**kwargs)

    async def extract_identifiers(self, sql: str, tools: list[Any]) -> AIMessage:
        """Попросить модель вызвать инструмент проверки имён."""

        bound = self._chat.bind_tools(tools)
        messages: list[BaseMessage] = [
            SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=f"SQL-запрос:\n{sql}"),
        ]
        response = await bound.ainvoke(messages)
        logger.info("модель вернула ответ", tool_calls=len(getattr(response, "tool_calls", [])))
        return response

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> str:
        """Попросить модель исправить имена один раз."""

        structured = self._chat.with_structured_output(SqlFix)
        messages: list[BaseMessage] = [
            SystemMessage(content=FIX_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Исходный запрос:\n{sql}\n\n"
                    f"Ненайденные имена и похожие варианты:\n{format_unknown(unknown)}"
                )
            ),
        ]
        result = await structured.ainvoke(messages)
        return result.sql.strip()

    async def propose_index(
        self,
        sql: str,
        plan_nodes: list[str],
        stats: dict[str, Any],
    ) -> IndexProposal:
        """Попросить модель предложить индекс."""

        structured = self._chat.with_structured_output(IndexProposal)
        messages: list[BaseMessage] = [
            SystemMessage(content=INDEX_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"SQL-запрос:\n{sql}\n\n"
                    f"Узлы плана: {', '.join(plan_nodes)}\n"
                    f"Замеры: медиана {stats.get('median_ms')} мс, "
                    f"минимум {stats.get('minimum_ms')} мс, "
                    f"максимум {stats.get('maximum_ms')} мс"
                )
            ),
        ]
        return await structured.ainvoke(messages)
