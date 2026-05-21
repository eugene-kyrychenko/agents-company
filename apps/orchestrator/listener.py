"""Long-running daemon — keeps 6 Discord bots alive and listens for founder
commands in #founder-commands.

Flow:
    Founder types text in #founder-commands
       │
       ├──→ starts with "/" → dispatched as command (/status, /list, /budget)
       │
       └──→ otherwise treated as a niche hint:
            CEO bot posts a confirmation with 🚀 / ❌ reactions
              │
              ├── 🚀 → spawn a new sprint as an asyncio task
              └── ❌ → cancel

Run with:
    uv run python -m apps.orchestrator.listener
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import discord
import psycopg
import typer
from rich.console import Console
from rich.logging import RichHandler

from apps.discord_layer.clients import MultiBotManager
from apps.discord_layer.config import discord_settings
from apps.discord_layer.reactions import ReactionHandler
from apps.discord_layer.transport import DiscordTransport
from apps.orchestrator.config import settings
from apps.orchestrator.cost_tracker import CostTracker
from apps.orchestrator.graph import build_graph
from apps.orchestrator.state import AgentRole, SprintState

logger = logging.getLogger(__name__)
console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=False)


FOUNDER_COMMANDS_CHANNEL = "founder-commands"
CONFIRM_EMOJI = "🚀"
CANCEL_EMOJI = "❌"
CONFIRMATION_TIMEOUT_SECONDS = 300  # 5 minutes
MAX_CONCURRENT_SPRINTS = 3


@dataclass
class PendingConfirmation:
    confirm_message_id: int
    niche_text: str
    founder_user_id: int
    created_at: datetime = field(default_factory=datetime.utcnow)


# ════════════════════════════════════════════════════════════════════════
#  Listener
# ════════════════════════════════════════════════════════════════════════


class Listener:
    """Holds the lifecycle of the long-running studio process."""

    def __init__(self) -> None:
        self.manager = MultiBotManager()
        self.cost_tracker = CostTracker()
        self.transport = DiscordTransport(self.manager)
        self.verdict_handler = ReactionHandler(self.manager)

        self._pending: dict[int, PendingConfirmation] = {}
        self._active_sprints: dict[str, asyncio.Task] = {}
        self._commands_channel_id: int | None = None

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        # Wire callbacks before bots come up so events aren't missed.
        self.manager.on_reaction = self._on_reaction
        self.manager.on_message = self._on_message
        await self.manager.start_all()
        await self.verdict_handler.setup()

        commands_channel = self.manager.find_channel(
            AgentRole.CEO, FOUNDER_COMMANDS_CHANNEL
        )
        self._commands_channel_id = commands_channel.id
        logger.info(
            "Listener up. Watching #%s (id=%d). Type an idea to start a sprint.",
            FOUNDER_COMMANDS_CHANNEL,
            self._commands_channel_id,
        )

        # Boot greeting
        await commands_channel.send(
            "🟢 **Студія онлайн.**\n"
            "Напишіть ідею продукту — я запропоную запуск спринту.\n"
            "Команди: `/status`, `/list`, `/budget`.\n"
        )

        # Block forever — bots run in background tasks owned by the manager
        await asyncio.Event().wait()

    async def stop(self) -> None:
        # Cancel any in-flight sprints, then close bots.
        for task in self._active_sprints.values():
            task.cancel()
        for task in self._active_sprints.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.manager.stop_all()

    # ── Event routing ──────────────────────────────────────────────

    async def _on_message(self, message: discord.Message) -> None:
        if message.channel.id != self._commands_channel_id:
            return
        text = message.content.strip()
        if not text:
            return

        if text.startswith("/"):
            await self._handle_command(message, text)
        else:
            await self._handle_idea(message, text)

    async def _on_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        # Dispatch by channel: confirmations vs verdicts.
        if payload.channel_id == self._commands_channel_id:
            await self._handle_confirmation(payload)
        else:
            # Falls back to verdict handler (#founder-decisions).
            await self.verdict_handler(payload)

    # ── Idea confirmation flow ─────────────────────────────────────

    async def _handle_idea(self, message: discord.Message, niche: str) -> None:
        if len(self._active_sprints) >= MAX_CONCURRENT_SPRINTS:
            await message.reply(
                f"⚠️ Зараз вже {len(self._active_sprints)} спринтів у роботі "
                f"(максимум {MAX_CONCURRENT_SPRINTS}). Зачекайте завершення."
            )
            return

        if await self.cost_tracker.is_idle():
            spend = await self.cost_tracker.spend_today_usd()
            await message.reply(
                f"🛑 Денний бюджет вичерпано (${spend:.2f} / "
                f"${settings.daily_budget_usd:.2f}). Спринти заморожені до завтра."
            )
            return

        confirm = await message.channel.send(
            f"🟡 **Нова ідея для спринту**\n"
            f"> {niche[:1500]}\n\n"
            f"Поставте {CONFIRM_EMOJI} щоб запустити, {CANCEL_EMOJI} щоб скасувати. "
            f"Чекаю {CONFIRMATION_TIMEOUT_SECONDS // 60} хв."
        )
        await confirm.add_reaction(CONFIRM_EMOJI)
        await confirm.add_reaction(CANCEL_EMOJI)

        self._pending[confirm.id] = PendingConfirmation(
            confirm_message_id=confirm.id,
            niche_text=niche,
            founder_user_id=message.author.id,
        )

        # Schedule auto-expiry
        asyncio.create_task(self._expire_confirmation(confirm.id))

    async def _expire_confirmation(self, confirm_message_id: int) -> None:
        await asyncio.sleep(CONFIRMATION_TIMEOUT_SECONDS)
        pending = self._pending.pop(confirm_message_id, None)
        if pending is None:
            return
        try:
            ceo_client = self.manager.clients[AgentRole.CEO]
            ch = ceo_client.get_channel(self._commands_channel_id)
            if ch is None:
                return
            msg = await ch.fetch_message(confirm_message_id)
            await msg.edit(content=f"⏰ Час підтвердження вийшов. Ідею не запущено.\n> {pending.niche_text[:500]}")
        except discord.NotFound:
            pass
        except Exception:
            logger.exception("Failed to expire confirmation %d", confirm_message_id)

    async def _handle_confirmation(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.member and payload.member.bot:
            return
        pending = self._pending.get(payload.message_id)
        if pending is None:
            return
        emoji = str(payload.emoji)
        if emoji not in {CONFIRM_EMOJI, CANCEL_EMOJI}:
            return

        # Pop so duplicate reactions don't fire twice.
        self._pending.pop(payload.message_id, None)

        ceo_client = self.manager.clients[AgentRole.CEO]
        channel = ceo_client.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        if emoji == CANCEL_EMOJI:
            await msg.edit(content=f"❌ Скасовано.\n> {pending.niche_text[:500]}")
            return

        # Confirmed → spawn sprint
        sprint_task = asyncio.create_task(self._run_sprint(pending.niche_text))
        await msg.edit(
            content=f"✅ Спринт запущено.\n> {pending.niche_text[:500]}\n\n"
            f"_Активних спринтів: {len(self._active_sprints) + 1}_"
        )

    # ── Sprint orchestration ───────────────────────────────────────

    async def _run_sprint(self, niche_hint: str) -> None:
        graph, _ = build_graph(transport=self.transport, cost_tracker=self.cost_tracker)
        initial = SprintState(niche_hint=niche_hint)
        sprint_id = initial.sprint_id

        self._active_sprints[sprint_id] = asyncio.current_task()  # type: ignore[assignment]
        _upsert_sprint(sprint_id, niche_hint=niche_hint, status="researching")
        try:
            config = {"configurable": {"thread_id": sprint_id}}
            await graph.ainvoke(initial, config=config)
            _upsert_sprint(sprint_id, status="awaiting_human")
            logger.info("Sprint %s completed (awaiting founder verdict)", sprint_id)
        except Exception:
            logger.exception("Sprint %s failed", sprint_id)
            _upsert_sprint(sprint_id, status="failed")
            await self._post_to_commands(
                f"💥 Спринт `{sprint_id}` впав. Деталі в логах оркестратора."
            )
        finally:
            self._active_sprints.pop(sprint_id, None)

    async def _post_to_commands(self, content: str) -> None:
        ceo_client = self.manager.clients[AgentRole.CEO]
        if self._commands_channel_id is None:
            return
        ch = ceo_client.get_channel(self._commands_channel_id)
        if ch is None:
            return
        await ch.send(content)

    # ── Text commands ──────────────────────────────────────────────

    async def _handle_command(self, message: discord.Message, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().lstrip("/")
        if cmd == "status":
            await self._cmd_status(message.channel)
        elif cmd == "list":
            await self._cmd_list(message.channel)
        elif cmd == "budget":
            await self._cmd_budget(message.channel)
        else:
            await message.reply(
                f"Невідома команда `/{cmd}`. Доступні: `/status`, `/list`, `/budget`."
            )

    async def _cmd_status(self, channel: discord.TextChannel) -> None:
        if not self._active_sprints:
            await channel.send("💤 Зараз немає активних спринтів.")
            return
        lines = [f"**Активні спринти ({len(self._active_sprints)}):**"]
        for sprint_id in self._active_sprints:
            lines.append(f"• `{sprint_id}` — у роботі")
        spend = await self.cost_tracker.spend_today_usd()
        lines.append(
            f"\n_Витрачено сьогодні:_ ${spend:.4f} / ${settings.daily_budget_usd:.2f}"
        )
        await channel.send("\n".join(lines))

    async def _cmd_list(self, channel: discord.TextChannel) -> None:
        rows = _recent_sprints(limit=10)
        if not rows:
            await channel.send("📭 Поки історії спринтів немає.")
            return

        emoji_for = {"approved": "🟢", "rejected": "🔴", None: "🟡"}
        lines = ["**Останні 10 спринтів:**\n"]
        for r in rows:
            mark = emoji_for.get(r["decision"], "⚪")
            niche = (r["niche_hint"] or "—")[:60]
            lines.append(
                f"{mark} `{r['id']}` — {niche}  _({r['status']})_"
            )
        await channel.send("\n".join(lines))

    async def _cmd_budget(self, channel: discord.TextChannel) -> None:
        today = await self.cost_tracker.spend_today_usd()
        month = await self.cost_tracker.spend_this_month_usd()
        daily_pct = (today / settings.daily_budget_usd * 100) if settings.daily_budget_usd else 0
        monthly_pct = (month / settings.monthly_budget_usd * 100) if settings.monthly_budget_usd else 0
        await channel.send(
            f"💰 **Бюджет**\n"
            f"_Сьогодні:_ ${today:.4f} / ${settings.daily_budget_usd:.2f}  "
            f"({daily_pct:.1f}%)\n"
            f"_За місяць:_ ${month:.4f} / ${settings.monthly_budget_usd:.2f}  "
            f"({monthly_pct:.1f}%)"
        )


# ════════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════════


def _upsert_sprint(sprint_id: str, *, niche_hint: str | None = None, status: str) -> None:
    """Insert or update a row in the sprints registry."""
    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO sprints (id, niche_hint, status)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET status = EXCLUDED.status,
                niche_hint = COALESCE(EXCLUDED.niche_hint, sprints.niche_hint),
                updated_at = NOW()
            """,
            (sprint_id, niche_hint, status),
        )


def _recent_sprints(limit: int = 10) -> list[dict]:
    with psycopg.connect(settings.postgres_dsn, autocommit=True) as conn:
        rows = conn.execute(
            """
            SELECT id, niche_hint, status, decision, created_at
            FROM sprints
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "niche_hint": r[1],
            "status": r[2],
            "decision": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]


# ════════════════════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════════════════════


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


async def _main() -> None:
    listener = Listener()
    try:
        await listener.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping listener...[/yellow]")
    finally:
        await listener.stop()


@app.command()
def main() -> None:
    """Run the persistent studio listener."""
    _configure_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    app()
