"""BaseAgent — every persona inherits from this.

Responsibilities:
- Route LLM calls through LiteLLM (provider-agnostic).
- Enforce model tier per agent.
- Apply prompt caching to long system prompts (90% discount on Anthropic).
- Track tokens and $ per call.
- Provide structured output via Pydantic schemas.
- Broadcast human-readable messages via the configured Transport.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from apps.orchestrator.config import settings
from apps.orchestrator.cost_tracker import CostTracker
from apps.orchestrator.permissions import ALLOWED_WRITE_CHANNELS
from apps.orchestrator.state import AgentRole, SprintState
from apps.orchestrator.transport import Transport

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Anthropic prompt-caching threshold: cache blocks of >=1024 tokens.
# Below that, caching has no effect.
PROMPT_CACHE_MIN_CHARS = 4096  # ~1024 tokens approx


class BaseAgent(ABC):
    role: ClassVar[AgentRole]
    default_tier: ClassVar[str]  # "tactical" | "analysis" | "strategic"
    system_prompt: ClassVar[str]

    def __init__(self, transport: Transport, cost_tracker: CostTracker | None = None) -> None:
        self.transport = transport
        self.cost_tracker = cost_tracker or CostTracker()
        self.allowed_channels = ALLOWED_WRITE_CHANNELS[self.role]

    # ── LLM ────────────────────────────────────────────────────────

    @property
    def model_name(self) -> str:
        tier_to_model = {
            "tactical": settings.model_tier_tactical,
            "analysis": settings.model_tier_analysis,
            "strategic": settings.model_tier_strategic,
        }
        return tier_to_model[self.default_tier]

    def _build_messages(self, user_content: str) -> list[dict[str, Any]]:
        """System prompt is cached if long enough; user content never cached."""
        system_block: dict[str, Any] = {"type": "text", "text": self.system_prompt}
        if len(self.system_prompt) >= PROMPT_CACHE_MIN_CHARS:
            system_block["cache_control"] = {"type": "ephemeral"}

        return [
            {"role": "system", "content": [system_block]},
            {"role": "user", "content": user_content},
        ]

    async def _llm_call(
        self,
        *,
        sprint_id: str,
        user_content: str,
        response_format: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, float]:
        """Single LLM call routed through LiteLLM. Returns (text, cost_usd)."""
        max_tokens = max_tokens or settings.per_message_token_limit
        messages = self._build_messages(user_content)

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "api_base": settings.litellm_base_url,
            "api_key": settings.litellm_master_key,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception:
            logger.exception("LLM call failed for agent=%s", self.role.value)
            raise

        text = resp.choices[0].message.content or ""

        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        cached_tokens = 0
        if usage and hasattr(usage, "prompt_tokens_details"):
            details = usage.prompt_tokens_details
            cached_tokens = getattr(details, "cached_tokens", 0) or 0

        cost = await self.cost_tracker.record(
            sprint_id=sprint_id,
            agent_role=self.role,
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )
        logger.info(
            "agent=%s model=%s tokens=(p=%d c=%d cached=%d) cost=$%.4f",
            self.role.value,
            self.model_name,
            prompt_tokens,
            completion_tokens,
            cached_tokens,
            cost,
        )
        return text, cost

    async def think(self, sprint_id: str, prompt: str) -> tuple[str, float]:
        """Free-form text response."""
        return await self._llm_call(sprint_id=sprint_id, user_content=prompt)

    async def think_structured(
        self,
        sprint_id: str,
        prompt: str,
        schema: type[T],
        max_attempts: int = 2,
    ) -> tuple[T, float]:
        """Structured response validated against a Pydantic schema.

        We don't use LiteLLM's response_format because provider support is
        uneven across Haiku/Sonnet/Opus; instead we instruct the model to
        emit JSON and validate locally with a single retry on parse failure.
        """
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        instruction = (
            f"{prompt}\n\n"
            f"Respond ONLY with valid JSON matching this schema. "
            f"No prose, no markdown fences, no commentary.\n\n"
            f"Schema:\n{schema_json}"
        )

        total_cost = 0.0
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            text, cost = await self._llm_call(
                sprint_id=sprint_id, user_content=instruction
            )
            total_cost += cost
            cleaned = _strip_code_fence(text)
            try:
                return schema.model_validate_json(cleaned), total_cost
            except ValidationError as e:
                last_err = e
                logger.warning(
                    "Schema validation failed (attempt %d/%d) for %s: %s",
                    attempt + 1,
                    max_attempts,
                    schema.__name__,
                    e,
                )
                instruction = (
                    f"Previous response did not match the schema. "
                    f"Error: {e}\n\nRetry with valid JSON only.\n\n"
                    f"Schema:\n{schema_json}"
                )

        assert last_err is not None
        raise last_err

    # ── Broadcast ──────────────────────────────────────────────────

    async def broadcast(self, channel: str, content: str) -> None:
        if channel not in self.allowed_channels:
            raise PermissionError(
                f"{self.role.value} cannot broadcast in #{channel}"
            )
        await self.transport.post(self.role, channel, content)

    # ── Entry point — each persona implements this ─────────────────

    @abstractmethod
    async def run(self, state: SprintState) -> dict[str, Any]:
        """Mutate the sprint and return a partial state update for LangGraph."""


def _strip_code_fence(text: str) -> str:
    """Strip ```json ... ``` fences if the model wrapped its output."""
    text = text.strip()
    if text.startswith("```"):
        # Drop first line (``` or ```json) and trailing ```
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
