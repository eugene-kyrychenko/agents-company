"""Language instructions injected into every agent's system prompt.

Goes at the TOP of the system prompt so it's covered by prompt caching —
as long as the language doesn't change between calls, the whole system
block is identical and the cache hits.
"""
from __future__ import annotations

LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "en": "",  # default: agents speak English (matches the system prompts)
    "uk": (
        "## Communication language\n"
        "\n"
        "Respond in **Ukrainian** for all narrative output: Discord posts, "
        "explanations, rationale, taglines, copy, social hooks, headlines, "
        "emails, descriptions, and any prose.\n"
        "\n"
        "Keep in English (do NOT translate):\n"
        "- JSON object keys / field names from any schema\n"
        "- Code, identifiers, file names, brand names, technical APIs\n"
        "- Established product names that already exist (GitHub, Chrome, Slack…)\n"
        "- Enum string values defined in the schema (e.g. 'go', 'must', 'pivot')\n"
        "\n"
        "Style: technical, sharp, conversational Ukrainian — like a senior "
        "remote engineer or product manager. Avoid overly formal/literary "
        "Ukrainian. Direct, no hedging, ≤6 sentences per paragraph.\n"
    ),
}


def language_preamble(language_code: str) -> str:
    """Return the preamble for the configured language, or empty string."""
    return LANGUAGE_INSTRUCTIONS.get(language_code, "")


# Non-English languages tokenize less efficiently — a literal Cyrillic or
# CJK response often uses 1.4-1.8x as many tokens as the equivalent English
# prose. Bumping max_tokens for those languages prevents the model from
# producing a JSON object that gets truncated mid-string (which then fails
# Pydantic validation and forces an expensive retry).
_TOKEN_BUDGET_MULTIPLIER: dict[str, float] = {
    "en": 1.0,
    "uk": 1.6,
}


def token_budget_multiplier(language_code: str) -> float:
    """How much to scale `per_message_token_limit` for a given language."""
    return _TOKEN_BUDGET_MULTIPLIER.get(language_code, 1.4)
