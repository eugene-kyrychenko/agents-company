"""Typed state passed between agents.

SprintState is the authoritative source of truth — Discord messages are
human-readable projections of fields here, never the other way around.
"""
from __future__ import annotations

import operator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class SprintStatus(StrEnum):
    PLANNING = "planning"
    RESEARCHING = "researching"
    DECIDING = "deciding"
    AWAITING_HUMAN = "awaiting_human"
    APPROVED = "approved"
    REJECTED = "rejected"
    KILLED = "killed"


class AgentRole(StrEnum):
    CEO = "ceo"
    COO = "coo"
    ANALYST = "analyst"
    FINANCE = "finance"
    GROWTH = "growth"
    PMM = "pmm"


# ── Sub-artifacts produced by individual agents ──────────────────────────


class Competitor(BaseModel):
    name: str
    url: str | None = None
    pricing: str | None = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class MarketReport(BaseModel):
    summary: str
    demand_signals: list[str] = Field(default_factory=list)
    target_persona: str
    market_size_estimate: str | None = None
    barriers_to_entry: list[str] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class PRDFeature(BaseModel):
    name: str
    description: str
    priority: Literal["must", "should", "could"] = "must"


class PRDDocument(BaseModel):
    product_name: str
    one_liner: str
    problem_statement: str
    target_user: str
    mvp_features: list[PRDFeature]
    excluded_from_mvp: list[str] = Field(default_factory=list)
    success_metric: str


class FinancialModel(BaseModel):
    pricing_tiers: list[dict[str, str]] = Field(default_factory=list)
    estimated_cac_usd: float | None = None
    estimated_ltv_usd: float | None = None
    target_mrr_6mo_usd: float | None = None
    target_arr_12mo_usd: float | None = None
    estimated_exit_multiple: float | None = None
    exit_readiness_score: float = Field(ge=0.0, le=10.0)
    notes: str = ""


class TaskItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    owner: AgentRole
    title: str
    description: str
    deadline_hours: int = 24
    status: Literal["pending", "in_progress", "done", "blocked"] = "pending"


class GTMPlan(BaseModel):
    primary_channel: str
    validation_experiment: str
    cold_outreach_audience: str | None = None
    estimated_validation_cost_usd: float = 0.0
    timeline_days: int = 7
    success_threshold: str


class CopyBundle(BaseModel):
    landing_headline: str
    landing_subheadline: str
    landing_cta: str
    cold_email_subject: str | None = None
    cold_email_body: str | None = None
    social_hooks: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    verdict: Literal["go", "no_go", "pivot"]
    rationale: str
    next_action: str
    confidence: float = Field(ge=0.0, le=1.0)


class AgentMessage(BaseModel):
    """One agent's contribution to the transcript (channel-routable later)."""

    role: AgentRole
    content: str
    channel_hint: str | None = None  # e.g. "#market-research"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── The state that flows through the LangGraph ───────────────────────────


class SprintState(BaseModel):
    """Single source of truth for one product sprint.

    Each agent node mutates a subset of fields; LangGraph merges via
    field-level reducers (lists accumulate, scalars overwrite).
    """

    model_config = {"arbitrary_types_allowed": True}

    # ── Identity ─────────────────────────────────────────────────────
    sprint_id: str = Field(default_factory=lambda: f"sprint-{uuid4().hex[:8]}")
    niche_hint: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Filled progressively by agents ───────────────────────────────
    hypothesis: str | None = None
    market_report: MarketReport | None = None
    prd: PRDDocument | None = None
    financial_model: FinancialModel | None = None
    tasks: Annotated[list[TaskItem], operator.add] = Field(default_factory=list)
    gtm_plan: GTMPlan | None = None
    copy_bundle: CopyBundle | None = None
    decision: Decision | None = None

    # ── Cross-cutting ────────────────────────────────────────────────
    messages: Annotated[list[AgentMessage], operator.add] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    status: SprintStatus = SprintStatus.PLANNING

    # ── Human-loop ───────────────────────────────────────────────────
    awaiting_reaction_message_id: int | None = None  # Discord message id
    human_decision: Literal["approved", "rejected"] | None = None
