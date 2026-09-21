"""Совместимая точка входа: запуск сервиса из корня репозитория."""

from sql_query_agent.server import main

if __name__ == "__main__":
    raise SystemExit(main())
