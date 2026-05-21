"""Reaction listener — 👍/👎 in #founder-decisions becomes a graph signal.

Flow:
    Founder reacts → CEO bot's raw_reaction_add fires →
    extract sprint_id from message body →
    publish JSON to Redis channel "sprint:{id}:decision" →
    persist decision in Postgres sprints registry.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

import discord
import psycopg
import redis.asyncio as aioredis

from apps.discord_layer.clients import MultiBotManager
from apps.discord_layer.config import discord_settings
from apps.orchestrator.config import settings
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)

APPROVE_EMOJI = "👍"
REJECT_EMOJI = "👎"
FOUNDER_DECISIONS_CHANNEL = "founder-decisions"

_SPRINT_ID_RE = re.compile(r"`(sprint-[a-f0-9]{8})`")


def _extract_sprint_id(message_text: str) -> str | None:
    """CEO's verdict message embeds the sprint id like `sprint-abcdef12`."""
    m = _SPRINT_ID_RE.search(message_text)
    return m.group(1) if m else None


class ReactionHandler:
    """Stateful wrapper around the reaction callback.

    Holds Redis connection + the channel id once known, so each event is
    cheap to process.
    """

    def __init__(self, manager: MultiBotManager) -> None:
        self.manager = manager
        self.redis: aioredis.Redis | None = None
        self._channel_id: int | None = None

    async def setup(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        channel = self.manager.find_channel(AgentRole.CEO, FOUNDER_DECISIONS_CHANNEL)
        self._channel_id = channel.id
        logger.info(
            "Reaction handler ready: watching channel=%d for 👍/👎",
            self._channel_id,
        )

    async def __call__(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.channel_id != self._channel_id:
            return
        emoji = str(payload.emoji)
        if emoji not in {APPROVE_EMOJI, REJECT_EMOJI}:
            return
        if payload.member is not None and payload.member.bot:
            return

        ceo_client = self.manager.clients[AgentRole.CEO]
        channel = ceo_client.get_channel(payload.channel_id)
        if channel is None:
            logger.warning("channel %d not in CEO bot cache", payload.channel_id)
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            logger.warning("message %d disappeared before reaction processed", payload.message_id)
            return

        sprint_id = _extract_sprint_id(message.content)
        if sprint_id is None:
            logger.info("reaction on non-verdict message %d ignored", payload.message_id)
            return

        decision: Literal["approved", "rejected"] = (
            "approved" if emoji == APPROVE_EMOJI else "rejected"
        )
        logger.info("Sprint %s: founder %s → %s", sprint_id, payload.user_id, decision)

        await self._persist(sprint_id, decision)
        if self.redis is not None:
            payload_json = json.dumps(
                {"sprint_id": sprint_id, "decision": decision, "user_id": payload.user_id}
            )
            await self.redis.publish(f"sprint:{sprint_id}:decision", payload_json)

    @staticmethod
    async def _persist(sprint_id: str, decision: str) -> None:
        with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO sprints (id, status, decision, finished_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE
                SET status = EXCLUDED.status,
                    decision = EXCLUDED.decision,
                    finished_at = NOW(),
                    updated_at = NOW()
                """,
                (sprint_id, decision, decision),
            )


def _founder_id_or_none() -> int | None:
    return discord_settings.discord_founder_user_id
