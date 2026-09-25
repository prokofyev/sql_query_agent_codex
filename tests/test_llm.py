"""Тесты адаптера модели: ответов без обращений к GigaChat.

Клиент модели подменяется: проверяется обработка ответа исправления,
в частности восстановление переносов строк, испорченных транспортом.
"""

from typing import Any

from sql_query_agent.agent.llm import GigaChatAdvisor, SqlFix, restore_escaped_newlines
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
