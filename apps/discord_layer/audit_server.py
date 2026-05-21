"""Inspect the Discord server state and (optionally) clean up orphan roles.

Usage (dry-run, no changes):
    uv run python -m apps.discord_layer.audit_server

Apply mode (deletes orphan theme-A roles):
    uv run python -m apps.discord_layer.audit_server --apply

What it does:
    1. Connects with the CEO bot token.
    2. Lists every role in the guild and categorises it.
    3. Reports missing bots, missing required roles, channel coverage.
    4. In --apply mode, deletes the orphan "Theme A" roles
       (AI Helm/Forge/Compass/Vault/Catalyst/Quill) safely — leaving
       managed bot roles and the Founder role alone.

Requirements:
    - DISCORD_BOT_TOKEN_CEO + DISCORD_GUILD_ID set in .env.
    - CEO bot must have `Manage Roles` permission to delete roles.
      (Just for this script — you can remove the permission afterward.)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from enum import StrEnum

import discord
import typer
from rich.console import Console
from rich.table import Table

from apps.discord_layer.config import discord_settings
from apps.orchestrator.permissions import ALL_CHANNELS
from apps.orchestrator.personas import PERSONAS

logger = logging.getLogger(__name__)
console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=False)


# Old "Theme A" names that should no longer exist on the server.
ORPHAN_ROLE_NAMES = frozenset(
    {"AI Helm", "AI Forge", "AI Compass", "AI Vault", "AI Catalyst", "AI Quill"}
)

# Current expected manual roles.
EXPECTED_MANUAL_ROLES = frozenset(p.discord_role for p in PERSONAS.values()) | {"Founder"}

# Current expected bot usernames (managed roles will carry these names).
EXPECTED_BOT_USERNAMES = frozenset(p.bot_username for p in PERSONAS.values())


class RoleKind(StrEnum):
    SYSTEM = "system"            # @everyone
    MANAGED_BOT = "managed-bot"  # auto-created by Discord per bot
    EXPECTED_MANUAL = "expected" # AI CEO / AI COO / ... / Founder
    ORPHAN_THEME_A = "orphan"    # AI Helm / Forge / ...
    UNKNOWN = "unknown"


@dataclass
class RoleReport:
    name: str
    kind: RoleKind
    member_count: int
    role_id: int


@dataclass
class AuditReport:
    guild_name: str
    guild_id: int
    roles: list[RoleReport] = field(default_factory=list)
    bots_present: set[str] = field(default_factory=set)
    bots_missing: set[str] = field(default_factory=set)
    channels_present: set[str] = field(default_factory=set)
    channels_missing: set[str] = field(default_factory=set)


def _classify_role(role: discord.Role) -> RoleKind:
    if role.is_default():
        return RoleKind.SYSTEM
    if role.managed:
        return RoleKind.MANAGED_BOT
    if role.name in ORPHAN_ROLE_NAMES:
        return RoleKind.ORPHAN_THEME_A
    if role.name in EXPECTED_MANUAL_ROLES:
        return RoleKind.EXPECTED_MANUAL
    return RoleKind.UNKNOWN


def _audit(guild: discord.Guild) -> AuditReport:
    rep = AuditReport(guild_name=guild.name, guild_id=guild.id)

    for role in sorted(guild.roles, key=lambda r: -r.position):
        rep.roles.append(
            RoleReport(
                name=role.name,
                kind=_classify_role(role),
                member_count=len(role.members),
                role_id=role.id,
            )
        )

    # Bot presence — detect via managed role names (a bot's managed role
    # carries the same name as its Discord application, regardless of the
    # bot's user.name which post-username-migration is often slugified).
    managed_role_names = {r.name for r in guild.roles if r.managed}
    rep.bots_present = managed_role_names & EXPECTED_BOT_USERNAMES
    rep.bots_missing = EXPECTED_BOT_USERNAMES - managed_role_names

    # Channel coverage
    channel_names = {c.name for c in guild.text_channels}
    rep.channels_present = ALL_CHANNELS & channel_names
    rep.channels_missing = ALL_CHANNELS - channel_names

    return rep


def _print_report(rep: AuditReport) -> None:
    console.rule(f"[bold]{rep.guild_name}[/bold]  ({rep.guild_id})")

    # ── Roles ─────────────────────────────────────────────────────
    table = Table(title="Roles", show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Members", justify="right")

    style_for_kind = {
        RoleKind.SYSTEM: "dim",
        RoleKind.MANAGED_BOT: "cyan",
        RoleKind.EXPECTED_MANUAL: "green",
        RoleKind.ORPHAN_THEME_A: "bold red",
        RoleKind.UNKNOWN: "yellow",
    }

    for r in rep.roles:
        style = style_for_kind.get(r.kind, "white")
        table.add_row(
            f"[{style}]{r.name}[/{style}]",
            r.kind.value,
            str(r.member_count),
        )
    console.print(table)

    orphans = [r for r in rep.roles if r.kind is RoleKind.ORPHAN_THEME_A]
    if orphans:
        console.print(
            f"\n[bold red]{len(orphans)} orphan role(s) detected:[/bold red] "
            + ", ".join(r.name for r in orphans)
        )
        console.print("[dim]Run again with --apply to delete them.[/dim]")
    else:
        console.print("\n[bold green]✓ No orphan roles[/bold green]")

    unknown = [r for r in rep.roles if r.kind is RoleKind.UNKNOWN]
    if unknown:
        console.print(
            f"[yellow]{len(unknown)} unrecognised role(s):[/yellow] "
            + ", ".join(r.name for r in unknown)
            + "[dim] — left as-is (not touched).[/dim]"
        )

    # ── Bot presence ──────────────────────────────────────────────
    console.print()
    console.print(f"[bold]Bots present:[/bold] {len(rep.bots_present)} / 6")
    for name in sorted(EXPECTED_BOT_USERNAMES):
        mark = "[green]✓[/green]" if name in rep.bots_present else "[red]✗ missing[/red]"
        console.print(f"  {mark} {name}")

    # Unknown bots
    all_bots_in_guild = {r.name for r in rep.roles if r.kind is RoleKind.MANAGED_BOT}
    unexpected_bots = all_bots_in_guild - EXPECTED_BOT_USERNAMES
    if unexpected_bots:
        console.print(
            f"\n[yellow]Bots in server but not in personas.PERSONAS:[/yellow] "
            + ", ".join(sorted(unexpected_bots))
        )
        console.print(
            "[dim]→ Either rename in Dev Portal or update personas.PERSONAS[/dim]"
        )

    # ── Channels ──────────────────────────────────────────────────
    console.print()
    console.print(f"[bold]Channels present:[/bold] {len(rep.channels_present)} / 8")
    for name in sorted(ALL_CHANNELS):
        mark = "[green]✓[/green]" if name in rep.channels_present else "[red]✗ missing[/red]"
        console.print(f"  {mark} #{name}")

    if rep.channels_missing:
        console.print(
            "\n[yellow]Some channels missing — run setup_server.py.[/yellow]"
        )


async def _delete_orphans(guild: discord.Guild, rep: AuditReport) -> int:
    deleted = 0
    for r in rep.roles:
        if r.kind is not RoleKind.ORPHAN_THEME_A:
            continue
        role_obj = guild.get_role(r.role_id)
        if role_obj is None:
            continue
        try:
            await role_obj.delete(reason="AI Studio audit: orphan theme-A role")
            console.print(f"  [bold red]✗ deleted[/bold red] {r.name}")
            deleted += 1
        except discord.Forbidden:
            console.print(
                f"  [yellow]! cannot delete {r.name} — bot needs 'Manage Roles' permission[/yellow]"
            )
        except Exception as e:
            console.print(f"  [red]error deleting {r.name}: {e}[/red]")
    return deleted


async def _run(apply_changes: bool) -> None:
    if not discord_settings.discord_bot_token_ceo:
        console.print("[red]DISCORD_BOT_TOKEN_CEO not set in .env[/red]")
        sys.exit(2)
    if discord_settings.discord_guild_id is None:
        console.print("[red]DISCORD_GUILD_ID not set in .env[/red]")
        sys.exit(2)

    intents = discord.Intents.default()
    intents.members = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(discord_settings.discord_guild_id)
            if guild is None:
                console.print(
                    f"[red]Bot not in guild {discord_settings.discord_guild_id}[/red]"
                )
                return

            rep = _audit(guild)
            _print_report(rep)

            if apply_changes:
                orphans = [r for r in rep.roles if r.kind is RoleKind.ORPHAN_THEME_A]
                if orphans:
                    console.rule("[bold red]Deleting orphan roles[/bold red]")
                    n = await _delete_orphans(guild, rep)
                    console.print(f"\n[bold]Deleted {n} role(s).[/bold]")
                else:
                    console.print("\n[bold]Nothing to delete.[/bold]")
        finally:
            await client.close()

    # client.start blocks until client.close() is called inside on_ready;
    # awaiting it here ensures the aiohttp connector closes cleanly.
    await client.start(discord_settings.discord_bot_token_ceo)


@app.command()
def main(
    apply_changes: bool = typer.Option(
        False,
        "--apply",
        help="Actually delete orphan roles. Without this flag, dry-run only.",
    ),
) -> None:
    """Audit the Discord server and optionally remove orphan theme-A roles."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    asyncio.run(_run(apply_changes))


if __name__ == "__main__":
    app()
