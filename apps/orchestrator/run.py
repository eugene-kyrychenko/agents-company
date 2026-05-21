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

import typer
from rich.console import Console
from rich.logging import RichHandler

from apps.orchestrator.artifacts import dump_outputs
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

    out = dump_outputs(state)
    console.rule("[bold green]Sprint complete[/bold green]")
    console.print(f"Output: [bold]{out}[/bold]")
    console.print(f"Status: {state.status.value}")
    console.print(f"Spend today: ${state.total_cost_usd:.4f}")


if __name__ == "__main__":
    app()
