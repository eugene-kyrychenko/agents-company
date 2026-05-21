"""LangGraph state machine for one sprint.

Flow: START → ceo_kickoff → analyst → finance → coo → growth → pmm
      → ceo_decide → END (status: AWAITING_HUMAN)

Phase 2 will replace END with an interrupt waiting on Discord reactions.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from apps.orchestrator.agents.analyst import AnalystAgent
from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.agents.ceo import CEOAgent
from apps.orchestrator.agents.coo import COOAgent
from apps.orchestrator.agents.finance import FinanceAgent
from apps.orchestrator.agents.growth import GrowthAgent
from apps.orchestrator.agents.pmm import PMMAgent
from apps.orchestrator.cost_tracker import CostTracker
from apps.orchestrator.state import SprintState
from apps.orchestrator.transport import ConsoleTransport, Transport

logger = logging.getLogger(__name__)


def build_agents(transport: Transport, cost_tracker: CostTracker) -> dict[str, BaseAgent]:
    """Instantiate all six personas with shared transport + cost tracker."""
    kwargs = {"transport": transport, "cost_tracker": cost_tracker}
    return {
        "ceo": CEOAgent(**kwargs),
        "analyst": AnalystAgent(**kwargs),
        "finance": FinanceAgent(**kwargs),
        "coo": COOAgent(**kwargs),
        "growth": GrowthAgent(**kwargs),
        "pmm": PMMAgent(**kwargs),
    }


def _node(agent: BaseAgent, method: str = "run") -> Callable[[SprintState], Awaitable[dict[str, Any]]]:
    """Wrap an agent method as a LangGraph node fn."""

    async def _fn(state: SprintState) -> dict[str, Any]:
        fn = getattr(agent, method)
        logger.info("→ %s.%s (sprint=%s)", agent.role.value, method, state.sprint_id)
        return await fn(state)

    _fn.__name__ = f"{agent.role.value}_{method}"
    return _fn


def build_graph(
    transport: Transport | None = None,
    cost_tracker: CostTracker | None = None,
    use_memory_checkpointer: bool = True,
):
    """Construct the compiled LangGraph for a sprint.

    Returns (compiled_graph, agents) so callers can poke individual agents
    for tests or out-of-band actions.
    """
    transport = transport or ConsoleTransport()
    cost_tracker = cost_tracker or CostTracker()
    agents = build_agents(transport, cost_tracker)

    g: StateGraph = StateGraph(SprintState)

    g.add_node("ceo_kickoff", _node(agents["ceo"], "run"))
    g.add_node("analyst", _node(agents["analyst"], "run"))
    g.add_node("finance", _node(agents["finance"], "run"))
    g.add_node("coo", _node(agents["coo"], "run"))
    g.add_node("growth", _node(agents["growth"], "run"))
    g.add_node("pmm", _node(agents["pmm"], "run"))
    g.add_node("ceo_decide", _node(agents["ceo"], "decide"))

    g.add_edge(START, "ceo_kickoff")
    g.add_edge("ceo_kickoff", "analyst")
    g.add_edge("analyst", "finance")
    g.add_edge("finance", "coo")
    g.add_edge("coo", "growth")
    g.add_edge("growth", "pmm")
    g.add_edge("pmm", "ceo_decide")
    g.add_edge("ceo_decide", END)

    checkpointer = MemorySaver() if use_memory_checkpointer else None
    compiled = g.compile(checkpointer=checkpointer)
    return compiled, agents
