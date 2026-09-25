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

EXTRACTION_SYSTEM_PROMPT = """Ты собираешь имена объектов из SQL-запроса.

Вызови инструмент check_schema и передай ему три списка:

- tables — настоящие имена таблиц запроса, без алиасов;
- aliases — алиасы запроса парами «алиас=настоящая таблица», например b=brand;
- columns — имена колонок ровно так, как они написаны, вместе с квалификатором, \
если он есть в запросе (product.brand_id, brand_name).

Важно: передавай имена ровно так, как они написаны в запросе. Не исправляй \
опечатки и не догадывайся, что имелось в виду, — исправлением занимается \
инструмент. Не решай, какой таблице принадлежит колонка: это определяет \
инструмент по схеме базы. Колонку с квалификатором передавай вместе с \
квалификатором, колонку без квалификатора — без него."""

FIX_SYSTEM_PROMPT = """Ты исправляешь имена объектов SQL-запроса.

Тебе дан исходный запрос и список ненайденных имён с похожими существующими \
названиями. Исправь в запросе только имена, заменив их на предложенные \
варианты. Ничего больше в запросе не меняй: не переписывай логику, не \
добавляй и не удаляй условия, не меняй порядок и состав колонок.

Среди имён могут быть неоднозначные колонки: они есть в нескольких таблицах \
запроса, и СУБД откажется выполнять такой запрос. Такую колонку обязательно \
квалифицируй именем одной из перечисленных таблиц, в которых она есть, \
например brand_id → product.brand_id. Заменой имени это не является: само имя \
колонки остаётся прежним.

Если для имени не предложено ни одного варианта, не придумывай название \
самостоятельно: оставь это имя в запросе как есть и не заменяй его.

Верни полный текст исправленного запроса и список сделанных замен. В список \
замен попадает только то, что ты действительно изменил, — по одной записи на \
каждую замену, с прежним и новым написанием имени. Если ты ничего не менял, \
список замен пуст."""

INDEX_SYSTEM_PROMPT = """Ты предлагаешь индекс для ускорения SQL-запроса в PostgreSQL.

Тебе дан запрос, план его выполнения и замеры времени. Предложи одну команду \
CREATE INDEX, которая может ускорить этот запрос, и объясни, на каком \
основании выбран такой индекс.

Если по плану видно, что узкое место не в поиске строк (например, \
последовательное чтение неизбежно или основное время уходит на соединение и \
агрегацию), скажи об этом прямо и предложи индекс, который действительно \
меняет план, либо честно укажи, что индекс вряд ли поможет."""


class NameReplacement(BaseModel):
    """Замена имени, которую модель сделала в запросе.

    Модель заявляет замены явно, но отчёт показывает только те из них,
    которые подтверждаются различием исходного и исправленного запросов.
    """

    old_name: str = Field(description="Имя так, как оно записано в исходном запросе")
    new_name: str = Field(description="Имя, которым оно заменено в исправленном запросе")
    kind: str = Field(default="", description="Что это: table или column")
    table: str = Field(default="", description="Таблица, к которой относится колонка")


class SqlFix(BaseModel):
    """Исправленный запрос и заявленные им замены."""

    sql: str = Field(description="Полный текст SQL-запроса с исправленными именами")
    replacements: list[NameReplacement] = Field(
        default_factory=list,
        description="Замены имён, сделанные в запросе; пусто, если замен не было",
    )


class IndexProposal(BaseModel):
    """Предложение по индексу."""

    ddl: str = Field(description="Команда CREATE INDEX целиком")
    reason: str = Field(description="Почему этот индекс должен ускорить запрос")


class AdvisorModel(Protocol):
    """Что агент ожидает от языковой модели."""

    async def extract_identifiers(self, sql: str, tools: list[Any]) -> AIMessage:
        """Вернуть ответ модели, возможно с вызовом инструмента."""

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> SqlFix:
        """Вернуть исправленный запрос и заявленные замены."""

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
        if item.get("ambiguous"):
            # Неоднозначные колонки описывает format_ambiguous: это не
            # ненайденное имя, и путать их в промпте нельзя.
            continue
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


def format_ambiguous(unknown: list[dict[str, Any]]) -> str:
    """Описать неоднозначные колонки для промпта.

    Неоднозначность — не ненайденное имя: колонка существует, но сразу в
    нескольких таблицах запроса, поэтому её нужно квалифицировать.
    """

    lines: list[str] = []
    for item in unknown:
        if not item.get("ambiguous"):
            continue
        tables = ", ".join(str(table) for table in item.get("searched_tables") or [])
        lines.append(
            f"- колонка «{item.get('name')}» есть в таблицах: {tables}. "
            "Квалифицируй её именем одной из этих таблиц."
        )
    return "\n".join(lines)


def restore_escaped_newlines(sql: str) -> str:
    """Вернуть настоящие переносы строк в ответе модели.

    GigaChat через структурированный вывод иногда отдаёт перевод строки парой
    символов `\\` и `n`. Такой запрос PostgreSQL не исполняет, поэтому пару
    нужно превратить в настоящий перевод строки. Внутри строковых литералов и
    кавычек-идентификаторов текст не меняется: там `\\n` может быть частью
    значения или регулярного выражения.
    """

    out: list[str] = []
    index = 0
    state = "code"
    length = len(sql)
    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]
        if state == "literal":
            if pair == "''":
                out.append(pair)
                index += 2
                continue
            if char == "'":
                state = "code"
            out.append(char)
            index += 1
            continue
        if state == "quoted":
            if char == '"':
                state = "code"
            out.append(char)
            index += 1
            continue
        if state == "block_comment" and pair == "*/":
            state = "code"
            out.append(pair)
            index += 2
            continue
        if state == "code" and pair == "--":
            state = "line_comment"
            out.append(pair)
            index += 2
            continue
        if state == "code" and pair == "/*":
            state = "block_comment"
            out.append(pair)
            index += 2
            continue
        if state == "code" and char == "'":
            state = "literal"
            out.append(char)
            index += 1
            continue
        if state == "code" and char == '"':
            state = "quoted"
            out.append(char)
            index += 1
            continue
        if state == "line_comment" and pair == "\\n":
            state = "code"
        if pair == "\\n":
            out.append("\n")
            index += 2
            continue
        if pair == "\\t":
            out.append("\t")
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


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

    async def propose_fix(self, sql: str, unknown: list[dict[str, Any]]) -> SqlFix:
        """Попросить модель исправить имена один раз."""

        structured = self._chat.with_structured_output(SqlFix)
        parts = [f"Исходный запрос:\n{sql}\n"]
        ambiguous = format_ambiguous(unknown)
        if ambiguous:
            parts.append(f"Неоднозначные колонки:\n{ambiguous}\n")
        parts.append(f"Ненайденные имена и похожие варианты:\n{format_unknown(unknown)}")
        messages: list[BaseMessage] = [
            SystemMessage(content=FIX_SYSTEM_PROMPT),
            HumanMessage(content="\n".join(parts)),
        ]
        result = await structured.ainvoke(messages)
        sql = result.sql
        if "\\n" in sql and "\n" not in sql:
            sql = restore_escaped_newlines(sql)
        return result.model_copy(update={"sql": sql.strip()})

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
