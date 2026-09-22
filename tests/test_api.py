"""Тесты API: запуск прогона, решения, валидация ввода, метрики и история."""

from typing import Any

from sql_query_agent.api.errors import INVALID_SQL
from tests.api_fakes import FakeWorld, build_test_client
from tests.fakes import FakeApply, FakeMeasure, FakeModel

GOOD_SQL = "select * from sku where product_id = 1"
TYPO_SQL = "select * from sku where product_colr_id = 1"
FIXED_SQL = "select * from sku where product_color_id = 1"
MISSING_TABLE = "zzz_table"
UNFIXABLE_SQL = f"select * from {MISSING_TABLE}"


def _typo_world() -> FakeWorld:
    """Мир, в котором модель находит и исправляет опечатку."""

    return FakeWorld(model=FakeModel(fixed_sql=FIXED_SQL))


async def test_start_run_reports_awaiting_decision() -> None:
    """Запуск прогона с опечаткой возвращает признак ожидания решения."""

    client, _ = build_test_client(_typo_world())

    response = await client.post("/runs", json={"sql": TYPO_SQL})

    assert response.status_code == 200
    payload = response.json()
    assert payload["awaiting_decision"] is True
    assert payload["step"] == "schema_fix"
    assert payload["fix"]["fixed_sql"] == FIXED_SQL
    assert payload["fix"]["replacements"][0]["new_name"] == "product_color_id"
    assert payload["thread_id"]


async def test_unfixable_names_are_reported_without_decision() -> None:
    """API отдаёт сообщение о ненайденных именах и не ждёт решения."""

    world = FakeWorld(
        model=FakeModel(entities=[{"table": MISSING_TABLE, "columns": []}]),
        measure=FakeMeasure(),
    )
    client, _ = build_test_client(world)

    response = await client.post("/runs", json={"sql": UNFIXABLE_SQL})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unknown_unfixable"
    assert payload["awaiting_decision"] is False
    assert payload["step"] is None
    assert MISSING_TABLE in payload["unfixable_message"]
    assert payload["fix"] is None
    assert payload["index"] is None
    assert payload["error"] is None
    assert world.measure.calls == 0


async def test_decision_continues_run() -> None:
    """Полный цикл «запуск → решение → завершение» проходит по API."""

    client, world = build_test_client(
        FakeWorld(
            model=FakeModel(
                entities=[{"table": "sku", "columns": ["product_colr_id"]}],
                fixed_sql=FIXED_SQL,
            ),
            apply=FakeApply(before_ms=100.0, after_ms=10.0),
        )
    )
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()
    thread_id = started["thread_id"]

    accepted = await client.post(f"/runs/{thread_id}/decision", json={"accepted": True})
    payload = accepted.json()

    assert payload["step"] == "index_proposal"
    assert payload["index"]["ddl"].startswith("CREATE INDEX")

    finished = (
        await client.post(f"/runs/{thread_id}/decision", json={"accepted": True})
    ).json()

    assert finished["status"] == "compared"
    assert finished["awaiting_decision"] is False
    assert finished["comparison"]["improved"] is True
    assert len(await world.journal.history()) == 1


async def test_declining_fix_ends_run() -> None:
    """Отказ от исправления завершает прогон по API."""

    client, _ = build_test_client(_typo_world())
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    payload = (
        await client.post(
            f"/runs/{started['thread_id']}/decision", json={"accepted": False}
        )
    ).json()

    assert payload["status"] == "fix_declined"
    assert payload["comparison"] is None


async def test_run_report_is_available_by_thread_id() -> None:
    """Отчёт по прогону доступен отдельным запросом."""

    client, _ = build_test_client(_typo_world())
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    response = await client.get(f"/runs/{started['thread_id']}")

    assert response.status_code == 200
    assert response.json()["thread_id"] == started["thread_id"]


async def test_unknown_run_is_not_found() -> None:
    """Неизвестный прогон даёт 404 в едином конверте."""

    client, _ = build_test_client()

    response = await client.get("/runs/no-such-run")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_decision_for_unknown_run_is_not_found() -> None:
    """Решение по неизвестному прогону даёт 404."""

    client, _ = build_test_client()

    response = await client.post("/runs/no-such-run/decision", json={"accepted": True})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_empty_input_is_rejected() -> None:
    """Пустой ввод отклоняется с понятным сообщением."""

    client, world = build_test_client()

    response = await client.post("/runs", json={"sql": "   "})

    assert response.status_code == 400
    assert response.json()["code"] == INVALID_SQL
    assert response.json()["message"] == "Запрос не задан"
    assert world.model.extract_calls == 0


async def test_multiple_statements_are_rejected() -> None:
    """Несколько команд в одном вводе отклоняются."""

    client, _ = build_test_client()

    response = await client.post("/runs", json={"sql": "select 1; select 2"})

    assert response.status_code == 400
    assert response.json()["message"] == "Допускается только один SQL-запрос"


async def test_non_select_statement_is_rejected() -> None:
    """Команда изменения данных отклоняется."""

    client, _ = build_test_client()

    response = await client.post("/runs", json={"sql": "delete from brand"})

    assert response.status_code == 400
    assert "на чтение" in response.json()["message"]


async def test_metrics_are_prometheus_text_without_sql_labels() -> None:
    """Метрики отдаются в формате Prometheus и не содержат текста запроса."""

    client, _ = build_test_client(FakeWorld(apply=FakeApply(before_ms=100.0, after_ms=10.0)))
    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    await client.post(f"/runs/{started['thread_id']}/decision", json={"accepted": True})

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert 'sqa_runs_total{status="compared"} 1.0' in body
    assert "select" not in body.lower()
    assert "sku" not in body.lower()


async def test_history_endpoint_lists_runs() -> None:
    """История прогонов отдаёт завершённые и ожидающие решения прогоны."""

    client, _ = build_test_client(_typo_world())
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    response = await client.get("/runs")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert [run["thread_id"] for run in runs] == [started["thread_id"]]


async def test_presets_endpoint_returns_library() -> None:
    """Библиотека запросов доступна по API."""

    client, _ = build_test_client()

    response = await client.get("/presets")

    assert response.status_code == 200
    presets = response.json()["presets"]
    assert len(presets) >= 5
    assert all(preset["sql"] for preset in presets)


async def test_invalid_body_is_reported_in_envelope() -> None:
    """Некорректное тело запроса даёт тот же конверт ошибки."""

    client, _ = build_test_client()

    response = await client.post("/runs", json={"query": "select 1"})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_fresh_app_has_no_runs() -> None:
    """У нового приложения история пуста."""

    client, _ = build_test_client()

    response = await client.get("/runs")

    assert response.json()["runs"] == []


__all__: list[Any] = []
