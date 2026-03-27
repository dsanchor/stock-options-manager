import json
import os
from datetime import datetime


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs("logs", exist_ok=True)


def _read_jsonl_log(log_path: str, max_entries: int, symbol: str | None,
                    empty_msg: str) -> str:
    """Read last N entries from a .jsonl log, optionally filtered by symbol.

    Args:
        log_path: Path to the .jsonl log file.
        max_entries: Maximum number of recent entries to return.
        symbol: If provided, only include entries whose ``symbol`` field
            matches (case-insensitive). The match checks the raw ``symbol``
            value as well as the ``EXCHANGE-SYMBOL`` composite format stored
            in config (e.g. ``"MO"`` matches entries with symbol ``"MO"``
            when the caller passes ``"MO"`` extracted from ``"NYSE-MO"``).
        empty_msg: Message returned when no matching entries are found.

    Returns:
        Newline-separated string of recent summaries.
    """
    if not os.path.exists(log_path):
        return empty_msg

    try:
        with open(log_path, 'r') as f:
            lines = [l.strip() for l in f if l.strip()]

        if not lines:
            return empty_msg

        # Filter by symbol when requested
        if symbol:
            sym_lower = symbol.lower()
            filtered: list[str] = []
            for line in lines:
                try:
                    entry = json.loads(line)
                    entry_sym = entry.get("symbol", "").lower()
                    if entry_sym == sym_lower:
                        filtered.append(line)
                except json.JSONDecodeError:
                    pass
            lines = filtered

        recent = lines[-max_entries:]

        if not recent:
            return empty_msg

        summaries = []
        for line in recent:
            try:
                entry = json.loads(line)
                summaries.append(entry.get("reason", json.dumps(entry)))
            except json.JSONDecodeError:
                summaries.append(line)

        return "\n".join(summaries)
    except Exception as e:
        return f"Error reading log: {str(e)}"


def read_decision_log(log_path: str, max_entries: int = 20,
                      symbol: str | None = None) -> str:
    """Read last N decision entries, optionally filtered by symbol."""
    return _read_jsonl_log(log_path, max_entries, symbol,
                           "No previous decisions recorded.")


def read_signal_log(log_path: str, max_entries: int = 10,
                    symbol: str | None = None) -> str:
    """Read last N signal entries, optionally filtered by symbol."""
    return _read_jsonl_log(log_path, max_entries, symbol,
                           "No previous signals recorded.")


def append_decision(log_path: str, json_data: dict):
    """Append a structured JSON decision to the .jsonl decision log.

    Each call writes one JSON object per line (JSON Lines format).

    Args:
        log_path: Path to the .jsonl decision log file.
        json_data: Dictionary to serialise as one JSON line.
    """
    ensure_logs_dir()

    with open(log_path, 'a') as f:
        f.write(json.dumps(json_data, separators=(',', ':')) + "\n")


def append_signal(signal_log_path: str, json_data: dict):
    """Append a structured JSON signal entry to the .jsonl signal log.

    Args:
        signal_log_path: Path to the .jsonl signal log file.
        json_data: Dictionary to serialise as one JSON line.
    """
    ensure_logs_dir()

    with open(signal_log_path, 'a') as f:
        f.write(json.dumps(json_data, separators=(',', ':')) + "\n")
