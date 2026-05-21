"""Discord-specific configuration loaded from environment.

Token resolution: each agent role reads `DISCORD_BOT_TOKEN_{ROLE}` from env.
If any token is missing, discord_layer.main raises clearly at boot rather
than failing mid-flight.
"""
from __future__ import annotations

import os
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

from apps.orchestrator.state import AgentRole


def _empty_str_to_none(v: object) -> object:
    """pydantic-settings reads '' for empty .env vars; coerce to None."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


OptInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_bot_token_ceo: str = ""
    discord_bot_token_coo: str = ""
    discord_bot_token_analyst: str = ""
    discord_bot_token_finance: str = ""
    discord_bot_token_growth: str = ""
    discord_bot_token_pmm: str = ""

    discord_guild_id: OptInt = None
    discord_founder_user_id: OptInt = None

    def token_for(self, role: AgentRole) -> str:
        attr = f"discord_bot_token_{role.value}"
        token = getattr(self, attr, "") or os.environ.get(attr.upper(), "")
        return token

    def all_tokens(self) -> dict[AgentRole, str]:
        return {role: self.token_for(role) for role in AgentRole}

    def missing_tokens(self) -> list[AgentRole]:
        return [r for r, t in self.all_tokens().items() if not t]


discord_settings = DiscordSettings()
