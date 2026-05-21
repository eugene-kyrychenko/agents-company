"""Cost ledger + circuit breaker.

Every LLM call records its tokens and $ here. When daily spend crosses
the configured cap, `is_idle()` flips True and the orchestrator stops
launching new sprint work until the next UTC day.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg

from apps.orchestrator.config import settings
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)


# Anthropic pricing (USD per 1M tokens) as of 2026-05.
# Used as a fallback when LiteLLM doesn't return cost in response.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # model_name -> (prompt_$/Mtok, completion_$/Mtok)
    "haiku-4-5": (0.25, 1.25),
    "sonnet-4-6": (3.00, 15.00),
    "opus-4-7": (15.00, 75.00),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p_rate, c_rate = MODEL_PRICING.get(model, (3.0, 15.0))  # default to sonnet pricing
    return (prompt_tokens * p_rate + completion_tokens * c_rate) / 1_000_000


class CostTracker:
    """Append-only ledger backed by Postgres."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.postgres_dsn

    @asynccontextmanager
    async def _conn(self):
        # psycopg3 sync connection wrapped — keeping the API async-friendly
        # for future swap to psycopg.AsyncConnection.
        conn = psycopg.connect(self.dsn, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()

    async def record(
        self,
        *,
        sprint_id: str | None,
        agent_role: AgentRole,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> float:
        if cost_usd is None:
            cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)

        async with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO cost_ledger
                    (sprint_id, agent_role, model, prompt_tokens,
                     completion_tokens, cached_tokens, cost_usd)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sprint_id,
                    agent_role.value,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cached_tokens,
                    Decimal(str(round(cost_usd, 6))),
                ),
            )
        return cost_usd

    async def spend_today_usd(self) -> float:
        today = date.today()
        async with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger "
                "WHERE occurred_at::date = %s",
                (today,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    async def spend_this_month_usd(self) -> float:
        now = datetime.now(timezone.utc)
        async with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger "
                "WHERE EXTRACT(YEAR FROM occurred_at) = %s "
                "AND EXTRACT(MONTH FROM occurred_at) = %s",
                (now.year, now.month),
            ).fetchone()
        return float(row[0]) if row else 0.0

    async def is_idle(self) -> bool:
        """Circuit breaker. True ⇒ stop launching new agent work."""
        spend = await self.spend_today_usd()
        if spend >= settings.daily_budget_usd:
            logger.warning(
                "Daily budget breached: $%.2f >= $%.2f — entering idle mode",
                spend,
                settings.daily_budget_usd,
            )
            return True
        return False
