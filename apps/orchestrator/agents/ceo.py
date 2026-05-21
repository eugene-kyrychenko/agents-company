"""CEO — opens the sprint, makes Go/No-Go on the final hypothesis."""
from __future__ import annotations

import json
from typing import Any, ClassVar

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, Decision, SprintState, SprintStatus

CEO_SYSTEM_PROMPT = """\
You are the AI CEO of an autonomous product studio whose mission is to
build Micro-SaaS and small-software products engineered to be sold on
micro-acquisition marketplaces (Acquire.com, Flippa, MicroAcquire-style
brokers). The studio is niche-agnostic: take the founder's idea on its
own terms — B2B or B2C, web, mobile, browser extension, desktop, API,
data product, whatever fits the job-to-be-done implied by the hint.

Your responsibilities:
1. Open each sprint with a sharp, testable product hypothesis derived
   from the founder's niche hint. Keep the founder's domain — do not
   translate it into an unrelated vertical to match a template. The
   hypothesis is one sentence: "A [product form] for [persona] that
   [job-to-be-done], because [signal]."
2. At the end of the sprint, after Analyst, Finance, COO, Growth, and PMM
   have submitted their artifacts, render a Go / No-Go / Pivot verdict
   with one paragraph of rationale and a concrete next action.

Operating principles:
- Stay in the founder's domain. If the hint says "mobile app for pet
  owners", the hypothesis is a mobile app for pet owners — not a Chrome
  extension for vets. Pivot only after the team's artifacts justify it.
- Brevity beats prose. You are the kind of CEO who writes 3-line emails.
- Cost discipline: the entire studio runs on a $50-100/month LLM budget.
  Never propose hypotheses that require expensive validation experiments
  (paid ads >$50, manual sales effort >2hrs).
- Built-to-sell: every hypothesis must have a credible buyer profile on
  a micro-acquisition marketplace — solo founders, small agencies,
  niche operators, vertical SaaS buyers. Boring, profitable,
  low-support products win.
- No vapor: if Finance returns an exit_readiness_score < 5.0 or Analyst
  reports unclear demand signals, default to No-Go or Pivot.
- You speak in #founder-decisions (final verdicts only), #c-level-strategy
  (sprint kickoff and direction), #market-research, #product-specifications,
  #growth-hacking, and #task-tracker.

Decision rubric (when called to render a verdict):
- GO: market_report.demand_signals has ≥3 concrete examples AND
  financial_model.exit_readiness_score ≥ 6.0 AND prd.mvp_features has ≤5
  must-have items.
- PIVOT: demand signals exist but the proposed MVP misses the actual job
  or the financials don't justify build cost. State the specific pivot.
- NO_GO: no clear demand OR exit readiness < 5.0 OR no plausible
  distribution channel.

When asked to produce a hypothesis or decision, respond strictly in the
JSON schema requested. Otherwise respond in plain English, ≤6 sentences.
"""


class CEOAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.CEO
    default_tier: ClassVar[str] = "analysis"
    system_prompt: ClassVar[str] = CEO_SYSTEM_PROMPT

    async def run(self, state: SprintState) -> dict[str, Any]:
        """Kickoff phase: produce a hypothesis from the niche hint."""
        prompt = (
            f"Niche hint from the founder: {state.niche_hint or 'open exploration'}\n\n"
            f"Produce a single-sentence product hypothesis and a 2-3 sentence "
            f"directive for the team. Format your response as JSON:\n"
            f'{{"hypothesis": "...", "directive": "..."}}'
        )
        text, _ = await self.think(state.sprint_id, prompt)
        payload = _safe_json(text)
        hypothesis = payload.get("hypothesis", text.strip())
        directive = payload.get("directive", "")

        await self.broadcast(
            "c-level-strategy",
            f"**Sprint {state.sprint_id} kickoff.**\n\n"
            f"_Hypothesis:_ {hypothesis}\n\n"
            f"_Team directive:_ {directive}\n\n"
            f"@COO assign breakdown. @Analyst lead research. "
            f"@Finance benchmark exit multiples.",
        )

        return {"hypothesis": hypothesis, "status": SprintStatus.RESEARCHING}

    async def decide(self, state: SprintState) -> dict[str, Any]:
        """Closing phase: produce the Go/No-Go decision and post to #founder-decisions."""
        context = {
            "hypothesis": state.hypothesis,
            "market_report": state.market_report.model_dump() if state.market_report else None,
            "competitors_count": (
                len(state.market_report.competitors) if state.market_report else 0
            ),
            "prd": state.prd.model_dump() if state.prd else None,
            "financial_model": (
                state.financial_model.model_dump() if state.financial_model else None
            ),
            "gtm_plan": state.gtm_plan.model_dump() if state.gtm_plan else None,
            "copy_sample": state.copy_bundle.landing_headline if state.copy_bundle else None,
            "total_spend_usd": round(state.total_cost_usd, 4),
        }
        prompt = (
            "Final sprint review. Render your verdict applying the decision rubric.\n\n"
            f"Sprint context (JSON):\n{json.dumps(context, indent=2, default=str)}"
        )
        decision, _ = await self.think_structured(state.sprint_id, prompt, Decision)

        verdict_emoji = {"go": "🟢", "no_go": "🔴", "pivot": "🟡"}[decision.verdict]
        summary = (
            f"{verdict_emoji} **Verdict: {decision.verdict.upper()}** "
            f"(confidence {decision.confidence:.0%})\n\n"
            f"**Sprint:** `{state.sprint_id}`\n"
            f"**Hypothesis:** {state.hypothesis}\n\n"
            f"**Rationale:** {decision.rationale}\n\n"
            f"**Next action:** {decision.next_action}\n\n"
            f"_React with 👍 to approve, 👎 to reject._"
        )
        await self.broadcast("founder-decisions", summary)

        return {
            "decision": decision,
            "status": SprintStatus.AWAITING_HUMAN,
        }


def _safe_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction; returns {} on failure."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("```")
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
