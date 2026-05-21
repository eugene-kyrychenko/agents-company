"""Idempotent Discord server provisioning.

Usage:
    uv run python -m apps.discord_layer.setup_server

Prerequisites (manual, in Discord Developer Portal — one-time):
    1. Create 6 applications: "Studio CEO", "Studio COO", "Studio Analyst",
       "Studio Finance", "Studio Growth", "Studio PMM". Each one gets a Bot.
    2. Under each Bot's settings, enable: PRESENCE INTENT, SERVER MEMBERS
       INTENT, MESSAGE CONTENT INTENT.
    3. Copy each bot token into .env (DISCORD_BOT_TOKEN_<ROLE>).
    4. Generate an OAuth2 URL for each bot with scopes `bot applications.commands`
       and permissions integer 268438544 (View Channels, Send Messages,
       Embed Links, Read Message History, Add Reactions). Invite all six
       to the SAME server.
    5. Note your DISCORD_GUILD_ID and DISCORD_FOUNDER_USER_ID, set them
       in .env.

What this script does (re-runnable):
    - Creates the role hierarchy: AI CEO, AI COO, AI Analyst, AI Finance,
      AI Growth, AI PMM, Founder (if missing).
    - Creates 4 categories and 8 channels (if missing).
    - Applies the per-channel permission matrix from
      `apps.orchestrator.permissions`.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from rich.console import Console

from apps.discord_layer.config import discord_settings
from apps.orchestrator.permissions import ALLOWED_WRITE_CHANNELS, CHANNELS
from apps.orchestrator.personas import PERSONAS
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)
console = Console()


ROLE_DISPLAY_NAMES: dict[AgentRole, str] = {
    role: p.discord_role for role, p in PERSONAS.items()
}

FOUNDER_ROLE_NAME = "Founder"

CATEGORY_LABELS: dict[str, str] = {
    "directors_board": "👑 Directors Board",
    "product_lab": "🔬 Product Lab",
    "growth_marketing": "📢 Growth & Marketing",
    "operations_logs": "⚙️ Operations & Logs",
}


def _readable_roles_for_channel(channel: str) -> set[AgentRole]:
    """Roles allowed to read a channel = anyone permitted to write there
    OR explicitly granted read-only (Founder reads everywhere)."""
    return {r for r, allowed in ALLOWED_WRITE_CHANNELS.items() if channel in allowed}


def _writable_roles_for_channel(channel: str) -> set[AgentRole]:
    return _readable_roles_for_channel(channel)


async def _ensure_roles(guild: discord.Guild) -> dict[AgentRole | str, discord.Role]:
    """Create missing AI agent roles + Founder role. Returns name → Role."""
    out: dict[AgentRole | str, discord.Role] = {}

    for role_enum, display in ROLE_DISPLAY_NAMES.items():
        existing = discord.utils.get(guild.roles, name=display)
        if existing:
            out[role_enum] = existing
            console.print(f"  • role [cyan]{display}[/cyan] exists")
        else:
            created = await guild.create_role(name=display, reason="AI Studio bootstrap")
            out[role_enum] = created
            console.print(f"  ✓ created role [green]{display}[/green]")

    founder = discord.utils.get(guild.roles, name=FOUNDER_ROLE_NAME)
    if founder is None:
        founder = await guild.create_role(name=FOUNDER_ROLE_NAME, reason="AI Studio bootstrap")
        console.print(f"  ✓ created role [green]{FOUNDER_ROLE_NAME}[/green]")
    else:
        console.print(f"  • role [cyan]{FOUNDER_ROLE_NAME}[/cyan] exists")
    out[FOUNDER_ROLE_NAME] = founder

    return out


async def _ensure_category(
    guild: discord.Guild, label: str
) -> discord.CategoryChannel:
    existing = discord.utils.get(guild.categories, name=label)
    if existing:
        return existing
    return await guild.create_category(label, reason="AI Studio bootstrap")


def _channel_overwrites(
    guild: discord.Guild,
    channel_name: str,
    roles: dict[AgentRole | str, discord.Role],
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Build the overwrites map enforcing the permission matrix."""
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}

    # Default: @everyone cannot see the channel.
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)

    writable_roles = _writable_roles_for_channel(channel_name)

    # AI agent roles: writable_roles can send; others cannot see.
    for role_enum, role_obj in roles.items():
        if not isinstance(role_enum, AgentRole):
            continue
        if role_enum in writable_roles:
            overwrites[role_obj] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
            )
        else:
            overwrites[role_obj] = discord.PermissionOverwrite(view_channel=False)

    # Founder always sees everything; writes only in the founder-* channels.
    founder = roles[FOUNDER_ROLE_NAME]
    founder_writable = {"founder-decisions", "founder-commands"}
    overwrites[founder] = discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=channel_name in founder_writable,
        add_reactions=True,
    )

    return overwrites


async def _ensure_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    roles: dict[AgentRole | str, discord.Role],
) -> discord.TextChannel:
    overwrites = _channel_overwrites(guild, name, roles)
    existing = discord.utils.get(guild.text_channels, name=name)
    if existing is None:
        ch = await guild.create_text_channel(
            name,
            category=category,
            overwrites=overwrites,
            reason="AI Studio bootstrap",
        )
        console.print(f"    ✓ #{name} created")
        return ch

    # Re-apply overwrites idempotently if drifted.
    if existing.category != category:
        await existing.edit(category=category, reason="AI Studio: move to correct category")
    await existing.edit(overwrites=overwrites, reason="AI Studio: re-apply permissions")
    console.print(f"    • #{name} exists (permissions reapplied)")
    return existing


async def provision_with(client: discord.Client) -> None:
    if discord_settings.discord_guild_id is None:
        console.print("[red]DISCORD_GUILD_ID is not set in .env[/red]")
        sys.exit(2)

    guild = client.get_guild(discord_settings.discord_guild_id)
    if guild is None:
        console.print(
            f"[red]Bot is not in guild {discord_settings.discord_guild_id}. "
            f"Invite it first.[/red]"
        )
        sys.exit(2)

    console.rule(f"[bold]Provisioning {guild.name} ({guild.id})[/bold]")

    console.print("[bold]1. Roles[/bold]")
    roles = await _ensure_roles(guild)

    console.print("[bold]2. Categories + channels[/bold]")
    for cat_key, channel_names in CHANNELS.items():
        category = await _ensure_category(guild, CATEGORY_LABELS[cat_key])
        console.print(f"  [bold]{CATEGORY_LABELS[cat_key]}[/bold]")
        for ch_name in channel_names:
            await _ensure_channel(guild, category, ch_name, roles)

    console.rule("[bold green]Done[/bold green]")
    console.print(
        "\nNext steps:\n"
        "  1. In Discord, assign the [bold]Founder[/bold] role to your own user.\n"
        "  2. For each agent bot, ensure the corresponding [bold]AI <ROLE>[/bold] role\n"
        "     is assigned to that bot's member (Discord may need you to do this manually\n"
        "     once per server: Members → bot → +role).\n"
    )


async def _main() -> None:
    if not discord_settings.discord_bot_token_ceo:
        console.print("[red]DISCORD_BOT_TOKEN_CEO not set — using CEO bot for provisioning.[/red]")
        sys.exit(2)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    client = discord.Client(intents=intents)
    done = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        try:
            await provision_with(client)
        finally:
            done.set()
            await client.close()

    task = asyncio.create_task(client.start(discord_settings.discord_bot_token_ceo))
    await done.wait()
    try:
        await task
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_main())
