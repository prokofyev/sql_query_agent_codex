"""Тесты инструмента проверки имён: описание и форма аргументов."""

from langchain_gigachat.utils.function_calling import convert_to_gigachat_tool

from sql_query_agent.domain import SchemaEntities
from sql_query_agent.tools.check_schema import CHECK_SCHEMA_DESCRIPTION


def _contains_key(node: object, key: str) -> bool:
    """Есть ли ключ где-либо в дереве схемы."""

    if isinstance(node, dict):
        return key in node or any(_contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(v, key) for v in node)
    return False


def test_schema_has_no_unsupported_constructs() -> None:
    """Схема аргументов не содержит `anyOf` и `allOf`, которые не поддерживает GigaChat."""

    parameters = convert_to_gigachat_tool(SchemaEntities)["function"]["parameters"]

    assert not _contains_key(parameters, "anyOf")
    assert not _contains_key(parameters, "allOf")


def test_every_object_has_properties() -> None:
    """У каждого объекта в схеме есть `properties` — иначе провайдер вернёт 422."""

    parameters = convert_to_gigachat_tool(SchemaEntities)["function"]["parameters"]

    def walk(node: object) -> list[dict]:
        found: list[dict] = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for value in node.values():
                found.extend(walk(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(walk(value))
        return found

    objects = walk(parameters)
    assert objects
    for obj in objects:
        assert "properties" in obj


def test_description_mentions_expected_input() -> None:
    """Описание инструмента требует передавать имена как есть и не исправлять опечатки."""

    lowered = CHECK_SCHEMA_DESCRIPTION.lower()

    assert "опечатк" in lowered
    assert "похожие" in lowered
