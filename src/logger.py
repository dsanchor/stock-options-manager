import os
from datetime import datetime
from typing import List


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs("logs", exist_ok=True)


def read_decision_log(log_path: str, max_entries: int = 20) -> str:
    """Read last N entries from decision log and return as string for agent context.
    
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
    """Append one-line decision summary to the decision log.
    
    Args:
        log_path: Path to the decision log file
        line: Decision summary line to append
    """
    ensure_logs_dir()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {line}\n"
    
    with open(log_path, 'a') as f:
        f.write(log_entry)


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
