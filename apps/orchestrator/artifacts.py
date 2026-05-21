"""Sprint artifact persistence — state.json + report.md to outputs/.

Shared by both entry points (apps.orchestrator.run for CLI runs and
apps.orchestrator.listener for the long-running daemon). Keeps disk
layout identical regardless of how a sprint was started.
"""
from __future__ import annotations

from pathlib import Path

from apps.orchestrator.config import settings
from apps.orchestrator.state import SprintState


def dump_outputs(state: SprintState, output_dir: Path | None = None) -> Path:
    """Write state.json + report.md for one finished sprint."""
    base = (output_dir or settings.output_dir) / state.sprint_id
    base.mkdir(parents=True, exist_ok=True)

    (base / "state.json").write_text(
        state.model_dump_json(indent=2, exclude={"messages"}),
        encoding="utf-8",
    )
    (base / "report.md").write_text(render_markdown(state), encoding="utf-8")
    return base


def render_markdown(s: SprintState) -> str:
    lines = [
        f"# Sprint {s.sprint_id}",
        f"_Niche hint:_ {s.niche_hint or '(open exploration)'}",
        f"_Status:_ **{s.status.value}**",
        f"_Total spend:_ ${s.total_cost_usd:.4f}",
        "",
        "## Hypothesis",
        s.hypothesis or "_not produced_",
        "",
    ]
    if s.market_report:
        lines += [
            "## Market report",
            f"**Persona:** {s.market_report.target_persona}",
            "",
            "**Demand signals:**",
            *(f"- {sig}" for sig in s.market_report.demand_signals),
            "",
            f"**Market size:** {s.market_report.market_size_estimate or '—'}",
            "",
            "**Competitors:**",
            *(
                f"- [{c.name}]({c.url or '#'}) — {c.pricing or 'pricing N/A'}"
                for c in s.market_report.competitors
            ),
            "",
        ]
    if s.prd:
        lines += [
            "## PRD",
            f"**{s.prd.product_name}** — {s.prd.one_liner}",
            "",
            f"**Problem:** {s.prd.problem_statement}",
            "",
            f"**Target:** {s.prd.target_user}",
            "",
            "**MVP features:**",
            *(f"- [{f.priority}] **{f.name}** — {f.description}" for f in s.prd.mvp_features),
            "",
            f"**Excluded from MVP:** {', '.join(s.prd.excluded_from_mvp) or '—'}",
            "",
            f"**Success metric:** {s.prd.success_metric}",
            "",
        ]
    if s.financial_model:
        fm = s.financial_model
        lines += [
            "## Financial model",
            f"**Exit readiness:** {fm.exit_readiness_score:.1f}/10",
            f"**Target ARR (12mo):** ${fm.target_arr_12mo_usd or 0:,.0f}",
            f"**Exit multiple:** {fm.estimated_exit_multiple or 0:.1f}x ARR",
            f"**CAC:** ${fm.estimated_cac_usd or 0:.0f} • **LTV:** ${fm.estimated_ltv_usd or 0:.0f}",
            "",
            fm.notes,
            "",
        ]
    if s.gtm_plan:
        g = s.gtm_plan
        lines += [
            "## GTM plan",
            f"**Channel:** {g.primary_channel}",
            f"**Experiment:** {g.validation_experiment}",
            f"**Budget:** ${g.estimated_validation_cost_usd:.0f} over {g.timeline_days} days",
            f"**Success threshold:** {g.success_threshold}",
            "",
        ]
    if s.copy_bundle:
        c = s.copy_bundle
        lines += [
            "## Copy bundle",
            f"**Headline:** {c.landing_headline}",
            f"**Sub:** {c.landing_subheadline}",
            f"**CTA:** `{c.landing_cta}`",
            "",
            "**Social hooks:**",
            *(f"- {h}" for h in c.social_hooks),
            "",
        ]
    if s.decision:
        lines += [
            "## Decision",
            f"**Verdict:** {s.decision.verdict.upper()} (confidence {s.decision.confidence:.0%})",
            "",
            f"**Rationale:** {s.decision.rationale}",
            "",
            f"**Next action:** {s.decision.next_action}",
            "",
        ]
    return "\n".join(lines)
