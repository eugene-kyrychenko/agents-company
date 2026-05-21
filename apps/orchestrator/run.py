"""CLI entry point for running one sprint end-to-end.

Usage:
    python -m apps.orchestrator.run --niche "Chrome extension for X"
    python -m apps.orchestrator.run                       # open exploration
    python -m apps.orchestrator.run -n "X" --transport discord    # post to Discord
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler

from apps.orchestrator.config import settings
from apps.orchestrator.cost_tracker import CostTracker
from apps.orchestrator.graph import build_graph
from apps.orchestrator.state import SprintState
from apps.orchestrator.transport import ConsoleTransport, Transport


class TransportChoice(str, enum.Enum):
    console = "console"
    discord = "discord"

app = typer.Typer(add_completion=False, no_args_is_help=False)
console = Console()


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


async def _run_sprint_with_transport(
    niche_hint: str | None,
    transport: Transport,
    cost_tracker: CostTracker,
) -> SprintState:
    if await cost_tracker.is_idle():
        spend = await cost_tracker.spend_today_usd()
        console.print(
            f"[bold red]Daily budget breached (${spend:.2f}). "
            f"Refusing to start sprint.[/bold red]"
        )
        raise typer.Exit(code=2)

    graph, _agents = build_graph(transport=transport, cost_tracker=cost_tracker)
    initial = SprintState(niche_hint=niche_hint)

    config = {"configurable": {"thread_id": initial.sprint_id}}
    console.rule(f"[bold]Sprint {initial.sprint_id}[/bold]")
    console.print(f"Niche hint: [italic]{niche_hint or '(open exploration)'}[/italic]")
    console.print()

    final_state_dict = await graph.ainvoke(initial, config=config)
    final_state = SprintState.model_validate(final_state_dict)
    final_state.total_cost_usd = await cost_tracker.spend_today_usd()
    return final_state


async def _run_console(niche_hint: str | None) -> SprintState:
    return await _run_sprint_with_transport(
        niche_hint, ConsoleTransport(), CostTracker()
    )


async def _run_discord(niche_hint: str | None, wait_for_reaction_seconds: int) -> SprintState:
    # Imported lazily so the console path doesn't require Discord env to load.
    from apps.discord_layer.clients import MultiBotManager
    from apps.discord_layer.reactions import ReactionHandler
    from apps.discord_layer.transport import DiscordTransport

    async with MultiBotManager() as manager:
        handler = ReactionHandler(manager)
        await handler.setup()
        manager.on_reaction = handler

        transport = DiscordTransport(manager)
        cost_tracker = CostTracker()
        state = await _run_sprint_with_transport(niche_hint, transport, cost_tracker)

        if wait_for_reaction_seconds > 0:
            console.print(
                f"\n[bold yellow]Waiting up to {wait_for_reaction_seconds}s "
                f"for founder reaction in #founder-decisions…[/bold yellow]"
            )
            await _wait_for_reaction(state.sprint_id, wait_for_reaction_seconds)

        return state


async def _wait_for_reaction(sprint_id: str, timeout_seconds: int) -> None:
    """Subscribe to Redis and block until the sprint's decision lands, or timeout."""
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(f"sprint:{sprint_id}:decision")

    try:
        async with asyncio.timeout(timeout_seconds):
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                payload = json.loads(msg["data"])
                console.print(
                    f"[bold]Founder decision:[/bold] {payload['decision'].upper()}"
                )
                return
    except TimeoutError:
        console.print("[dim]No reaction within timeout — sprint remains AWAITING_HUMAN.[/dim]")
    finally:
        await pubsub.close()
        await r.aclose()


def _dump_outputs(state: SprintState) -> Path:
    base = settings.output_dir / state.sprint_id
    base.mkdir(parents=True, exist_ok=True)

    (base / "state.json").write_text(
        state.model_dump_json(indent=2, exclude={"messages"}),
        encoding="utf-8",
    )

    md = _render_markdown(state)
    (base / "report.md").write_text(md, encoding="utf-8")

    return base


def _render_markdown(s: SprintState) -> str:
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


@app.command()
def main(
    niche: str | None = typer.Option(
        None, "--niche", "-n", help="Niche hint for the CEO to ground the sprint."
    ),
    transport: TransportChoice = typer.Option(
        TransportChoice.console,
        "--transport",
        "-t",
        help="Where agents publish their work. 'discord' requires bot tokens in .env.",
    ),
    wait_seconds: int = typer.Option(
        3600,
        "--wait-seconds",
        help="In discord mode, how long to wait for the founder's reaction.",
    ),
) -> None:
    """Run one product sprint end-to-end."""
    _configure_logging()
    if transport is TransportChoice.console:
        state = asyncio.run(_run_console(niche))
    else:
        state = asyncio.run(_run_discord(niche, wait_seconds))

    out = _dump_outputs(state)
    console.rule("[bold green]Sprint complete[/bold green]")
    console.print(f"Output: [bold]{out}[/bold]")
    console.print(f"Status: {state.status.value}")
    console.print(f"Spend today: ${state.total_cost_usd:.4f}")


if __name__ == "__main__":
    app()
