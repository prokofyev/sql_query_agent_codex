"""Тесты адаптера модели: ответов без обращений к GigaChat.

Клиент модели подменяется: проверяется обработка ответа исправления,
восстановление переносов строк, испорченных транспортом, и подробный лог
ответов модели.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from sql_query_agent.agent.llm import (
    GigaChatAdvisor,
    IndexProposal,
    SqlFix,
    describe_tool_calls,
    restore_escaped_newlines,
)
from sql_query_agent.config import GigaChatSettings

ESCAPED_SQL = "select sku_id,\\n       product_id\\nfrom sku\\nwhere product_id = 42"
RESTORED_SQL = "select sku_id,\n       product_id\nfrom sku\nwhere product_id = 42"


def test_escaped_newlines_become_real_ones() -> None:
    """Пары `\\` и `n` вне литералов превращаются в настоящие переносы."""

    assert restore_escaped_newlines(ESCAPED_SQL) == RESTORED_SQL
    assert restore_escaped_newlines("select 1\\t, 2") == "select 1\t, 2"


def test_newlines_inside_literals_and_identifiers_are_untouched() -> None:
    """`\\n` внутри литерала или кавычек-идентификаторов — часть значения."""

    assert restore_escaped_newlines("select regexp_replace(name, '\\n', '') from t") == (
        "select regexp_replace(name, '\\n', '') from t"
    )
    assert restore_escaped_newlines("select E'a\\nb' from t") == "select E'a\\nb' from t"
    assert restore_escaped_newlines('select "a\\nb" from t') == 'select "a\\nb" from t'
    assert restore_escaped_newlines("select '\\t' from t") == "select '\\t' from t"


def test_double_backslash_is_not_a_newline() -> None:
    """`\\s+` в регулярном выражении остаётся как есть."""

    sql = "select regexp_replace(name, '\\\\s+', ' ') from t"
    assert restore_escaped_newlines(sql) == sql


def test_escaped_newlines_in_comments_are_restored() -> None:
    """Комментарий тоже должен закончиться: иначе запрос сломается."""

    assert restore_escaped_newlines("select 1 -- c\\nfrom sku") == "select 1 -- c\nfrom sku"
    assert restore_escaped_newlines("select 1 /* a\\nb */") == "select 1 /* a\nb */"


def test_real_newlines_are_untouched() -> None:
    """Ответ с настоящими переносами не меняется."""

    assert restore_escaped_newlines(RESTORED_SQL) == RESTORED_SQL


class _Structured:
    """Подставной структурированный вывод: возвращает заранее заданный ответ."""

    def __init__(self, fix: SqlFix) -> None:
        self._fix = fix
        self.calls = 0

    async def ainvoke(self, _messages: list[Any]) -> SqlFix:
        """Вернуть заданный ответ исправления."""

        self.calls += 1
        return self._fix


class _FakeChat:
    """Подставной клиент модели: отдаёт один и тот же структурированный ответ."""

    def __init__(self, fix: SqlFix) -> None:
        self.structured = _Structured(fix)

    def with_structured_output(self, _schema: Any) -> _Structured:
        """Вернуть подставной структурированный вывод."""

        return self.structured


UNKNOWN = [
    {
        "kind": "table",
        "table": "skuu",
        "name": "skuu",
        "candidates": [{"name": "sku", "score": 90.0}],
    }
]


async def test_propose_fix_restores_newlines_in_model_answer() -> None:
    """Исправленный запрос приходит пользователю с настоящими переносами."""

    chat = _FakeChat(SqlFix(sql=ESCAPED_SQL))
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=chat)

    fix = await advisor.propose_fix("select * from skuu", UNKNOWN)

    assert fix.sql == RESTORED_SQL


async def test_propose_fix_keeps_correct_answer() -> None:
    """Ответ с настоящими переносами не переписывается."""

    chat = _FakeChat(SqlFix(sql=RESTORED_SQL))
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=chat)

    fix = await advisor.propose_fix("select * from skuu", UNKNOWN)

    assert fix.sql == RESTORED_SQL


class _ToolBoundChat:
    """Подставной клиент модели: возвращает ответ с вызовом инструмента."""

    def __init__(self, response: AIMessage) -> None:
        self._response = response

    def bind_tools(self, _tools: list[Any]) -> "_ToolBoundChat":
        """Принять инструменты и вернуть себя."""

        return self

    async def ainvoke(self, _messages: list[Any]) -> AIMessage:
        """Вернуть заранее заданный ответ."""

        return self._response


class _IndexChat:
    """Подставной клиент модели: возвращает заданное предложение индекса."""

    def __init__(self, proposal: IndexProposal) -> None:
        self._proposal = proposal

    def with_structured_output(self, _schema: Any) -> "_IndexChat":
        """Принять схему и вернуть себя."""

        return self

    async def ainvoke(self, _messages: list[Any]) -> IndexProposal:
        """Вернуть заранее заданное предложение."""

        return self._proposal


def test_describe_tool_calls_keeps_names_and_args() -> None:
    """Описание вызова инструмента содержит имя и аргументы."""

    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "check_schema",
                "args": {"tables": ["product"], "columns": ["brand_name"]},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )

    assert describe_tool_calls(response) == [
        {"name": "check_schema", "args": {"tables": ["product"], "columns": ["brand_name"]}}
    ]
    assert describe_tool_calls(AIMessage(content="нет вызовов")) == []


async def test_extraction_logs_tool_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    """В лог попадает, какие имена модель передала инструменту."""

    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "check_schema",
                "args": {"tables": ["product", "brand"], "columns": ["brand_name"]},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=_ToolBoundChat(response))

    await advisor.extract_identifiers("select brand_name from product", [])

    output = capsys.readouterr().out
    assert "brand_name" in output
    assert "вызов".lower() in output.lower() or "tool" in output.lower()


async def test_extraction_does_not_log_source_sql(capsys: pytest.CaptureFixture[str]) -> None:
    """Текст исходного запроса в лог не попадает."""

    source = "select secret_column from secret_table"
    response = AIMessage(content="", tool_calls=[])
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=_ToolBoundChat(response))

    await advisor.extract_identifiers(source, [])

    output = capsys.readouterr().out
    assert "secret_table" not in output
    assert "secret_column" not in output


async def test_fix_logs_answer_and_replacements(capsys: pytest.CaptureFixture[str]) -> None:
    """В лог попадает исправленный запрос и заявленные замены."""

    chat = _FakeChat(
        SqlFix(
            sql=RESTORED_SQL,
            replacements=[
                {"old_name": "skuu", "new_name": "sku", "kind": "table", "table": ""}
            ],
        )
    )
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=chat)

    await advisor.propose_fix("select * from skuu", UNKNOWN)

    output = capsys.readouterr().out
    assert "исправление запроса" in output
    assert "skuu" in output
    assert "sku" in output


async def test_index_proposal_is_logged(capsys: pytest.CaptureFixture[str]) -> None:
    """В лог попадает предложение по индексу."""

    proposal = IndexProposal(
        ddl="CREATE INDEX ON sku (product_id)",
        reason="индекс по фильтру",
    )
    advisor = GigaChatAdvisor(GigaChatSettings(_env_file=None), chat=_IndexChat(proposal))

    await advisor.propose_index("select 1", ["Seq Scan sku"], {"median_ms": 5.0})

    output = capsys.readouterr().out
    assert "предложение индекса" in output
    assert "CREATE INDEX ON sku (product_id)" in output
    assert "индекс по фильтру" in output
