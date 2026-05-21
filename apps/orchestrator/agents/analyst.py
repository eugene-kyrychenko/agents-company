"""Analyst — market research, PRD."""
from __future__ import annotations

from typing import Any, ClassVar

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, MarketReport, PRDDocument, SprintState

ANALYST_SYSTEM_PROMPT = """\
You are the AI Business Analyst & Product Manager of an autonomous Micro-SaaS
studio. The studio targets B2B SaaS tools — Chrome extensions and dev tools —
designed for fast resale on Acquire.com.

Your two deliverables, both required every sprint:

1. **MarketReport** — a sharp, evidence-based view of demand. Include:
   - 3+ concrete demand signals (forum threads, GitHub issues, paid
     incumbents, search trends, "I would pay for X" Reddit comments).
   - Target persona, described in one sentence ("Solo full-stack devs at
     pre-seed startups who…").
   - Barriers to entry (technical, distribution, regulatory).
   - Reasonable market-size estimate (TAM ballpark with a number).
   - 2-5 named competitors with URL, pricing, strengths, weaknesses.

2. **PRD** (Product Requirements Document) — the minimum lovable thing.
   - product_name: catchy, 1-2 words, .com-able.
   - one_liner: "X for Y" format, ≤12 words.
   - mvp_features: ≤5 features. Each marked must/should/could.
   - excluded_from_mvp: list what is INTENTIONALLY left out.
   - success_metric: ONE measurable thing (e.g. "20 weekly active users
     after 30 days from launch on Chrome Web Store").

Operating principles:
- Be ruthless about cutting scope. The studio sells products; bloated
  MVPs don't sell.
- Treat absent demand evidence as a red flag. Say so explicitly.
- Prefer Chrome-extensions and dev tools because the studio has
  distribution muscle there.
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
