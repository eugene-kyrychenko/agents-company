"""MultiBotManager — runs 6 discord.py clients in one process.

Each agent role gets its own Discord application/bot/token. They all
share an asyncio loop. start_all() blocks until every client emits
on_ready; stop_all() closes them cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord

from apps.discord_layer.config import discord_settings
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)


def _default_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.reactions = True
    intents.members = True
    return intents


class MultiBotManager:
    """Owns one `discord.Client` per agent role.

    Use as an async context manager:

        async with MultiBotManager() as mgr:
            channel = mgr.find_channel(AgentRole.CEO, "founder-decisions")
            await channel.send("hello")
    """

    def __init__(self, intents: discord.Intents | None = None) -> None:
        self.intents = intents or _default_intents()
        self.tokens = discord_settings.all_tokens()
        missing = discord_settings.missing_tokens()
        if missing:
            raise RuntimeError(
                f"Missing Discord tokens for: {[r.value for r in missing]}. "
                f"Set DISCORD_BOT_TOKEN_<ROLE> in .env."
            )

        self.clients: dict[AgentRole, discord.Client] = {}
        self._ready_events: dict[AgentRole, asyncio.Event] = {}
        self._run_tasks: list[asyncio.Task] = []
        # Late-bindable callbacks: assign before start_all() to wire handlers.
        self.on_reaction: (
            Callable[[discord.RawReactionActionEvent], Coroutine[Any, Any, None]] | None
        ) = None
        self.on_message: (
            Callable[[discord.Message], Coroutine[Any, Any, None]] | None
        ) = None

        for role in AgentRole:
            client = discord.Client(intents=self.intents)
            self.clients[role] = client
            self._ready_events[role] = asyncio.Event()
            self._wire_events(role, client)

    def _wire_events(self, role: AgentRole, client: discord.Client) -> None:
        @client.event
        async def on_ready() -> None:
            logger.info("Discord bot ready: %s as %s", role.value, client.user)
            self._ready_events[role].set()

        # Only the CEO bot listens to founder events — #founder-decisions
        # and #founder-commands are CEO-only channels, so other bots don't
        # need (or get) these events.
        if role is AgentRole.CEO:
            @client.event
            async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
                if self.on_reaction is not None:
                    await self.on_reaction(payload)

            @client.event
            async def on_message(message: discord.Message) -> None:
                if message.author.bot:
                    return
                if self.on_message is not None:
                    await self.on_message(message)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start_all(self, ready_timeout: float = 30.0) -> None:
        for role, client in self.clients.items():
            task = asyncio.create_task(
                client.start(self.tokens[role]),
                name=f"discord-bot-{role.value}",
            )
            self._run_tasks.append(task)

        try:
            await asyncio.wait_for(
                asyncio.gather(*(ev.wait() for ev in self._ready_events.values())),
                timeout=ready_timeout,
            )
        except TimeoutError as e:
            not_ready = [r.value for r, ev in self._ready_events.items() if not ev.is_set()]
            raise RuntimeError(f"Discord bots not ready in {ready_timeout}s: {not_ready}") from e

    async def stop_all(self) -> None:
        for client in self.clients.values():
            await client.close()
        for task in self._run_tasks:
            task.cancel()
        for task in self._run_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def __aenter__(self) -> MultiBotManager:
        await self.start_all()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.stop_all()

    # ── Lookups ────────────────────────────────────────────────────

    def guild_for(self, role: AgentRole) -> discord.Guild:
        if discord_settings.discord_guild_id is None:
            raise RuntimeError("DISCORD_GUILD_ID not set in .env")
        guild = self.clients[role].get_guild(discord_settings.discord_guild_id)
        if guild is None:
            raise RuntimeError(
                f"{role.value} bot is not in guild {discord_settings.discord_guild_id}. "
                f"Invite the bot to the server first."
            )
        return guild

    def find_channel(self, role: AgentRole, name: str) -> discord.TextChannel:
        guild = self.guild_for(role)
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is None:
            raise RuntimeError(f"#{name} not found in guild {guild.id}")
        return channel
