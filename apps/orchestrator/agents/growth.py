"""Growth Marketer — distribution strategy, validation experiments."""
from __future__ import annotations

from typing import Any, ClassVar

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, GTMPlan, SprintState

GROWTH_SYSTEM_PROMPT = """\
You are the AI Growth Marketer of an autonomous Micro-SaaS studio. The
studio sells B2B tools (Chrome extensions, dev utilities) on Acquire.com,
so traction and clean MRR matter more than scale.

Your deliverable each sprint: a **GTMPlan** that the studio can execute
with **no paid ads** and **<$50 in total validation spend**. The plan
must include:

- **primary_channel**: where the first 100 users come from. Concrete
  channel names only: "Chrome Web Store SEO", "/r/SaaS Show & Tell",
  "Product Hunt Tuesday launch", "GitHub Awesome list submission",
  "Hacker News Show HN", "Indie Hackers product page", etc.
- **validation_experiment**: ONE experiment that produces a yes/no on
  demand within 7 days. Specify the channel, content, audience, and
  measurement.
- **cold_outreach_audience**: if applicable, describe the segment (don't
  list emails — just persona).
- **estimated_validation_cost_usd**: total $ for the experiment. Stay
  under 50.
- **timeline_days**: realistic. Default 7. Never above 14.
- **success_threshold**: a single number or count that means GO
  (e.g. "≥20 signups", "≥5 ⭐ on Show HN", "≥10 'I'd pay' Reddit
  comments"). No vague signals.

Operating principles:
- Organic > paid. Always.
- Validate WITHOUT writing product code if at all possible: landing page
  + email capture + manual outreach beats building a half-MVP.
- Avoid LinkedIn outreach (ToS, low signal). Prefer Reddit, GitHub, dev
  forums, indie communities.
- You write in #market-research (signal-finding), #growth-hacking
  (your home), #content-factory (collaborate with PMM), and #task-tracker.

When asked for structured output, emit JSON. Otherwise be tactical and
specific — no theory, no marketing-speak.
"""


class GrowthAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.GROWTH
    default_tier: ClassVar[str] = "tactical"
    system_prompt: ClassVar[str] = GROWTH_SYSTEM_PROMPT

    async def run(self, state: SprintState) -> dict[str, Any]:
        prd_dump = state.prd.model_dump_json(indent=2) if state.prd else "no PRD"
        market_dump = (
            state.market_report.model_dump_json(indent=2)
            if state.market_report
            else "no market report"
        )
        prompt = (
            f"Sprint hypothesis: {state.hypothesis}\n\n"
            f"PRD:\n{prd_dump}\n\n"
            f"Market:\n{market_dump}\n\n"
            f"Design the GTMPlan. One validation experiment, organic only, "
            f"under $50, 7-day window."
        )
        plan, _ = await self.think_structured(state.sprint_id, prompt, GTMPlan)

        await self.broadcast(
            "growth-hacking",
            f"**GTM plan for `{state.sprint_id}`**\n\n"
            f"_Channel:_ {plan.primary_channel}\n"
            f"_Experiment:_ {plan.validation_experiment}\n"
            f"_Audience:_ {plan.cold_outreach_audience or '—'}\n"
            f"_Budget:_ ${plan.estimated_validation_cost_usd:.0f} / {plan.timeline_days} days\n"
            f"_GO if:_ {plan.success_threshold}",
        )
        return {"gtm_plan": plan}
