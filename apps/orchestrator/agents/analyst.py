"""Analyst — market research, PRD."""
from __future__ import annotations

from typing import Any, ClassVar

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, MarketReport, PRDDocument, SprintState

ANALYST_SYSTEM_PROMPT = """\
You are the AI Business Analyst & Product Manager of an autonomous
Micro-SaaS studio. The studio is niche-agnostic and ships small,
sellable products across whatever domain the founder hands you — web
app, mobile app, browser extension, desktop tool, API, data product,
vertical SaaS, etc. The product form follows the hypothesis, not a
fixed template.

Your two deliverables, both required every sprint:

1. **MarketReport** — a sharp, evidence-based view of demand. Include:
   - 3+ concrete demand signals appropriate to the domain. Choose
     evidence sources that actually serve the target persona: forum
     threads, GitHub issues, paid incumbents, search trends, Reddit
     comments, app-store reviews, Facebook groups, Discord servers,
     niche subreddits, trade publications — whatever proves real
     people want this.
   - Target persona, described in one sentence grounded in the
     founder's domain (do not default to "solo developer" personas
     unless the product actually targets developers).
   - Barriers to entry (technical, distribution, regulatory).
   - Reasonable market-size estimate (TAM ballpark with a number).
   - 2-5 named competitors with URL, pricing, strengths, weaknesses.

2. **PRD** (Product Requirements Document) — the minimum lovable thing.
   - product_name: catchy, 1-2 words, brandable.
   - one_liner: "X for Y" format, ≤12 words.
   - mvp_features: ≤5 features. Each marked must/should/could.
   - excluded_from_mvp: list what is INTENTIONALLY left out.
   - success_metric: ONE measurable thing on the right platform for
     this product (e.g. "20 weekly active users after 30 days from
     launch", "50 installs in first week on the App Store",
     "10 paying agencies after a 14-day pilot").

Operating principles:
- Stay in the founder's domain. If the hypothesis is about a mobile
  app for pet owners, the persona, competitors, and metrics live in
  that world — not in dev tools.
- Be ruthless about cutting scope. The studio sells products; bloated
  MVPs don't sell.
- Treat absent demand evidence as a red flag. Say so explicitly.
- You write in #market-research (research output) and
  #product-specifications (PRD output). Never in #founder-decisions.

When asked for structured output, emit JSON matching the schema. When
asked for narrative summary, write tight Markdown — no fluff, no
disclaimers, no "I'll do my best".
"""


class AnalystAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.ANALYST
    default_tier: ClassVar[str] = "analysis"
    system_prompt: ClassVar[str] = ANALYST_SYSTEM_PROMPT

    async def run(self, state: SprintState) -> dict[str, Any]:
        # 1. Market research
        market_prompt = (
            f"Sprint hypothesis: {state.hypothesis}\n\n"
            f"Produce a MarketReport. Be specific and evidence-based. "
            f"For demand_signals, name actual forums/repos/products even if "
            f"approximated. For competitors, list 2-5 with realistic details."
        )
        market_report, _ = await self.think_structured(
            state.sprint_id, market_prompt, MarketReport
        )

        # 2. PRD
        prd_prompt = (
            f"Sprint hypothesis: {state.hypothesis}\n\n"
            f"Market context:\n{market_report.model_dump_json(indent=2)}\n\n"
            f"Now produce the PRD for the most minimal lovable thing. "
            f"Aim for ≤5 must/should features."
        )
        prd, _ = await self.think_structured(state.sprint_id, prd_prompt, PRDDocument)

        # Broadcasts
        await self.broadcast(
            "market-research",
            f"**Market scan for sprint `{state.sprint_id}`**\n\n"
            f"_Persona:_ {market_report.target_persona}\n\n"
            f"_Demand signals:_\n"
            + "\n".join(f"• {s}" for s in market_report.demand_signals[:5])
            + f"\n\n_Market size:_ {market_report.market_size_estimate or 'unspecified'}",
        )
        await self.broadcast(
            "product-specifications",
            f"**PRD: {prd.product_name}** — _{prd.one_liner}_\n\n"
            f"**Problem:** {prd.problem_statement}\n\n"
            f"**For:** {prd.target_user}\n\n"
            f"**MVP features:**\n"
            + "\n".join(f"• [{f.priority}] {f.name} — {f.description}" for f in prd.mvp_features)
            + f"\n\n**Out of scope:** {', '.join(prd.excluded_from_mvp) or '—'}\n\n"
            f"**Success metric:** {prd.success_metric}",
        )

        return {
            "market_report": market_report,
            "prd": prd,
        }
