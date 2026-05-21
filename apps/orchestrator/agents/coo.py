"""COO — process orchestrator, task breakdown, system logs."""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from apps.orchestrator.agents.base import BaseAgent
from apps.orchestrator.state import AgentRole, SprintState, TaskItem

COO_SYSTEM_PROMPT = """\
You are the AI COO of an autonomous Micro-SaaS studio. You do not invent
strategy — you make it executable. You translate the CEO's directive and
the team's outputs into a concrete task list with owners and deadlines.

Your deliverable each sprint: a list of 4-8 **TaskItems**. Each task:

- `owner`: one of ceo, coo, analyst, finance, growth, pmm.
- `title`: imperative phrase, ≤8 words.
- `description`: ≤2 sentences. What "done" looks like.
- `deadline_hours`: integer, realistic. For a Micro-SaaS sprint the whole
  cycle finishes within 8-24 hours, so tasks should be 1-6 hours each.

Operating principles:
- No bloat. If a task isn't needed for the Go/No-Go decision, don't
  create it.
- Parallelize where possible: Analyst and Finance can run concurrently;
  Growth and PMM can run concurrently after PRD lands.
- Be ruthless about removing redundant work.
- You write in #c-level-strategy (high-level coordination),
  #market-research, #product-specifications, #task-tracker (your home),
  and #system-logs (health summaries).

When asked for structured output, emit JSON. For narrative, be ultra-
terse — bullet lists, no preamble.
"""


class _TaskList(BaseModel):
    tasks: list[TaskItem] = Field(default_factory=list)


class COOAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.COO
    default_tier: ClassVar[str] = "tactical"
    system_prompt: ClassVar[str] = COO_SYSTEM_PROMPT

    async def run(self, state: SprintState) -> dict[str, Any]:
        prompt = (
            f"Sprint hypothesis: {state.hypothesis}\n\n"
            f"PRD product_name: {state.prd.product_name if state.prd else '?'}\n"
            f"PRD one_liner: {state.prd.one_liner if state.prd else '?'}\n\n"
            f"Generate a TaskList for the remaining sprint work: "
            f"Growth's GTM design, PMM's copy production, and the CEO "
            f"decision call. 4-6 tasks total."
        )
        result, _ = await self.think_structured(state.sprint_id, prompt, _TaskList)
        tasks = result.tasks

        # Broadcast a Jira-style task list
        lines = [f"**Sprint `{state.sprint_id}` execution plan**\n"]
        for t in tasks:
            lines.append(
                f"`[{t.id}]` **@{t.owner.value}** — {t.title} "
                f"_({t.deadline_hours}h)_"
            )
        await self.broadcast("task-tracker", "\n".join(lines))

        await self.broadcast(
            "system-logs",
            f"`{state.sprint_id}` task plan published: {len(tasks)} items, "
            f"running spend ${state.total_cost_usd:.4f}",
        )

        return {"tasks": tasks}
