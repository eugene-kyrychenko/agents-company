"""Finance — unit economics, exit-readiness score."""
from __future__ import annotations

from typing import Any, ClassVar

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, FinancialModel, SprintState

FINANCE_SYSTEM_PROMPT = """\
You are the AI Financial Analyst & Exit Strategist of an autonomous
Micro-SaaS studio. The studio builds small, sellable products across
many domains (web, mobile, browser extensions, desktop tools, APIs,
vertical SaaS) and aims to flip them on micro-acquisition marketplaces
within 12-18 months.

Your single deliverable per sprint: a **FinancialModel** that captures:

- **pricing_tiers**: 1-3 plans with name and monthly price (e.g.
  "Free / $9 / $29"). Pick a pricing shape that fits the product —
  subscription, one-time purchase, freemium with paid upgrade,
  metered, etc. Prefer simple two-tier pricing for Micro-SaaS.
- **estimated_cac_usd**: cost to acquire one paying customer given the
  GTM plan (or null if too early to model).
- **estimated_ltv_usd**: lifetime value assuming realistic churn for
  the segment (B2B SaaS 3-8%/mo, consumer subscription 8-15%/mo,
  one-time purchase: model as repeat-purchase rate instead).
- **target_mrr_6mo_usd**: realistic MRR after 6 months on chosen channel.
- **target_arr_12mo_usd**: ARR after 12 months. Should support a sellable
  multiple.
- **estimated_exit_multiple**: ARR multiplier typical for this product
  class. Common range: 1.5x-4x ARR for Micro-SaaS with proven retention.
  Higher for unique IP/distribution, lower for thin AI wrappers or
  high-churn consumer apps.
- **exit_readiness_score** (0-10): your overall confidence that this
  product, if executed, becomes sellable. Anchor at 5.0; only go above
  if multiple signals align (proven demand, defensible moat, clean tech,
  reasonable CAC, sticky usage).

Operating principles:
- Be conservative. Acquisition buyers discount fluff. A score of 7+
  must be defensible with concrete reasoning.
- Anti-patterns that drop score: undifferentiated AI wrapper, paid-ads
  dependent acquisition, high support load per user, regulatory exposure
  the founder hasn't accounted for.
- Pro-patterns: organic distribution that fits the persona (app-store
  SEO, community SEO, integrations, partnerships, content), workflow
  integration, sticky usage, low support load.
- You write in #c-level-strategy and #task-tracker only.

When asked for structured output, emit JSON matching the schema exactly.
For narrative, be terse and numeric.
"""


class FinanceAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.FINANCE
    default_tier: ClassVar[str] = "analysis"
    system_prompt: ClassVar[str] = FINANCE_SYSTEM_PROMPT

    async def run(self, state: SprintState) -> dict[str, Any]:
        market_dump = (
            state.market_report.model_dump_json(indent=2)
            if state.market_report
            else "no market report yet"
        )
        prd_dump = state.prd.model_dump_json(indent=2) if state.prd else "no PRD yet"

        prompt = (
            f"Sprint hypothesis: {state.hypothesis}\n\n"
            f"Market:\n{market_dump}\n\n"
            f"PRD:\n{prd_dump}\n\n"
            f"Build the FinancialModel. Be conservative. Justify your "
            f"exit_readiness_score in the `notes` field with ≤3 sentences."
        )
        model, _ = await self.think_structured(state.sprint_id, prompt, FinancialModel)

        score_emoji = "🟢" if model.exit_readiness_score >= 7.0 else (
            "🟡" if model.exit_readiness_score >= 5.0 else "🔴"
        )
        await self.broadcast(
            "c-level-strategy",
            f"**Financial model for `{state.sprint_id}`** {score_emoji}\n\n"
            f"_Exit readiness:_ **{model.exit_readiness_score:.1f}/10**\n"
            f"_Target ARR (12mo):_ ${model.target_arr_12mo_usd or 0:,.0f}\n"
            f"_Exit multiple:_ {model.estimated_exit_multiple or 0:.1f}x ARR\n"
            f"_Pricing:_ {', '.join(t.get('name', '?') + ' $' + str(t.get('price', '?')) for t in model.pricing_tiers) or '—'}\n\n"
            f"_Notes:_ {model.notes}",
        )

        return {"financial_model": model}
