"""Тесты точки входа сервиса: разбор аргументов и адрес приложения."""

from sql_query_agent import server


def test_default_app_factory_points_to_real_builder() -> None:
    """Фабрика приложения указывает на рабочую сборку зависимостей."""

    module_name, attribute = server.APP_FACTORY.split(":")

    assert module_name == "sql_query_agent.api.app"
    assert attribute == "build_default_app"


def test_parser_defaults() -> None:
    """По умолчанию сервис слушает локальный адрес."""

    args = server.build_parser().parse_args([])

    assert args.host == server.DEFAULT_HOST
    assert args.port == server.DEFAULT_PORT
    assert args.reload is False


def test_parser_accepts_overrides() -> None:
    """Хост, порт и перезапуск переопределяются аргументами."""

    args = server.build_parser().parse_args(["--host", "0.0.0.0", "--port", "9001", "--reload"])

    assert args.host == "0.0.0.0"
    assert args.port == 9001
    assert args.reload is True
