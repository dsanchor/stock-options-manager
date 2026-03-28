"""Context injection adapter for agent prompts.

Replaces the file-based logger.py read functions with CosmosDB-backed
equivalents.  Each decision is presented with its signal status so the
agent knows whether it was actionable.

Context format (oldest → newest, one block per decision):
    [2026-03-28 14:30:00] WAIT
    Premium marginal at 0.8%...

    [2026-03-28 16:30:00] SELL ⚡ SIGNAL
    IV Rank 72, premium 2.1%...
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.cosmos_db import CosmosDBService


class ContextProvider:
    """Provides per-symbol decision/signal context for agent prompts."""

    def __init__(self, cosmos: CosmosDBService) -> None:
        self.cosmos = cosmos

    def get_context(
        self,
        symbol: str,
        agent_type: str,
        max_entries: int = 2,
        position_id: str | None = None,
    ) -> str:
        """Return formatted context string for prompt injection.

        Fetches the last *max_entries* decisions for the given symbol and
        agent type.  Each decision includes its signal status (actionable
        or not).  Returned oldest-first for natural chronological reading.

        Args:
            symbol: Ticker symbol.
            agent_type: Agent type key.
            max_entries: Number of recent decisions (0–5, default 2).
            position_id: Optional position filter for monitor agents.

        Returns:
            Human-readable context string, or a "no history" message.
        """
        if max_entries <= 0:
            return "Context injection disabled."

        decisions = self.cosmos.get_recent_decisions(
            symbol, agent_type, max_entries, position_id
        )
        if not decisions:
            return "No previous decisions recorded."

        blocks: list[str] = []
        for d in reversed(decisions):  # newest-first → oldest-first
            ts = d.get("timestamp", "?")
            decision = d.get("decision", "?")
            is_signal = d.get("is_signal", False)

            header = f"[{ts}] {decision}"
            if is_signal:
                header += " ⚡ SIGNAL"

            reason = d.get("reason", "")
            if not reason:
                reason = json.dumps(
                    {k: v for k, v in d.items()
                     if k not in ("id", "symbol", "doc_type", "_rid",
                                  "_self", "_etag", "_attachments", "_ts")},
                    indent=2,
                )

            blocks.append(f"{header}\n{reason}")

        return "\n\n".join(blocks)
