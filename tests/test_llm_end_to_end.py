"""Сквозной прогон с реальной моделью GigaChat.

Тесты помечены `llm` и запускаются только с флагом `--run-llm`: они тратят
обращения к модели и требуют креденшелов. В отличие от остальных тестов,
здесь настройки читаются из `.env` целиком: креденшелы и параметры
сертификатов задаются там, а не подставляются заглушками.
"""

import pytest

from sql_query_agent.agent.graph import build_graph
from sql_query_agent.agent.llm import GigaChatAdvisor
from sql_query_agent.config import Settings
from sql_query_agent.db.catalog import SchemaCatalog
from sql_query_agent.run.report import RunStatus, build_report

pytestmark = [pytest.mark.llm, pytest.mark.integration]

TYPO_SQL = "select * from sku where product_colr = 'red'"

CATALOG = SchemaCatalog.from_rows(
    [
        ("brand", "brand_id"),
        ("brand", "brand_name"),
        ("product", "product_id"),
        ("product", "product_name"),
        ("product", "brand_id"),
        ("sku", "sku_id"),
        ("sku", "product_id"),
        ("sku", "product_size_id"),
        ("sku", "product_color_id"),
    ]
)
COLOUR_COLUMN = "product_color_id"


async def test_unfixable_name_ends_session_with_real_model(
    require_postgres: None,
    postgres_available: bool,
) -> None:
    """Имя без похожих названий завершает прогон: модель исправления не вызывается.

    Настоящая модель должна честно передать несуществующее имя инструменту,
    а решение о завершении принимает система по пустому списку кандидатов.
    """

    from langgraph.checkpoint.memory import InMemorySaver
    from psycopg_pool import AsyncConnectionPool

    from sql_query_agent.db.pool import create_pool, open_pool
    from sql_query_agent.tools.check_schema import SchemaChecker
    from tests.fakes import FakeApply, FakeMeasure

    settings = Settings()
    sql = "select * from zzz_no_such_table"
    pool: AsyncConnectionPool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        graph = build_graph(
            model=GigaChatAdvisor(settings.gigachat),
            checker=SchemaChecker(
                pool,
                schema=settings.database.schema_name,
                threshold=settings.validation.fuzzy_threshold,
                suggestion_limit=settings.validation.suggestion_limit,
            ),
            measure_tool=FakeMeasure(),
            apply_tool=FakeApply(),
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "llm-unfixable"}}

        await graph.ainvoke({"original_sql": sql, "current_sql": sql}, config)

        snapshot = await graph.aget_state(config)
        report = build_report("llm-unfixable", dict(snapshot.values), [])
        assert report.status is RunStatus.UNKNOWN_UNFIXABLE
        assert not snapshot.interrupts
        assert "zzz_no_such_table" in report.unfixable_message
    finally:
        await pool.close()


async def test_real_model_fixes_both_table_and_column(
    require_postgres: None,
    postgres_available: bool,
) -> None:
    """Настоящая модель исправляет и имя таблицы, и имя колонки.

    Проверка имён обязана сообщить оба ненайденных имени: иначе модель
    починит только то, о чём ей сказали, и запрос упадёт на замере.
    """

    from psycopg_pool import AsyncConnectionPool

    from sql_query_agent.db.pool import create_pool, open_pool
    from sql_query_agent.tools.check_schema import SchemaChecker

    settings = Settings()
    sql = "select * from sku2 where product_id2 = 42"
    pool: AsyncConnectionPool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        checker = SchemaChecker(
            pool,
            schema=settings.database.schema_name,
            threshold=settings.validation.fuzzy_threshold,
            suggestion_limit=settings.validation.suggestion_limit,
        )
        advisor = GigaChatAdvisor(settings.gigachat)

        message = await advisor.extract_identifiers(sql, [checker.as_tool()])
        tool_calls = [
            call
            for call in (getattr(message, "tool_calls", None) or [])
            if call.get("name") == "check_schema"
        ]
        assert tool_calls, "модель не вызвала инструмент проверки имён"

        result = await checker.run(list(tool_calls[0]["args"].get("entities") or []))
        found = {(item["kind"], item["name"]) for item in result["unknown"]}
        assert found == {("table", "sku2"), ("column", "product_id2")}

        fixed = await advisor.propose_fix(sql, list(result["unknown"]))
        assert "from sku" in fixed and "sku2" not in fixed
        assert "product_id = 42" in fixed and "product_id2" not in fixed
    finally:
        await pool.close()


def test_credentials_are_configured() -> None:
    """Креденшелы GigaChat заданы в окружении."""

    settings = Settings()

    assert settings.gigachat.credentials.get_secret_value()


async def test_real_model_extracts_names_and_proposes_fix(
    require_postgres: None,
    postgres_available: bool,
) -> None:
    """Настоящая модель находит опечатку по `information_schema` и правит запрос.

    Проверяется не свободный текст модели, а её решение в терминах данных:
    инструмент получает имена из запроса, а исправление содержит реальную
    колонку из каталога. Замер при этом не выполняется — проверяется ветка
    проверки имён и исправления.
    """

    from psycopg_pool import AsyncConnectionPool

    from sql_query_agent.db.pool import create_pool, open_pool
    from sql_query_agent.tools.check_schema import SchemaChecker

    settings = Settings()
    pool: AsyncConnectionPool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        checker = SchemaChecker(
            pool,
            schema=settings.database.schema_name,
            threshold=settings.validation.fuzzy_threshold,
            suggestion_limit=settings.validation.suggestion_limit,
        )
        advisor = GigaChatAdvisor(settings.gigachat)

        message = await advisor.extract_identifiers(TYPO_SQL, [checker.as_tool()])
        tool_calls = [
            call
            for call in (getattr(message, "tool_calls", None) or [])
            if call.get("name") == "check_schema"
        ]
        assert tool_calls, "модель не вызвала инструмент проверки имён"

        result = await checker.run(list(tool_calls[0]["args"].get("entities") or []))
        assert result["unknown"], "опечатка не найдена инструментом"
        candidate_names = {
            candidate["name"]
            for item in result["unknown"]
            for candidate in item["candidates"]
        }
        assert COLOUR_COLUMN in candidate_names

        fixed = await advisor.propose_fix(TYPO_SQL, list(result["unknown"]))
        assert COLOUR_COLUMN in fixed
    finally:
        await pool.close()


async def test_real_model_proposes_index(require_postgres: None) -> None:
    """Настоящая модель предлагает индекс по команде `CREATE INDEX`.

    Проверяется форма ответа и запрет на переформулировку запроса: в
    предложении есть только команда создания индекса и обоснование.
    """

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from psycopg_pool import AsyncConnectionPool

    from sql_query_agent.db.index_apply import IndexApplier
    from sql_query_agent.db.pool import create_pool, open_pool
    from sql_query_agent.tools.apply_index import ApplyIndexTool
    from sql_query_agent.tools.check_schema import SchemaChecker
    from sql_query_agent.tools.measure_query import QueryMeasureTool, build_measurer

    settings = Settings()
    sql = "select * from sku where product_id = 42"
    pool: AsyncConnectionPool = create_pool(settings.database.dsn)
    await open_pool(pool)
    try:
        measurer = build_measurer(
            pool,
            statement_timeout_ms=settings.database.statement_timeout_ms,
            lock_timeout_ms=settings.database.lock_timeout_ms,
            warmup_runs=0,
            repeat_runs=1,
        )
        graph = build_graph(
            model=GigaChatAdvisor(settings.gigachat),
            checker=SchemaChecker(pool, schema=settings.database.schema_name),
            measure_tool=QueryMeasureTool(measurer),
            apply_tool=ApplyIndexTool(IndexApplier(pool, measurer)),
            checkpointer=InMemorySaver(),
        )

        config = {"configurable": {"thread_id": "llm-index-proposal"}}
        await graph.ainvoke({"original_sql": sql, "current_sql": sql}, config)
        snapshot = await graph.aget_state(config)
        report = build_report(
            "llm-index-proposal",
            dict(snapshot.values),
            [dict(item.value) for item in (snapshot.interrupts or [])],
        )

        assert report.status is RunStatus.AWAITING_DECISION
        assert report.index is not None
        assert report.index.ddl.strip().upper().startswith("CREATE INDEX")
        assert report.index.reason

        # Отказ от индекса завершает прогон без создания индекса.
        await graph.ainvoke(Command(resume={"accepted": False}), config)
        snapshot = await graph.aget_state(config)
        final = build_report("llm-index-proposal", dict(snapshot.values), [])
        assert final.status is RunStatus.INDEX_DECLINED
    finally:
        await pool.close()
