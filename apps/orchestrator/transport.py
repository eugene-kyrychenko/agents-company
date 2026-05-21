"""Pluggable broadcast transport for agent messages.

Phase 1: ConsoleTransport prints to stdout + appends to SprintState.messages.
Phase 2: DiscordTransport replaces it without agents knowing.
"""
from __future__ import annotations

import logging
from typing import Protocol

from rich.console import Console
from rich.panel import Panel

from apps.orchestrator.permissions import assert_can_write
from apps.orchestrator.personas import persona_for
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)


class Transport(Protocol):
    async def post(self, role: AgentRole, channel: str, content: str) -> None: ...


class ConsoleTransport:
    """Phase 1 transport — pretty-prints to stdout, no Discord."""

    def __init__(self) -> None:
        self.console = Console()

    async def post(self, role: AgentRole, channel: str, content: str) -> None:
        assert_can_write(role, channel)
        p = persona_for(role)
        title = f"{p.emoji} {p.name.upper()} → #{channel}"
        self.console.print(
            Panel(content, title=title, border_style=p.rich_style, expand=False)
        )
