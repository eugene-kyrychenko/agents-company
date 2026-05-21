"""Unit tests for discord_layer that don't require a live Discord."""
from __future__ import annotations

import pytest

from apps.discord_layer.reactions import _extract_sprint_id
from apps.discord_layer.transport import MAX_MESSAGE_CHARS, _chunk


def test_extract_sprint_id_from_verdict() -> None:
    """The regex must match the sprint_id embedded by CEO.decide()."""
    msg = (
        "🟢 **Verdict: GO** (confidence 80%)\n\n"
        "**Sprint:** `sprint-abc12345`\n"
        "**Hypothesis:** ...\n"
    )
    assert _extract_sprint_id(msg) == "sprint-abc12345"


def test_extract_sprint_id_returns_none_when_absent() -> None:
    assert _extract_sprint_id("just a random message") is None
    assert _extract_sprint_id("`sprint-not-hex!!`") is None  # bad chars


def test_chunk_short_message_passes_through() -> None:
    assert _chunk("hello") == ["hello"]


def test_chunk_breaks_on_paragraph_boundary() -> None:
    short = "A" * 1500
    long = "\n\n".join([short, short, short])
    chunks = _chunk(long)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= MAX_MESSAGE_CHARS


def test_chunk_falls_back_to_hard_cut_for_long_lines() -> None:
    blob = "x" * (MAX_MESSAGE_CHARS * 2 + 50)
    chunks = _chunk(blob)
    assert all(len(c) <= MAX_MESSAGE_CHARS for c in chunks)
    assert "".join(chunks) == blob


def test_multibot_manager_rejects_missing_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """If any DISCORD_BOT_TOKEN_<ROLE> is missing, MultiBotManager refuses to construct."""
    # Clear all Discord tokens
    for role in ("CEO", "COO", "ANALYST", "FINANCE", "GROWTH", "PMM"):
        monkeypatch.setenv(f"DISCORD_BOT_TOKEN_{role}", "")

    # Reload the discord_settings to pick up the empty env
    import importlib

    from apps.discord_layer import clients, config

    importlib.reload(config)
    importlib.reload(clients)

    with pytest.raises(RuntimeError, match="Missing Discord tokens"):
        clients.MultiBotManager()
