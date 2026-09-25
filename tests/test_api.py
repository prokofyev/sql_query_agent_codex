"""Тесты API: запуск прогона, решения, валидация ввода, метрики и история."""

from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from sql_query_agent.api.errors import (
    INVALID_SQL,
    PRESETS_UNAVAILABLE,
    SCHEMA_UNAVAILABLE,
)
from sql_query_agent.observability.metrics import RunMetrics
from tests.api_fakes import FakeWorld, build_test_client, decide
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
        model=FakeModel(entities={"tables": [MISSING_TABLE], "columns": []}),
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
                entities={"tables": ["sku"], "columns": ["product_colr_id"]},
                fixed_sql=FIXED_SQL,
            ),
            apply=FakeApply(before_ms=100.0, after_ms=10.0),
        )
    )
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    accepted = await decide(client, started, accepted=True)
    payload = accepted.json()

    assert payload["step"] == "index_proposal"
    assert payload["index"]["ddl"].startswith("CREATE INDEX")

    finished = (await decide(client, payload, accepted=True)).json()

    assert finished["status"] == "compared"
    assert finished["awaiting_decision"] is False
    assert finished["comparison"]["improved"] is True
    assert len(await world.journal.history()) == 1


async def test_declining_fix_ends_run() -> None:
    """Отказ от исправления завершает прогон по API."""

    client, _ = build_test_client(_typo_world())
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    payload = (
        await decide(client, started, accepted=False)
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
    """Решение по неизвестному прогону даёт 404, а не «не ждёт решения»."""

    client, _ = build_test_client()

    response = await client.post(
        "/runs/no-such-run/decision",
        json={"accepted": True, "step": "schema_fix"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_decision_on_completed_run_is_conflict() -> None:
    """Повторное решение по завершённому прогону даёт 409."""

    client, _ = build_test_client(
        FakeWorld(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    )
    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    finished = (await decide(client, started, accepted=True)).json()

    response = await decide(
        client, finished, accepted=True, step="index_proposal"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "not_awaiting_decision"
    assert response.json()["message"]


async def test_rejected_decision_keeps_report_unchanged() -> None:
    """Отклонённое решение не меняет отчёт о прогоне."""

    client, _ = build_test_client(
        FakeWorld(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    )
    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    finished = (await decide(client, started, accepted=True)).json()

    await decide(client, finished, accepted=False, step="index_proposal")
    stored = (await client.get(f"/runs/{finished['thread_id']}")).json()

    assert stored == finished


async def test_stale_decision_for_closed_step_is_conflict() -> None:
    """Запоздалое решение по закрытому этапу не создаёт индекс."""

    client, world = build_test_client(
        FakeWorld(
            model=FakeModel(
                entities={"tables": ["sku"], "columns": ["product_colr_id"]},
                fixed_sql=FIXED_SQL,
            ),
            apply=FakeApply(before_ms=100.0, after_ms=10.0),
        )
    )
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()
    assert started["step"] == "schema_fix"

    proposed = (await decide(client, started, accepted=True)).json()
    assert proposed["step"] == "index_proposal"
    assert world.apply.calls == 0

    stale = await client.post(
        f"/runs/{started['thread_id']}/decision",
        json={"accepted": True, "step": "schema_fix"},
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "not_awaiting_decision"
    assert world.apply.calls == 0
    assert (await client.get(f"/runs/{started['thread_id']}")).json() == proposed


async def test_decision_without_step_is_rejected() -> None:
    """Запрос решения без этапа отклоняется как неверный."""

    client, _ = build_test_client(_typo_world())
    started = (await client.post("/runs", json={"sql": TYPO_SQL})).json()

    response = await client.post(
        f"/runs/{started['thread_id']}/decision",
        json={"accepted": True},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_metrics_count_completed_run_once() -> None:
    """Повторное решение по завершённому прогону не удваивает метрики и замеры."""

    registry = CollectorRegistry()
    world = FakeWorld(apply=FakeApply(before_ms=100.0, after_ms=10.0))
    world.metrics = RunMetrics(registry)
    client, _ = build_test_client(world)
    started = (await client.post("/runs", json={"sql": GOOD_SQL})).json()
    finished = (await decide(client, started, accepted=True)).json()
    counted = generate_latest(registry)

    await decide(client, finished, accepted=True, step="index_proposal")

    assert generate_latest(registry) == counted
    assert registry.get_sample_value("sqa_runs_total", {"status": "compared"}) == 1.0
    assert registry.get_sample_value("sqa_indexes_applied_total") == 1.0


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
    await decide(client, started, accepted=True)

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
    """Библиотека запросов отдаётся из файла, заданного настройкой."""

    client, _ = build_test_client()

    response = await client.get("/presets")

    assert response.status_code == 200
    presets = response.json()["presets"]
    assert len(presets) == 12
    assert all(preset["sql"] for preset in presets)
    assert all(preset["title"] for preset in presets)
    assert all(preset["note"] for preset in presets)
    assert all("expected" not in preset for preset in presets)


async def test_presets_endpoint_reads_records_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Состав библиотеки определяется файлом, а не кодом."""

    path = tmp_path / "presets.yaml"
    path.write_text(
        "- id: only\n  title: Единственный\n  sql: select 1\n  note: из файла\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SQA_PRESETS__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/presets")

    assert response.status_code == 200
    assert response.json()["presets"] == [
        {"id": "only", "title": "Единственный", "sql": "select 1", "note": "из файла"}
    ]


async def test_presets_endpoint_with_empty_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой файл даёт пустую библиотеку и не ломает эндпоинт."""

    path = tmp_path / "presets.yaml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("SQA_PRESETS__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/presets")

    assert response.status_code == 200
    assert response.json()["presets"] == []


async def test_presets_endpoint_with_broken_record_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повреждённая запись даёт ошибку вместо повреждённого пресета."""

    path = tmp_path / "presets.yaml"
    path.write_text("- id: broken\n  title: Без SQL\n", encoding="utf-8")
    monkeypatch.setenv("SQA_PRESETS__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/presets")

    assert response.status_code == 500
    assert response.json()["code"] == PRESETS_UNAVAILABLE
    assert "sql" in response.json()["message"]


async def test_schema_endpoint_returns_diagram() -> None:
    """Схема базы отдаётся из файла, заданного настройкой."""

    client, _ = build_test_client()

    response = await client.get("/schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["caption"]
    assert "sku" in payload["diagram"]
    assert "FK" in payload["diagram"]


async def test_schema_endpoint_reads_from_configured_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Содержимое схемы определяется файлом, а не кодом."""

    path = tmp_path / "schema.yaml"
    path.write_text(
        "caption: Из файла\n\ndiagram: |\n  +--+\n  | x|\n  +--+\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SQA_SCHEMA__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/schema")

    assert response.status_code == 200
    assert response.json() == {"caption": "Из файла", "diagram": "+--+\n| x|\n+--+"}


async def test_schema_endpoint_with_missing_file_gives_empty_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отсутствующий файл даёт пустую схему и не ломает эндпоинт."""

    monkeypatch.setenv("SQA_SCHEMA__PATH", str(tmp_path / "no-such-file.yaml"))

    client, _ = build_test_client()

    response = await client.get("/schema")

    assert response.status_code == 200
    assert response.json() == {"caption": "", "diagram": ""}


async def test_schema_endpoint_with_empty_file_gives_empty_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой файл схемы даёт пустую схему."""

    path = tmp_path / "schema.yaml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("SQA_SCHEMA__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/schema")

    assert response.status_code == 200
    assert response.json() == {"caption": "", "diagram": ""}


async def test_schema_endpoint_with_broken_file_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повреждённый файл даёт ошибку вместо частичной схемы."""

    path = tmp_path / "schema.yaml"
    path.write_text("caption: Без рисунка\n", encoding="utf-8")
    monkeypatch.setenv("SQA_SCHEMA__PATH", str(path))

    client, _ = build_test_client()

    response = await client.get("/schema")

    assert response.status_code == 500
    assert response.json()["code"] == SCHEMA_UNAVAILABLE
    assert "diagram" in response.json()["message"]


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
