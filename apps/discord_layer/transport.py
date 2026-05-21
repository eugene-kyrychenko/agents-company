"""DiscordTransport — Transport implementation that publishes via the
correct bot for each agent role.

Same interface as ConsoleTransport, so swapping is a one-line change in
the orchestrator's bootstrap.
"""
from __future__ import annotations

import logging

from apps.discord_layer.clients import MultiBotManager
from apps.orchestrator.permissions import assert_can_write
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1900  # Discord limit is 2000; leave headroom


def _chunk(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split on paragraph boundaries when possible, else hard-cut."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class DiscordTransport:
    def __init__(self, manager: MultiBotManager) -> None:
        self.manager = manager

    async def post(self, role: AgentRole, channel: str, content: str) -> None:
        assert_can_write(role, channel)
        ch = self.manager.find_channel(role, channel)
        for chunk in _chunk(content):
            await ch.send(chunk)
        logger.debug("posted %d chars by %s to #%s", len(content), role.value, channel)
