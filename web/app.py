import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from croniter import croniter
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Agent type registry — maps URL slugs to file paths and metadata
# ---------------------------------------------------------------------------
AGENT_TYPES = {
    "open_call_monitor": {
        "label": "Open Call Monitor",
        "source_file": "data/opened_calls.txt",
        "decision_log": "logs/open_call_monitor_decisions.jsonl",
        "signal_log": "logs/open_call_monitor_signals.jsonl",
        "is_position_monitor": True,
    },
    "open_put_monitor": {
        "label": "Open Put Monitor",
        "source_file": "data/opened_puts.txt",
        "decision_log": "logs/open_put_monitor_decisions.jsonl",
        "signal_log": "logs/open_put_monitor_signals.jsonl",
        "is_position_monitor": True,
    },
    "covered_call": {
        "label": "Following · Covered Call",
        "source_file": "data/covered_call_symbols.txt",
        "decision_log": "logs/covered_call_decisions.jsonl",
        "signal_log": "logs/covered_call_signals.jsonl",
        "is_position_monitor": False,
    },
    "cash_secured_put": {
        "label": "Following · Cash-Secured Put",
        "source_file": "data/cash_secured_put_symbols.txt",
        "decision_log": "logs/cash_secured_put_decisions.jsonl",
        "signal_log": "logs/cash_secured_put_signals.jsonl",
        "is_position_monitor": False,
    },
}

DATA_FILES = {
    "covered_call_symbols": {
        "path": "data/covered_call_symbols.txt",
        "label": "Covered Call Symbols",
        "hint": "One symbol per line in EXCHANGE-SYMBOL format (e.g., NYSE-MO, NASDAQ-AAPL). Lines starting with # are comments.",
    },
    "cash_secured_put_symbols": {
        "path": "data/cash_secured_put_symbols.txt",
        "label": "Cash-Secured Put Symbols",
        "hint": "One symbol per line in EXCHANGE-SYMBOL format (e.g., NYSE-GIS, NASDAQ-MSFT). Lines starting with # are comments.",
    },
    "opened_calls": {
        "path": "data/opened_calls.txt",
        "label": "Open Call Positions",
        "hint": "One position per line: EXCHANGE-SYMBOL,strike,expiration (e.g., NYSE-MO,72,2026-04-24). Lines starting with # are comments.",
    },
    "opened_puts": {
        "path": "data/opened_puts.txt",
        "label": "Open Put Positions",
        "hint": "One position per line: EXCHANGE-SYMBOL,strike,expiration (e.g., NASDAQ-MSFT,340,2026-04-10). Lines starting with # are comments.",
    },
}

# ---------------------------------------------------------------------------
# JSONL / config utilities
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load raw config.yaml without env-var substitution (web doesn't need secrets)."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    """Read a JSONL file and return a list of dicts."""
    full_path = PROJECT_ROOT / path
    if not full_path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with open(full_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def read_data_file(path: str) -> str:
    full_path = PROJECT_ROOT / path
    if not full_path.exists():
        return ""
    return full_path.read_text()


def write_data_file(path: str, content: str):
    full_path = PROJECT_ROOT / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)


def parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse a timestamp string into a timezone-aware UTC datetime."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _symbol_display(entry: Dict[str, Any], is_position_monitor: bool) -> str:
    """Build the display label for a symbol/position."""
    symbol = entry.get("symbol", "?")
    if is_position_monitor:
        strike = entry.get("current_strike") or entry.get("strike", "")
        exp = entry.get("current_expiration") or entry.get("expiration", "")
        if strike and exp:
            return f"{symbol} ${strike} exp {exp}"
    return symbol


def _signal_key(entry: Dict[str, Any], is_position_monitor: bool) -> str:
    """Build a grouping key for a signal/decision entry."""
    symbol = entry.get("symbol", "?")
    if is_position_monitor:
        strike = entry.get("current_strike") or entry.get("strike", "")
        exp = entry.get("current_expiration") or entry.get("expiration", "")
        if strike and exp:
            return f"{symbol}_{strike}_{exp}"
    return symbol


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Options Agent Dashboard")

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── Jinja2 custom filters ─────────────────────────────────────────────────
def _json_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)

templates.env.filters["json_pretty"] = _json_pretty


# ── Dashboard ──────────────────────────────────────────────────────────────

def _count_by_range(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count entries by time range: today, week, month, total."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    counts = {"today": 0, "week": 0, "month": 0, "total": len(entries)}
    for e in entries:
        ts = parse_timestamp(e.get("timestamp", ""))
        if ts is None:
            continue
        if ts >= today_start:
            counts["today"] += 1
        if ts >= week_start:
            counts["week"] += 1
        if ts >= month_start:
            counts["month"] += 1
    return counts


def _build_agent_table(agent_key: str, agent_info: Dict) -> Dict[str, Any]:
    """Build the per-agent table data for the dashboard."""
    is_pm = agent_info["is_position_monitor"]

    # Use signal log for sell-side agents, decision log for monitors
    log_path = agent_info["signal_log"] if not is_pm else agent_info["decision_log"]
    entries = read_jsonl(log_path)

    # Group by symbol key
    groups: Dict[str, List[Dict[str, Any]]] = {}
    display_map: Dict[str, str] = {}
    for e in entries:
        key = _signal_key(e, is_pm)
        display_map[key] = _symbol_display(e, is_pm)
        groups.setdefault(key, []).append(e)

    rows = []
    for key, group in groups.items():
        counts = _count_by_range(group)
        rows.append({
            "key": key,
            "display": display_map[key],
            "today": counts["today"],
            "week": counts["week"],
            "month": counts["month"],
            "total": counts["total"],
        })

    total_counts = _count_by_range(entries)
    return {
        "key": agent_key,
        "label": agent_info["label"],
        "rows": rows,
        "totals": total_counts,
        "is_position_monitor": is_pm,
    }


def _recent_activity(limit: int = 10) -> List[Dict[str, Any]]:
    """Get the most recent decision/signal entries across ALL agents."""
    all_entries: List[Dict[str, Any]] = []
    for agent_key, info in AGENT_TYPES.items():
        for log_key in ("decision_log", "signal_log"):
            entries = read_jsonl(info[log_key])
            for e in entries:
                e["_agent_key"] = agent_key
                e["_agent_label"] = info["label"]
                e["_log_type"] = "signal" if "signal" in log_key else "decision"
                all_entries.append(e)

    # Sort by timestamp desc
    def _sort_key(e: Dict) -> str:
        return e.get("timestamp", "")

    all_entries.sort(key=_sort_key, reverse=True)

    # Deduplicate (same timestamp + symbol + agent = same event)
    seen = set()
    unique = []
    for e in all_entries:
        sig = (e.get("timestamp", ""), e.get("symbol", ""), e["_agent_key"])
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(e)

    return unique[:limit]


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "")

    # Next scheduled run
    next_run = ""
    if cron_expr:
        try:
            cron = croniter(cron_expr, datetime.now())
            next_run = cron.get_next(datetime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            next_run = "Invalid cron"

    # Last run — most recent timestamp across all decision logs
    last_run = ""
    for info in AGENT_TYPES.values():
        entries = read_jsonl(info["decision_log"])
        if entries:
            ts = entries[-1].get("timestamp", "")
            if ts > last_run:
                last_run = ts

    agent_tables = [_build_agent_table(k, v) for k, v in AGENT_TYPES.items()]

    # Totals across all agents
    grand_totals = {"today": 0, "week": 0, "month": 0, "total": 0}
    for t in agent_tables:
        for k in grand_totals:
            grand_totals[k] += t["totals"][k]

    # Position summary
    symbol_count = 0
    position_count = 0
    for info in AGENT_TYPES.values():
        content = read_data_file(info["source_file"])
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
        if info["is_position_monitor"]:
            position_count += len(lines)
        else:
            symbol_count += len(lines)

    activity = _recent_activity(10)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "agent_tables": agent_tables,
        "grand_totals": grand_totals,
        "last_run": last_run,
        "next_run": next_run,
        "cron_expr": cron_expr,
        "symbol_count": symbol_count,
        "position_count": position_count,
        "activity": activity,
    })


# ── Signals list for agent+symbol ─────────────────────────────────────────

@app.get("/signals/{agent_type}/{symbol}", response_class=HTMLResponse)
async def signals_list(request: Request, agent_type: str, symbol: str):
    if agent_type not in AGENT_TYPES:
        return HTMLResponse("Agent type not found", status_code=404)

    info = AGENT_TYPES[agent_type]
    is_pm = info["is_position_monitor"]

    # Read from signal log for sell-side, decision log for monitors
    log_path = info["signal_log"] if not is_pm else info["decision_log"]
    all_entries = read_jsonl(log_path)

    # Filter by symbol key
    filtered = [e for e in all_entries if _signal_key(e, is_pm) == symbol]
    # Sort newest first
    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    return templates.TemplateResponse("signals.html", {
        "request": request,
        "agent_type": agent_type,
        "agent_label": info["label"],
        "symbol": symbol,
        "signals": filtered,
        "is_position_monitor": is_pm,
    })


# ── Single signal detail with backing decisions ───────────────────────────

@app.get("/signals/{agent_type}/{symbol}/{signal_index}", response_class=HTMLResponse)
async def signal_detail(request: Request, agent_type: str, symbol: str, signal_index: int):
    if agent_type not in AGENT_TYPES:
        return HTMLResponse("Agent type not found", status_code=404)

    info = AGENT_TYPES[agent_type]
    is_pm = info["is_position_monitor"]

    log_path = info["signal_log"] if not is_pm else info["decision_log"]
    all_entries = read_jsonl(log_path)
    filtered = [e for e in all_entries if _signal_key(e, is_pm) == symbol]
    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    if signal_index < 0 or signal_index >= len(filtered):
        return HTMLResponse("Signal not found", status_code=404)

    signal_entry = filtered[signal_index]
    signal_ts = parse_timestamp(signal_entry.get("timestamp", ""))

    # Find backing decisions — same symbol, within 2 hours before the signal
    decisions = read_jsonl(info["decision_log"])
    backing = []
    signal_symbol = signal_entry.get("symbol", "").lower()
    for d in decisions:
        d_symbol = d.get("symbol", "").lower()
        if d_symbol != signal_symbol:
            continue
        d_ts = parse_timestamp(d.get("timestamp", ""))
        if d_ts and signal_ts:
            diff = abs((signal_ts - d_ts).total_seconds())
            if diff <= 7200:  # 2 hours
                backing.append(d)
    backing.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    return templates.TemplateResponse("signal_detail.html", {
        "request": request,
        "agent_type": agent_type,
        "agent_label": info["label"],
        "symbol": symbol,
        "signal_index": signal_index,
        "signal": signal_entry,
        "decisions": backing,
        "is_position_monitor": is_pm,
    })


# ── Settings ───────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    files = {}
    for key, meta in DATA_FILES.items():
        files[key] = {
            "label": meta["label"],
            "hint": meta["hint"],
            "content": read_data_file(meta["path"]),
        }
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "files": files,
    })


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    form = await request.form()
    saved = []
    for key, meta in DATA_FILES.items():
        field_name = f"file_{key}"
        if field_name in form:
            content = str(form[field_name])
            # Normalize line endings
            content = content.replace("\r\n", "\n")
            if not content.endswith("\n"):
                content += "\n"
            write_data_file(meta["path"], content)
            saved.append(meta["label"])

    # Re-read for display
    files = {}
    for key, meta in DATA_FILES.items():
        files[key] = {
            "label": meta["label"],
            "hint": meta["hint"],
            "content": read_data_file(meta["path"]),
        }
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "files": files,
        "saved": saved,
    })


# ── Chat ───────────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.post("/api/chat")
async def chat_api(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    if not messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    # Build context from recent decisions
    context_parts = []
    for agent_key, info in AGENT_TYPES.items():
        entries = read_jsonl(info["decision_log"])
        recent = entries[-20:]  # last 20 per log
        if recent:
            context_parts.append(f"\n--- {info['label']} (last {len(recent)} decisions) ---")
            for e in recent:
                context_parts.append(json.dumps(e, indent=2, default=str))

    context_text = "\n".join(context_parts) if context_parts else "No recent decisions available."

    system_prompt = (
        "You are an options trading advisor. You have access to recent analysis decisions "
        "for the user's portfolio. Answer questions about positions, risks, and recommended "
        "actions based on this data.\n\n"
        f"Recent analysis data:\n{context_text}"
    )

    # Load config for Azure OpenAI
    config = _load_config()
    azure_cfg = config.get("azure", {})
    raw_endpoint = azure_cfg.get("project_endpoint", "")
    model = azure_cfg.get("model_deployment", "gpt-4o")

    # Resolve env vars in endpoint (handles ${VAR} pattern)
    import re as _re
    def _resolve_env(s: str) -> str:
        def _repl(m):
            return os.environ.get(m.group(1), "")
        return _re.sub(r'\$\{([^}]+)\}', _repl, s)

    endpoint = _resolve_env(raw_endpoint)
    if not endpoint:
        return JSONResponse({"error": "Azure endpoint not configured"}, status_code=500)

    # Strip /api suffix for AzureOpenAI client
    if endpoint.endswith("/api"):
        endpoint = endpoint[:-4]

    try:
        from azure.identity import AzureCliCredential
        from openai import AzureOpenAI

        credential = AzureCliCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=token.token,
            api_version="2024-12-01-preview",
        )

        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            api_messages.append({"role": m["role"], "content": m["content"]})

        response = client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=0.7,
            max_completion_tokens=2048,
        )

        reply = response.choices[0].message.content
        return JSONResponse({"reply": reply})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
