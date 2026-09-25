"""Узлы графа агента.

Узлы разделены на две роли: «вычислить» и «подтвердить». Обращения к модели и
замеры выполняются в вычислительных узлах, которые не содержат `interrupt()`,
а узлы подтверждения только вызывают `interrupt()` и читают готовые данные из
состояния.

Разделение обязательно, а не стилистическое: LangGraph при возобновлении
после `interrupt()` перезапускает узел целиком, поэтому вызов модели или
замер внутри узла с `interrupt()` повторялись бы на каждое решение
пользователя.
"""

from typing import Any, Protocol

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from sql_query_agent.agent.llm import AdvisorModel
from sql_query_agent.agent.state import AgentState
from sql_query_agent.domain import UnknownName
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.tools.check_schema import SchemaChecker
from sql_query_agent.tools.schema_check import can_fix_all, describe_unfixable

UNFIXABLE_STATUS = "unknown_unfixable"

logger = get_logger(__name__)

NO_TOOL_CALL_WARNING = (
    "Модель не вызвала инструмент проверки имён: проверка имён не выполнялась. "
    "Результат запроса может оказаться ошибочным."
)


class MeasureRunner(Protocol):
    """Что нужно от инструмента замера."""

    async def run(self, sql: str) -> dict[str, Any]:
        """Измерить запрос."""


class ApplyRunner(Protocol):
    """Что нужно от инструмента применения индекса."""

    async def run(self, sql: str, index_ddl: str) -> dict[str, Any]:
        """Применить индекс и вернуть сравнение."""


def _warnings(state: AgentState) -> list[str]:
    """Накопленные предупреждения."""

    return list(state.get("warnings") or [])


def _tool_call_args(message: AIMessage) -> dict[str, Any] | None:
    """Аргументы вызова `check_schema`, если модель его сделала."""

    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        if name == "check_schema":
            args = call.get("args") if isinstance(call, dict) else None
            return dict(args or {})
    return None


async def extract_and_check(
    state: AgentState,
    *,
    model: AdvisorModel,
    checker: SchemaChecker,
) -> dict[str, Any]:
    """Извлечь имена моделью и проверить их инструментом.

    Если модель не вызвала инструмент, обработка продолжается, но в состояние
    добавляется предупреждение: проверка считается невыполненной.
    """

    message = await model.extract_identifiers(state["current_sql"], [checker.as_tool()])
    args = _tool_call_args(message)

    if args is None:
        logger.warning("модель не вызвала инструмент проверки имён")
        return {
            "entities": {},
            "schema_result": None,
            "schema_checked": False,
            "warnings": [*_warnings(state), NO_TOOL_CALL_WARNING],
        }

    result = await checker.run(args)
    return {
        "entities": args,
        "schema_result": result,
        "schema_checked": True,
    }


def needs_fix(state: AgentState) -> str:
    """Маршрут после проверки имён.

    Исправлять запрос можно только тогда, когда замены есть у всех
    ненайденных имён: иначе модель заменила бы часть имён, а запрос всё
    равно остался бы неисправимым.
    """

    result = state.get("schema_result")
    if not state.get("schema_checked") or not result:
        return "measure"
    unknown = list(result.get("unknown") or [])
    if not unknown:
        return "measure"
    # Неоднозначные колонки приходят в том же списке и исправимы всегда:
    # их лечит квалификатор, предложенный инструментом.
    if can_fix_all(_unknown_names(unknown)):
        return "prepare_fix"
    return "report_unfixable"


def _unknown_names(unknown: list[dict[str, Any]]) -> list[UnknownName]:
    """Ненайденные имена из состояния в виде доменных моделей."""

    return [UnknownName.model_validate(item) for item in unknown]


def report_unfixable(state: AgentState) -> dict[str, Any]:
    """Завершить обработку, сообщив о ненайденных именах без замен.

    Модель исправления здесь не вызывается: подставлять имя наугад опаснее,
    чем попросить пользователя поправить запрос. Замер тоже не выполняется —
    запрос заведомо не исполним.
    """

    result = state.get("schema_result") or {}
    unknown = list(result.get("unknown") or [])
    message = describe_unfixable(_unknown_names(unknown))
    logger.info("имена не найдены, замен нет", unknown=len(unknown))
    return {
        "fixed_sql": None,
        "fix_replacements": [],
        "unfixable_message": message,
        "status": UNFIXABLE_STATUS,
    }


async def prepare_fix(state: AgentState, *, model: AdvisorModel) -> dict[str, Any]:
    """Однократно исправить имена моделью.

    Повторный цикл исправления не запускается: достаточно ли правки, решает
    пользователь.
    """

    result = state.get("schema_result") or {}
    unknown = list(result.get("unknown") or [])
    if not can_fix_all(_unknown_names(unknown)):
        # Страховка на случай прямого вызова узла: без кандидатов модель
        # начала бы придумывать имена, которых в запросе не было.
        logger.warning("исправление без кандидатов пропущено", unknown=len(unknown))
        return report_unfixable(state)

    fix = await model.propose_fix(state["current_sql"], unknown)
    logger.info(
        "модель предложила исправление",
        length=len(fix.sql),
        replacements=len(fix.replacements),
    )
    return {
        "fixed_sql": fix.sql,
        # Заявленные замены сохраняются вместе с текстом: подтверждение по
        # различию текстов делается позже, при сборке отчёта.
        "fix_replacements": [item.model_dump(mode="json") for item in fix.replacements],
    }


async def confirm_fix(state: AgentState) -> dict[str, Any]:
    """Запросить решение пользователя по исправленному запросу."""

    decision = interrupt(
        {
            "step": "schema_fix",
            "original_sql": state["current_sql"],
            "fixed_sql": state.get("fixed_sql"),
            "unknown": (state.get("schema_result") or {}).get("unknown", []),
        }
    )
    accepted = bool((decision or {}).get("accepted"))

    if not accepted:
        return {
            "fix_applied": False,
            "fix_declined": True,
            "status": "fix_declined",
        }

    return {
        "fix_applied": True,
        "fix_declined": False,
        "status": "fix_accepted",
        "current_sql": state.get("fixed_sql") or state["current_sql"],
    }


def fix_decision(state: AgentState) -> str:
    """Маршрут после решения по исправлению."""

    return "end" if state.get("fix_declined") else "measure"


async def measure(state: AgentState, *, measure_tool: MeasureRunner) -> dict[str, Any]:
    """Измерить текущий запрос."""

    result = await measure_tool.run(state["current_sql"])

    if not result.get("ok"):
        error = str(result.get("error") or "замер не удался")
        logger.warning("замер не удался", error=error)
        return {
            "before_stats": None,
            "before_plan_nodes": [],
            "proposal": None,
            "status": "measure_failed",
            "warnings": [*_warnings(state), error],
        }

    return {
        "before_stats": {
            "median_ms": result["median_ms"],
            "minimum_ms": result["minimum_ms"],
            "maximum_ms": result["maximum_ms"],
            "runs": result["runs"],
        },
        "before_plan_nodes": list(result.get("plan_nodes") or []),
    }


def has_baseline(state: AgentState) -> str:
    """Маршрут после замера."""

    return "prepare_index" if state.get("before_stats") else "end"


async def prepare_index(state: AgentState, *, model: AdvisorModel) -> dict[str, Any]:
    """Получить от модели предложение по индексу."""

    suggestion = await model.propose_index(
        state["current_sql"],
        list(state.get("before_plan_nodes") or []),
        dict(state.get("before_stats") or {}),
    )
    proposal = {"ddl": suggestion.ddl.strip(), "reason": suggestion.reason}
    logger.info("модель предложила индекс", ddl=proposal["ddl"])
    return {"proposal": proposal}


async def confirm_index(state: AgentState) -> dict[str, Any]:
    """Запросить решение пользователя по предложенному индексу."""

    proposal = state.get("proposal") or {}
    decision = interrupt(
        {
            "step": "index_proposal",
            "sql": state["current_sql"],
            "ddl": proposal.get("ddl"),
            "reason": proposal.get("reason"),
            "before_stats": state.get("before_stats"),
        }
    )
    accepted = bool((decision or {}).get("accepted"))

    if not accepted:
        return {"index_declined": True, "status": "index_declined"}

    return {"index_declined": False, "status": "index_accepted"}


def index_decision(state: AgentState) -> str:
    """Маршрут после решения по индексу."""

    return "end" if state.get("index_declined") else "apply"


async def apply_index(state: AgentState, *, apply_tool: ApplyRunner) -> dict[str, Any]:
    """Применить индекс внутри транзакции и получить сравнение."""

    proposal = state.get("proposal") or {}
    result = await apply_tool.run(state["current_sql"], str(proposal.get("ddl", "")))
    update: dict[str, Any] = {
        "apply_result": result,
        "status": "compared" if result.get("applied") else "apply_failed",
    }
    if not result.get("applied"):
        update["warnings"] = [
            *_warnings(state),
            str(result.get("error") or "индекс не применён"),
        ]
    return update
