"""Сборка графа агента.

Схема:

```
START -> extract_and_check -> [needs_fix]
            |                    |
            |                    +-- prepare_fix -> confirm_fix -> [fix_decision]
            |                    |                     |                 |
            |                    |                     | отказ -> END    +-- принято -> measure
            |                    +-- measure <-------- + (нет опечаток)
            v
        measure -> [has_baseline] -> prepare_index -> confirm_index -> [index_decision]
                        |                        |                    |
                        |                        | отказ -> END       +-- принято ->
                        +-- ошибка -> END        |                    apply_index -> END
```
"""

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from sql_query_agent.agent import nodes
from sql_query_agent.agent.llm import AdvisorModel
from sql_query_agent.agent.state import AgentState
from sql_query_agent.logging_setup import get_logger
from sql_query_agent.tools.check_schema import SchemaChecker

logger = get_logger(__name__)

EXTRACT_NODE = "extract_and_check"
PREPARE_FIX_NODE = "prepare_fix"
CONFIRM_FIX_NODE = "confirm_fix"
MEASURE_NODE = "measure"
PREPARE_INDEX_NODE = "prepare_index"
CONFIRM_INDEX_NODE = "confirm_index"
APPLY_NODE = "apply_index"


def build_graph(
    *,
    model: AdvisorModel,
    checker: SchemaChecker,
    measure_tool: Any,
    apply_tool: Any,
    checkpointer: Any,
) -> Any:
    """Собрать и скомпилировать граф агента."""

    builder = StateGraph(AgentState)

    builder.add_node(
        EXTRACT_NODE,
        partial(nodes.extract_and_check, model=model, checker=checker),
    )
    builder.add_node(PREPARE_FIX_NODE, partial(nodes.prepare_fix, model=model))
    builder.add_node(CONFIRM_FIX_NODE, nodes.confirm_fix)
    builder.add_node(MEASURE_NODE, partial(nodes.measure, measure_tool=measure_tool))
    builder.add_node(PREPARE_INDEX_NODE, partial(nodes.prepare_index, model=model))
    builder.add_node(CONFIRM_INDEX_NODE, nodes.confirm_index)
    builder.add_node(APPLY_NODE, partial(nodes.apply_index, apply_tool=apply_tool))

    builder.add_edge(START, EXTRACT_NODE)
    builder.add_conditional_edges(
        EXTRACT_NODE,
        nodes.needs_fix,
        {
            "prepare_fix": PREPARE_FIX_NODE,
            "measure": MEASURE_NODE,
        },
    )
    builder.add_edge(PREPARE_FIX_NODE, CONFIRM_FIX_NODE)
    builder.add_conditional_edges(
        CONFIRM_FIX_NODE,
        nodes.fix_decision,
        {"end": END, "measure": MEASURE_NODE},
    )
    builder.add_conditional_edges(
        MEASURE_NODE,
        nodes.has_baseline,
        {"prepare_index": PREPARE_INDEX_NODE, "end": END},
    )
    builder.add_edge(PREPARE_INDEX_NODE, CONFIRM_INDEX_NODE)
    builder.add_conditional_edges(
        CONFIRM_INDEX_NODE,
        nodes.index_decision,
        {"end": END, "apply": APPLY_NODE},
    )
    builder.add_edge(APPLY_NODE, END)

    return builder.compile(checkpointer=checkpointer)
