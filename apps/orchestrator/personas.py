"""Display identities for each agent role.

Naming follows the "Studio <Role>" pattern to match the Discord bot
application names. Source of truth — Discord bot names, role names,
console styling, and the self-identity injected into each system prompt
all read from here.
"""
from __future__ import annotations

from dataclasses import dataclass

from apps.orchestrator.state import AgentRole


@dataclass(frozen=True)
class Persona:
    role: AgentRole
    name: str               # "CEO" — shown in console panel title
    bot_username: str       # "Studio CEO" — matches Discord application name
    discord_role: str       # "AI CEO"   — server role used for permissions
    emoji: str
    rich_style: str         # ConsoleTransport panel border
    tagline: str            # one-liner identity prepended to the system prompt


PERSONAS: dict[AgentRole, Persona] = {
    AgentRole.CEO: Persona(
        role=AgentRole.CEO,
        name="CEO",
        bot_username="Studio CEO",
        discord_role="AI CEO",
        emoji="👑",
        rich_style="bold magenta",
        tagline="You are the AI CEO of the studio. You set direction and call Go/No-Go.",
    ),
    AgentRole.COO: Persona(
        role=AgentRole.COO,
        name="COO",
        bot_username="Studio COO",
        discord_role="AI COO",
        emoji="⚙️",
        rich_style="bold cyan",
        tagline="You are the AI COO. You hammer plans into executable tasks and keep the cadence.",
    ),
    AgentRole.ANALYST: Persona(
        role=AgentRole.ANALYST,
        name="Analyst",
        bot_username="Studio Analyst",
        discord_role="AI Analyst",
        emoji="🔬",
        rich_style="bold blue",
        tagline="You are the AI Business Analyst. You read the market and chart product direction.",
    ),
    AgentRole.FINANCE: Persona(
        role=AgentRole.FINANCE,
        name="Finance",
        bot_username="Studio Finance",
        discord_role="AI Finance",
        emoji="💰",
        rich_style="bold green",
        tagline="You are the AI Financial Analyst. You guard unit economics and judge exit potential.",
    ),
    AgentRole.GROWTH: Persona(
        role=AgentRole.GROWTH,
        name="Growth",
        bot_username="Studio Growth",
        discord_role="AI Growth",
        emoji="🚀",
        rich_style="bold yellow",
        tagline="You are the AI Growth Marketer. You design low-cost validation that proves demand fast.",
    ),
    AgentRole.PMM: Persona(
        role=AgentRole.PMM,
        name="PMM",
        bot_username="Studio PMM",
        discord_role="AI PMM",
        emoji="✍️",
        rich_style="bold red",
        tagline="You are the AI PMM / Copywriter. You shape the product's voice — sharp, technical, no fluff.",
    ),
}


def persona_for(role: AgentRole) -> Persona:
    return PERSONAS[role]
