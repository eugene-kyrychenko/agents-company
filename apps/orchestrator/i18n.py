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
