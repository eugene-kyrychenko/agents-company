"""End-to-end dry-run of one sprint. Requires:
- ANTHROPIC_API_KEY set
- docker-compose up postgres redis litellm  (running)

Run with:  pytest tests/e2e -m e2e -s
"""
from __future__ import annotations

import os

import pytest

from apps.orchestrator.config import settings
from apps.orchestrator.cost_tracker import CostTracker
from apps.orchestrator.graph import build_graph
from apps.orchestrator.state import SprintState, SprintStatus

pytestmark = pytest.mark.e2e

requires_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") and not settings.anthropic_api_key,
    reason="ANTHROPIC_API_KEY not set",
)


@requires_api_key
async def test_full_sprint_haiku_under_budget() -> None:
    """One full sprint with default tiers; cost ceiling is $0.50."""
    cost_tracker = CostTracker()
    graph, _ = build_graph(cost_tracker=cost_tracker)

    initial = SprintState(niche_hint="chrome extension for code review")
    config = {"configurable": {"thread_id": initial.sprint_id}}

    result_dict = await graph.ainvoke(initial, config=config)
    state = SprintState.model_validate(result_dict)

    assert state.hypothesis, "CEO must produce a hypothesis"
    assert state.market_report, "Analyst must produce a market report"
    assert state.prd, "Analyst must produce a PRD"
    assert state.financial_model, "Finance must produce a model"
    assert state.tasks, "COO must produce a task list"
    assert state.gtm_plan, "Growth must produce a GTM plan"
    assert state.copy_bundle, "PMM must produce copy"
    assert state.decision, "CEO must produce a verdict"
    assert state.status is SprintStatus.AWAITING_HUMAN

    spend = await cost_tracker.spend_today_usd()
    assert spend < 0.50, f"sprint over budget: ${spend:.4f}"
