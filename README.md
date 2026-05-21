# agents-company

Autonomous multi-agent AI product studio that ships and sells Micro-SaaS
(Chrome extensions, dev tools) on marketplaces like Acquire.com.

One sprint = six AI personas (CEO → Analyst → Finance → COO → Growth → PMM
→ CEO decide) running through a LangGraph DAG, posting their work to
Discord channels, waiting for a founder verdict (👍/👎 reaction).
Target operating cost: $50–100 / month total LLM spend.

This README is the **developer onboarding** doc. For day-to-day operating
(launching sprints, reading verdicts) see the slash-commands in
`#founder-commands` (`/status`, `/list`, `/budget`).

---

## 1. Quick start

```bash
# 1. Python deps
uv sync

# 2. Fill in secrets
cp .env.example .env
# edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   DISCORD_BOT_TOKEN_CEO / _COO / _ANALYST / _FINANCE / _GROWTH / _PMM
#   TAVILY_API_KEY  (optional, for web search)

# 3. Start Postgres + Redis + LiteLLM + (optional) the Discord listener
./scripts/up.sh           # foreground
./scripts/up.sh --bg      # detached, logs to ./logs/listener.log
./scripts/down.sh         # stop everything; --keep-db to leave Docker running
./scripts/logs.sh         # tail listener logs

# 4. One-off sprint without Discord (useful for iterating on prompts)
uv run python -m apps.orchestrator.run --niche "Chrome extension for X"
uv run python -m apps.orchestrator.run -t discord -n "X"   # via Discord

# Tests
uv run pytest
```

`scripts/up.sh` brings up `postgres`, `redis`, `litellm` from
[docker-compose.yml](docker-compose.yml), waits for the LiteLLM
health-check, then launches the persistent listener that watches
`#founder-commands` for new ideas.

---

## 2. Architecture & data flow

### 2.1 The big picture

```
                      ┌──────────────────────────┐
   Founder types      │                          │
   idea in Discord ──►│      listener.py         │
   #founder-commands  │ (Listener daemon, 6 bots)│
                      │                          │
                      └────────────┬─────────────┘
                                   │ confirms with 🚀
                                   ▼
                      ┌──────────────────────────┐
                      │       graph.py           │
                      │   LangGraph StateGraph   │
                      │                          │
                      │  CEO → Analyst → Finance │
                      │   → COO → Growth → PMM   │
                      │   → CEO decide → END     │
                      └────────────┬─────────────┘
                                   │ every LLM call
                                   ▼
                      ┌──────────────────────────┐
                      │   LiteLLM proxy :4000    │
                      │ haiku-4-5 / sonnet-4-6 / │
                      │ opus-4-7  (Anthropic)    │
                      └────────────┬─────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       ┌────────────┐       ┌────────────┐       ┌────────────┐
       │ Postgres   │       │ Transport  │       │ CostTracker│
       │ - sprints  │       │ (Console / │       │ (circuit   │
       │ - kb       │       │  Discord)  │       │  breaker)  │
       │ - costs    │       └────────────┘       └────────────┘
       └────────────┘
```

### 2.2 Components

| Component | File | Responsibility |
|---|---|---|
| **Listener** | [listener.py](apps/orchestrator/listener.py) | Long-running daemon. Owns 6 Discord bots, watches `#founder-commands`, dispatches `/status` `/list` `/budget`, spawns sprints on 🚀 confirmation. Caps at 3 concurrent sprints. |
| **Graph** | [graph.py](apps/orchestrator/graph.py) | Builds the LangGraph state machine, wires nodes in fixed sequence. Uses `MemorySaver` checkpointer by default. |
| **SprintState** | [state.py](apps/orchestrator/state.py) | The pydantic model that flows through the graph. Single source of truth — Discord posts are projections, never the other way. Fields filled progressively: `hypothesis → market_report → prd → financial_model → tasks → gtm_plan → copy_bundle → decision`. |
| **BaseAgent** | [agents/base.py](apps/orchestrator/agents/base.py) | Common skeleton: LLM routing, prompt caching, structured-output validation, broadcast w/ permission check, cost recording. |
| **Personas** | [personas.py](apps/orchestrator/personas.py) | Display identity (name, emoji, Rich style, tagline, Discord bot username). Each agent's system prompt inherits its tagline from here. |
| **Transport** | [transport.py](apps/orchestrator/transport.py) | `Protocol` with one method `post(role, channel, content)`. Implemented by `ConsoleTransport` (Rich panel to stdout) and `DiscordTransport` (multi-bot post). Agents call `self.broadcast(...)` which routes through transport + asserts permissions. |
| **Permissions** | [permissions.py](apps/orchestrator/permissions.py) | `ALLOWED_WRITE_CHANNELS: dict[AgentRole, frozenset[str]]`. Code is the source of truth; Discord role permissions are advisory. `assert_can_write` raises `PermissionError`. |
| **CostTracker** | [cost_tracker.py](apps/orchestrator/cost_tracker.py) | Append-only Postgres ledger (`cost_ledger` table). `is_idle()` returns True when daily cap is hit → sprint launches refuse. Falls back to hard-coded per-model pricing if LiteLLM doesn't return cost. |
| **LiteLLM proxy** | [infra/litellm/config.yaml](infra/litellm/config.yaml) | Model aliases (`haiku-4-5`, `sonnet-4-6`, `opus-4-7`) → Anthropic. Redis cache + Haiku→Sonnet fallback. Master key from env. |
| **i18n** | [i18n.py](apps/orchestrator/i18n.py) | Prepended language preamble. `STUDIO_LANGUAGE=uk` switches narrative output to Ukrainian; JSON keys / schema enums stay English. Preamble sits inside the cached system block. |
| **Tools** | [tools/web_search.py](apps/orchestrator/tools/web_search.py), [tools/kb.py](apps/orchestrator/tools/kb.py) | Tavily search + Postgres knowledge base (cross-sprint memory). **Wired but not yet called by any agent** — placeholder for Phase 2 tool-use. |

### 2.3 Sprint lifecycle (control flow)

1. **Founder posts an idea** in `#founder-commands`. Listener replies with a confirmation message and adds 🚀 / ❌ reactions.
2. **Founder reacts 🚀.** Listener calls `build_graph(...)`, creates a fresh `SprintState(niche_hint=...)`, and schedules `graph.ainvoke()` as an asyncio task.
3. **LangGraph runs nodes sequentially.** Each node = `agent.run(state)` (or `agent.decide` for CEO's second pass). Node returns a partial state update; LangGraph merges (lists accumulate via `operator.add`, scalars overwrite).
4. **Each agent**:
   - Builds prompt (system = persona prompt + optional language preamble, user = task-specific).
   - Calls LiteLLM via `litellm.acompletion(model="openai/<alias>", api_base=...)` — `openai/` prefix tells the SDK we're hitting an OpenAI-compatible proxy, not Anthropic directly.
   - Validates structured output against a Pydantic schema (1 retry on parse failure).
   - Records tokens + $ via `CostTracker`.
   - Broadcasts a human-readable Markdown summary to its permitted channel(s).
5. **CEO `decide`** posts the verdict to `#founder-decisions` with 👍/👎 reactions. Graph ends; sprint status = `AWAITING_HUMAN`.
6. **Founder reacts** in `#founder-decisions`. `ReactionHandler` publishes the verdict to Redis Pub/Sub (`sprint:<id>:decision`) and updates the `sprints` table. (In one-shot CLI mode, `_wait_for_reaction` blocks on that channel up to `--wait-seconds`.)
7. **Artefacts dumped** to `outputs/<sprint_id>/` as `state.json` + `report.md`.

### 2.4 Model tiers and cost discipline

| Tier | Model alias | Per-1M tokens (in / out) | Used by |
|---|---|---|---|
| `tactical` | `haiku-4-5` | $0.25 / $1.25 | COO, Growth, PMM |
| `analysis` | `sonnet-4-6` | $3.00 / $15.00 | CEO, Analyst, Finance |
| `strategic` | `opus-4-7` | $15.00 / $75.00 | (reserved — not used in P1) |

Tier is chosen per-agent via `default_tier: ClassVar[str]`. To override globally, change the env vars `MODEL_TIER_TACTICAL` / `_ANALYSIS` / `_STRATEGIC`.

Three lines of defence against runaway spend:

- **`per_message_token_limit=2000`** — max completion tokens per call.
- **`daily_budget_usd=3.0`** (≈$90/mo) — `is_idle()` flips on breach, listener refuses new sprints, CLI exits 2.
- **Prompt caching** — system prompts ≥ 4096 chars get `cache_control: {type: ephemeral}`. The language preamble sits *inside* the cached block so a stable `STUDIO_LANGUAGE` keeps the cache warm. Caveat: at the moment most persona prompts are under 4 KB, so caching only kicks in for `uk` mode where the preamble pushes them over. Beef up the persona prompts if you want the discount everywhere.

### 2.5 Persistence

Three Postgres tables (see [infra/postgres/init.sql](infra/postgres/init.sql)):

- **`sprints`** — id, niche_hint, status, decision, timestamps. Written by the listener.
- **`cost_ledger`** — append-only LLM-call log (sprint, agent, model, tokens, cost). Read by `CostTracker.spend_today/_this_month`.
- **`kb_entries`** — cross-sprint memory (market signals, competitors, lessons). Phase-1 search is ILIKE; embeddings planned for later.

Redis is used for two things: LiteLLM response cache and Pub/Sub (`sprint:<id>:decision`).

LangGraph currently uses an in-memory checkpointer (`MemorySaver`). Swap to `langgraph-checkpoint-postgres` when you want sprint state to survive a listener restart — the dep is already in `pyproject.toml`.

---

## 3. Agents reference

Every agent inherits `BaseAgent` and supplies three class vars:

```python
class FooAgent(BaseAgent):
    role: ClassVar[AgentRole] = AgentRole.FOO
    default_tier: ClassVar[str] = "tactical" | "analysis" | "strategic"
    system_prompt: ClassVar[str] = """..."""

    async def run(self, state: SprintState) -> dict[str, Any]:
        ...  # return partial state update
```

Read these alongside the files below to see how prompts, schemas and channels line up.

### 3.1 CEO — `analysis` tier &nbsp;[agents/ceo.py](apps/orchestrator/agents/ceo.py)

- **Role:** opens each sprint with a one-sentence hypothesis; renders the final Go / No-Go / Pivot verdict against a rubric.
- **Runs twice in the graph:** `ceo.run` at the start (kickoff), `ceo.decide` at the end (verdict).
- **Deliverables:**
  - Kickoff → `{hypothesis, directive}` (loose JSON via `think`).
  - Verdict → `Decision { verdict: go|no_go|pivot, rationale, next_action, confidence }` (structured).
- **Decision rubric in the prompt:** GO requires ≥3 demand signals AND `exit_readiness_score ≥ 6` AND ≤5 must-have features. NO_GO on missing demand or `exit_readiness < 5`.
- **Writes to:** `founder-decisions` (verdict only), `founder-commands` (reply to founder), `c-level-strategy`, `market-research`, `product-specifications`, `growth-hacking`, `task-tracker`.

### 3.2 Analyst — `analysis` tier &nbsp;[agents/analyst.py](apps/orchestrator/agents/analyst.py)

- **Role:** market research + PRD authoring.
- **Deliverables (both required, both structured):**
  - `MarketReport { summary, demand_signals[], target_persona, market_size_estimate, barriers_to_entry[], competitors[Competitor], sources[] }`.
  - `PRDDocument { product_name, one_liner, problem_statement, target_user, mvp_features[PRDFeature], excluded_from_mvp[], success_metric }` — max 5 MVP features tagged `must`/`should`/`could`.
- **Operating constraints baked into prompt:** ruthless scope-cutting, prefer Chrome extensions & dev tools, treat absent evidence as a red flag.
- **Writes to:** `market-research`, `product-specifications`, `task-tracker`.

### 3.3 Finance — `analysis` tier &nbsp;[agents/finance.py](apps/orchestrator/agents/finance.py)

- **Role:** unit economics + exit-readiness score (anchored at 5.0/10, defensible at 7+).
- **Deliverable:** `FinancialModel { pricing_tiers[], estimated_cac_usd, estimated_ltv_usd, target_mrr_6mo_usd, target_arr_12mo_usd, estimated_exit_multiple, exit_readiness_score, notes }`.
- **Acquire.com-aware:** prompt enumerates pro-patterns (organic distribution, sticky usage, low support) and anti-patterns (paid-ads dependency, thin AI wrappers, regulatory exposure).
- **Writes to:** `c-level-strategy`, `task-tracker`.

### 3.4 COO — `tactical` tier (Haiku) &nbsp;[agents/coo.py](apps/orchestrator/agents/coo.py)

- **Role:** translates strategy into an executable task list. Does **not** invent direction.
- **Deliverable:** 4–8 `TaskItem { id, owner, title, description, deadline_hours, status }` in a `_TaskList` wrapper.
- **Operating constraints:** parallelise where possible (Analyst+Finance concurrent, Growth+PMM concurrent after PRD), per-task budget 1–6 h, kill anything not needed for the Go/No-Go.
- **Writes to:** `c-level-strategy`, `market-research`, `product-specifications`, `task-tracker`, `system-logs`.

### 3.5 Growth — `tactical` tier &nbsp;[agents/growth.py](apps/orchestrator/agents/growth.py)

- **Role:** design a validation experiment that produces a yes/no within 7 days, organic-only, **< $50 total spend**.
- **Deliverable:** `GTMPlan { primary_channel, validation_experiment, cold_outreach_audience, estimated_validation_cost_usd, timeline_days, success_threshold }`.
- **Channel allow-list in the prompt:** Chrome Web Store SEO, `/r/SaaS` Show & Tell, Product Hunt Tuesday, GitHub Awesome lists, Show HN, Indie Hackers. LinkedIn explicitly banned.
- **Writes to:** `market-research`, `growth-hacking`, `content-factory`, `task-tracker`.

### 3.6 PMM — `tactical` tier &nbsp;[agents/pmm.py](apps/orchestrator/agents/pmm.py)

- **Role:** product voice for the chosen GTM channel.
- **Deliverable:** `CopyBundle { landing_headline (≤9 words), landing_subheadline (≤18 words), landing_cta (1–3 words), cold_email_subject, cold_email_body (≤80 words), social_hooks[3–5] }`.
- **Voice rules in prompt:** outcome > feature, specific > clever, no "AI-powered" / "next-gen" / "revolutionary".
- **Writes to:** `growth-hacking`, `content-factory`.

### 3.7 Channel × agent permission matrix

| Channel | CEO | COO | Analyst | Finance | Growth | PMM |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| founder-decisions | ✅ | | | | | |
| founder-commands | ✅ | | | | | |
| c-level-strategy | ✅ | ✅ | | ✅ | | |
| market-research | ✅ | ✅ | ✅ | | ✅ | |
| product-specifications | ✅ | ✅ | ✅ | | | |
| growth-hacking | ✅ | | | | ✅ | ✅ |
| content-factory | | | | | ✅ | ✅ |
| task-tracker | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| system-logs | | ✅ | | | | |

Founder is human; reads everywhere, reacts (👍/👎/🚀/❌) in `#founder-decisions` and `#founder-commands`.

---

## 4. Adding a new agent

1. **Define the deliverable schema** in [state.py](apps/orchestrator/state.py) as a Pydantic model. Add the corresponding optional field to `SprintState`.
2. **Add a role** to `AgentRole` enum.
3. **Add a persona** to `PERSONAS` in [personas.py](apps/orchestrator/personas.py) — bot username, emoji, Rich style, tagline.
4. **Grant channel permissions** by adding an entry to `ALLOWED_WRITE_CHANNELS` in [permissions.py](apps/orchestrator/permissions.py). The agent will get a `PermissionError` on any broadcast outside this set.
5. **Create the agent file** in `apps/orchestrator/agents/<name>.py`. Subclass `BaseAgent`, set `role`/`default_tier`/`system_prompt`, implement `async def run(self, state) -> dict[str, Any]`.
6. **Wire into the graph** in [graph.py](apps/orchestrator/graph.py): add to `build_agents()`, `g.add_node(...)`, and the `g.add_edge(...)` sequence.
7. **(Discord transport only)** Create a new Discord application/bot for the role, add `DISCORD_BOT_TOKEN_<ROLE>` to `.env`, and add the matching server role + channel permissions on Discord — see `apps/discord_layer/setup_server.py` for the bootstrapper.
8. **Tests:** add a smoke test in `tests/` that calls `agent.run(SprintState(...))` with a fake transport.

---

## 5. Layout

```
apps/
  orchestrator/
    agents/         # one file per persona + base.py
    tools/          # web_search, kb (Phase-1: not yet called)
    config.py       # Settings (pydantic_settings)
    cost_tracker.py # $ ledger + circuit breaker
    graph.py        # LangGraph wiring
    i18n.py         # language preamble
    listener.py     # long-running daemon (Discord)
    permissions.py  # channel ACLs (source of truth)
    personas.py     # display identities
    run.py          # one-shot CLI
    state.py        # SprintState + sub-models
    transport.py    # Protocol + ConsoleTransport
  discord_layer/
    clients.py      # MultiBotManager (6 discord.py clients)
    transport.py    # DiscordTransport
    reactions.py    # 👍/👎 verdict handler → Redis
    setup_server.py # bootstrap channels, roles, permissions
    audit_server.py # compare server state to permissions.py
infra/
  litellm/config.yaml     # model aliases, fallbacks, cache
  postgres/init.sql       # sprints, cost_ledger, kb_entries
docker-compose.yml        # postgres, redis, litellm, langfuse
scripts/
  up.sh down.sh logs.sh
tests/
  test_smoke.py
  test_discord_layer.py
  e2e/test_dry_run_sprint.py
```
