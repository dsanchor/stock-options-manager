import json
import os
from datetime import datetime


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs("logs", exist_ok=True)


def read_decision_log(log_path: str, max_entries: int = 20) -> str:
    """Read last N entries from the .jsonl decision log for agent context.

    Each line in the file is a JSON object. Returns a human-readable summary
    built from the ``reason`` field (or the full JSON when ``reason`` is
    absent) so the agent can use prior decisions as context.

    Args:
        log_path: Path to the .jsonl decision log file.
        max_entries: Maximum number of recent entries to return.

    Returns:
        Newline-separated string of recent decision summaries.
    """
    if not os.path.exists(log_path):
        return "No previous decisions recorded."

    try:
        with open(log_path, 'r') as f:
            lines = [l.strip() for l in f if l.strip()]

        recent = lines[-max_entries:]

        if not recent:
            return "No previous decisions recorded."

        summaries = []
        for line in recent:
            try:
                entry = json.loads(line)
                summaries.append(entry.get("reason", json.dumps(entry)))
            except json.JSONDecodeError:
                summaries.append(line)

        return "\n".join(summaries)
    except Exception as e:
        return f"Error reading decision log: {str(e)}"


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
