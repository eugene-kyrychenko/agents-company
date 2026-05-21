"""Authoritative agent → channel write permissions.

This map is the *code-level* enforcement. Discord native role permissions
mirror it but are advisory — if they disagree, this file wins.

Channel names match Discord channel names exactly so a single string can
identify both.
"""
from __future__ import annotations

from apps.orchestrator.state import AgentRole

# Channels grouped by category, for documentation/reference only.
CHANNELS = {
    "directors_board": ["founder-decisions", "c-level-strategy"],
    "product_lab": ["market-research", "product-specifications"],
    "growth_marketing": ["growth-hacking", "content-factory"],
    "operations_logs": ["task-tracker", "system-logs"],
}

ALL_CHANNELS: set[str] = {ch for group in CHANNELS.values() for ch in group}


# Source of truth: what each agent is allowed to POST in.
# Founder is human and not in this map; founder reads everywhere, reacts in
# #founder-decisions.
ALLOWED_WRITE_CHANNELS: dict[AgentRole, frozenset[str]] = {
    AgentRole.CEO: frozenset({
        "founder-decisions",       # ONLY CEO writes here (besides founder reactions)
        "c-level-strategy",
        "market-research",
        "product-specifications",
        "growth-hacking",
        "task-tracker",
    }),
    AgentRole.COO: frozenset({
        "c-level-strategy",
        "market-research",
        "product-specifications",
        "task-tracker",
        "system-logs",
    }),
    AgentRole.ANALYST: frozenset({
        "market-research",
        "product-specifications",
        "task-tracker",
    }),
    AgentRole.FINANCE: frozenset({
        "c-level-strategy",
        "task-tracker",
    }),
    AgentRole.GROWTH: frozenset({
        "market-research",
        "growth-hacking",
        "content-factory",
        "task-tracker",
    }),
    AgentRole.PMM: frozenset({
        "growth-hacking",
        "content-factory",
        "task-tracker",
    }),
}


class PermissionError(Exception):
    """Raised when an agent attempts to write to a forbidden channel."""


def assert_can_write(role: AgentRole, channel: str) -> None:
    """Raise if `role` is not allowed to post in `channel`."""
    if channel not in ALL_CHANNELS:
        raise PermissionError(f"Unknown channel: {channel!r}")
    allowed = ALLOWED_WRITE_CHANNELS.get(role, frozenset())
    if channel not in allowed:
        raise PermissionError(
            f"Agent {role.value!r} cannot write to #{channel}. "
            f"Allowed: {sorted(allowed)}"
        )
