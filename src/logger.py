import json
import os
from datetime import datetime
from typing import List, Optional


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs("logs", exist_ok=True)


def _jsonl_path(decision_log_path: str) -> str:
    """Derive .jsonl file path from a decision log path.
    
    Example: "logs/covered_call_decisions.log" → "logs/covered_call_decisions.jsonl"
    """
    base, _ = os.path.splitext(decision_log_path)
    return f"{base}.jsonl"


def read_decision_log(log_path: str, max_entries: int = 20) -> str:
    """Read last N entries from decision log and return as string for agent context.
    
    Reads from the human-readable .log file (SUMMARY lines). Falls back gracefully
    if the file doesn't exist yet.
    
    Args:
        log_path: Path to the decision log file
        max_entries: Maximum number of recent entries to read
        
    Returns:
        String containing the last N log entries, one per line
    """
    if not os.path.exists(log_path):
        return "No previous decisions recorded."
    
    try:
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        # Get last max_entries lines
        recent_lines = lines[-max_entries:] if len(lines) > max_entries else lines
        
        if not recent_lines:
            return "No previous decisions recorded."
        
        return "".join(recent_lines).strip()
    except Exception as e:
        return f"Error reading decision log: {str(e)}"


def append_decision(log_path: str, line: str):
    """Append one-line decision summary (SUMMARY or legacy format) to the decision log.
    
    Args:
        log_path: Path to the decision log file
        line: Decision summary line to append
    """
    ensure_logs_dir()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {line}\n"
    
    with open(log_path, 'a') as f:
        f.write(log_entry)


def append_decision_json(log_path: str, json_data: dict):
    """Write a structured JSON decision to the .jsonl companion file.
    
    The .jsonl path is derived from the decision_log_path by changing the
    extension (e.g., logs/covered_call_decisions.log → .jsonl).
    Each call appends one JSON object per line (JSON Lines format).
    
    Args:
        log_path: Path to the *decision log* (.log) file — the .jsonl
                  path is derived automatically.
        json_data: Dictionary to serialise as one JSON line.
    """
    ensure_logs_dir()
    
    jsonl_file = _jsonl_path(log_path)
    with open(jsonl_file, 'a') as f:
        f.write(json.dumps(json_data, separators=(',', ':')) + "\n")


def append_signal(signal_log_path: str, line: str):
    """Append sell signal entry to the signal log.
    
    Args:
        signal_log_path: Path to the signal log file
        line: Signal line to append
    """
    ensure_logs_dir()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {line}\n"
    
    with open(signal_log_path, 'a') as f:
        f.write(log_entry)
