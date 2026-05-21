"""Phase 1 unit tests — no external services, no LLM calls."""
from __future__ import annotations

import pytest

from apps.orchestrator.permissions import (
    ALL_CHANNELS,
    ALLOWED_WRITE_CHANNELS,
    PermissionError,
    assert_can_write,
)
from apps.orchestrator.state import (
    AgentRole,
    Competitor,
    CopyBundle,
    Decision,
    FinancialModel,
    GTMPlan,
    MarketReport,
    PRDDocument,
    PRDFeature,
    SprintState,
    SprintStatus,
)


# ── State model ──────────────────────────────────────────────────────


def test_sprint_state_defaults() -> None:
    s = SprintState()
    assert s.sprint_id.startswith("sprint-")
    assert s.status is SprintStatus.PLANNING
    assert s.total_cost_usd == 0.0
    assert s.messages == []
    assert s.hypothesis is None


def test_sprint_state_round_trip() -> None:
    s = SprintState(
        niche_hint="chrome ext",
        hypothesis="A foo for bar that bazzes.",
        market_report=MarketReport(
            summary="strong demand",
            demand_signals=["s1", "s2", "s3"],
            target_persona="indie devs",
            competitors=[Competitor(name="Foo", url="https://foo.test")],
        ),
        prd=PRDDocument(
            product_name="Foo",
            one_liner="Foo for bar",
            problem_statement="Bar takes too long.",
            target_user="indie devs",
            mvp_features=[PRDFeature(name="X", description="Y", priority="must")],
            success_metric="20 WAU",
        ),
        financial_model=FinancialModel(exit_readiness_score=7.5, notes="solid"),
        gtm_plan=GTMPlan(
            primary_channel="Chrome Web Store SEO",
            validation_experiment="post on /r/webdev",
            success_threshold="20 signups",
        ),
        copy_bundle=CopyBundle(
            landing_headline="Stop foo",
            landing_subheadline="for devs who bar",
            landing_cta="Install",
        ),
        decision=Decision(
            verdict="go",
            rationale="signals + readiness",
            next_action="build the wedge",
            confidence=0.7,
        ),
    )
    dumped = s.model_dump_json()
    restored = SprintState.model_validate_json(dumped)
    assert restored.hypothesis == s.hypothesis
    assert restored.financial_model is not None
    assert restored.financial_model.exit_readiness_score == 7.5


# ── Permissions ──────────────────────────────────────────────────────


def test_all_channels_covered() -> None:
    """Every allowed channel must be a known channel."""
    for role, channels in ALLOWED_WRITE_CHANNELS.items():
        assert channels <= ALL_CHANNELS, f"{role}: unknown channels {channels - ALL_CHANNELS}"


def test_only_ceo_writes_founder_decisions() -> None:
    """Critical invariant: only CEO is allowed in #founder-decisions."""
    for role, channels in ALLOWED_WRITE_CHANNELS.items():
        if role is AgentRole.CEO:
            assert "founder-decisions" in channels
        else:
            assert "founder-decisions" not in channels


def test_permission_enforcement() -> None:
    assert_can_write(AgentRole.CEO, "founder-decisions")
    assert_can_write(AgentRole.ANALYST, "market-research")

    with pytest.raises(PermissionError):
        assert_can_write(AgentRole.ANALYST, "founder-decisions")
    with pytest.raises(PermissionError):
        assert_can_write(AgentRole.PMM, "c-level-strategy")
    with pytest.raises(PermissionError):
        assert_can_write(AgentRole.CEO, "no-such-channel")


# ── Graph wiring ─────────────────────────────────────────────────────


def test_graph_compiles() -> None:
    """The full LangGraph must wire without exceptions."""
    from apps.orchestrator.graph import build_graph

    graph, agents = build_graph(use_memory_checkpointer=True)
    assert graph is not None
    assert set(agents.keys()) == {"ceo", "analyst", "finance", "coo", "growth", "pmm"}


def test_agents_have_disjoint_role_attribute() -> None:
    """Each agent class declares its own AgentRole."""
    from apps.orchestrator.agents.analyst import AnalystAgent
    from apps.orchestrator.agents.ceo import CEOAgent
    from apps.orchestrator.agents.coo import COOAgent
    from apps.orchestrator.agents.finance import FinanceAgent
    from apps.orchestrator.agents.growth import GrowthAgent
    from apps.orchestrator.agents.pmm import PMMAgent

    roles = [
        CEOAgent.role,
        AnalystAgent.role,
        FinanceAgent.role,
        COOAgent.role,
        GrowthAgent.role,
        PMMAgent.role,
    ]
    assert len(set(roles)) == 6, "agent roles must be unique"
