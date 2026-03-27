import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
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


def _write_config(config: Dict[str, Any]):
    """Write config dict back to config.yaml."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


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


def _seed_from_source(source_file: str, is_pm: bool) -> tuple:
    """Parse a source data file and return (groups, display_map) pre-seeded
    with empty lists so every symbol/position gets a dashboard row."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    display_map: Dict[str, str] = {}
    content = read_data_file(source_file)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if is_pm:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            symbol = parts[0].split("-", 1)[-1]
            strike = float(parts[1])
            expiration = parts[2].strip()
            key = f"{symbol}_{strike}_{expiration}"
            display = f"{symbol} ${strike} exp {expiration}"
        else:
            symbol = line.split("-", 1)[-1]
            key = symbol
            display = symbol
        groups.setdefault(key, [])
        display_map.setdefault(key, display)
    return groups, display_map


def _latest_decisions_by_key(agent_info: Dict) -> Dict[str, Dict[str, Any]]:
    """Return the most recent decision entry for each symbol/position key."""
    is_pm = agent_info["is_position_monitor"]
    decisions = read_jsonl(agent_info["decision_log"])
    latest: Dict[str, Dict[str, Any]] = {}
    for d in decisions:
        key = _signal_key(d, is_pm)
        prev = latest.get(key)
        if prev is None or d.get("timestamp", "") > prev.get("timestamp", ""):
            latest[key] = d
    return latest


def _build_agent_table(agent_key: str, agent_info: Dict) -> Dict[str, Any]:
    """Build the per-agent table data for the dashboard."""
    is_pm = agent_info["is_position_monitor"]

    # Seed rows from the source data file so ALL symbols appear
    groups, display_map = _seed_from_source(agent_info["source_file"], is_pm)

    # Always use signal_log for dashboard counts (signals = actionable only)
    entries = read_jsonl(agent_info["signal_log"])

    # Layer signal entries on top
    for e in entries:
        key = _signal_key(e, is_pm)
        display_map.setdefault(key, _symbol_display(e, is_pm))
        groups.setdefault(key, []).append(e)

    # Latest decision per symbol — used for health metrics & risk flags
    latest_decisions = _latest_decisions_by_key(agent_info)

    rows = []
    for key, group in groups.items():
        counts = _count_by_range(group)
        row = {
            "key": key,
            "display": display_map[key],
            "today": counts["today"],
            "week": counts["week"],
            "month": counts["month"],
            "total": counts["total"],
        }

        # Attach latest decision metrics
        dec = latest_decisions.get(key, {})

        # Quick Win 2: risk flags for ALL agent types
        row["risk_flags"] = dec.get("risk_flags", [])

        # Quick Win 1: position health cards for monitors
        if is_pm:
            row["dte"] = dec.get("dte_remaining")
            row["moneyness"] = dec.get("moneyness")
            row["assignment_risk"] = dec.get("assignment_risk")
            row["delta"] = dec.get("delta")

        rows.append(row)

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

    # Always read from signal_log for the signals table
    all_entries = read_jsonl(info["signal_log"])

    # Filter by symbol key
    filtered = [e for e in all_entries if _signal_key(e, is_pm) == symbol]
    # Sort newest first
    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Also load recent decisions for context
    all_decisions = read_jsonl(info["decision_log"])
    decisions = [d for d in all_decisions if _signal_key(d, is_pm) == symbol]
    decisions.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    decisions = decisions[:20]

    return templates.TemplateResponse("signals.html", {
        "request": request,
        "agent_type": agent_type,
        "agent_label": info["label"],
        "symbol": symbol,
        "signals": filtered,
        "decisions": decisions,
        "is_position_monitor": is_pm,
    })


# ── Single signal detail with backing decisions ───────────────────────────

@app.get("/signals/{agent_type}/{symbol}/{signal_index}", response_class=HTMLResponse)
async def signal_detail(request: Request, agent_type: str, symbol: str, signal_index: int):
    if agent_type not in AGENT_TYPES:
        return HTMLResponse("Agent type not found", status_code=404)

    info = AGENT_TYPES[agent_type]
    is_pm = info["is_position_monitor"]

    # Always read from signal_log for signal detail
    all_entries = read_jsonl(info["signal_log"])
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


# ── Single decision detail ─────────────────────────────────────────────────

@app.get("/decisions/{agent_type}/{symbol}/{decision_index}", response_class=HTMLResponse)
async def decision_detail(request: Request, agent_type: str, symbol: str, decision_index: int):
    if agent_type not in AGENT_TYPES:
        return HTMLResponse("Agent type not found", status_code=404)

    info = AGENT_TYPES[agent_type]
    is_pm = info["is_position_monitor"]

    all_decisions = read_jsonl(info["decision_log"])
    filtered = [d for d in all_decisions if _signal_key(d, is_pm) == symbol]
    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    if decision_index < 0 or decision_index >= len(filtered):
        return HTMLResponse("Decision not found", status_code=404)

    decision_entry = filtered[decision_index]

    return templates.TemplateResponse("decision_detail.html", {
        "request": request,
        "agent_type": agent_type,
        "agent_label": info["label"],
        "symbol": symbol,
        "decision_index": decision_index,
        "decision": decision_entry,
        "is_position_monitor": is_pm,
    })


# ── Settings ───────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")

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
        "cron_expr": cron_expr,
    })


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    form = await request.form()
    saved = []

    # Handle cron expression update
    new_cron = str(form.get("cron_expr", "")).strip()
    if new_cron:
        try:
            croniter(new_cron)  # validate
            config = _load_config()
            config.setdefault("scheduler", {})["cron"] = new_cron
            _write_config(config)
            saved.append("Cron schedule")

            # Signal the scheduler to reschedule (if running)
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule(new_cron)
        except (ValueError, KeyError):
            pass  # invalid cron — silently keep old value

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
    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
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
        "cron_expr": cron_expr,
    })


# ── Trigger (Run Now) ──────────────────────────────────────────────────────

AGENT_FUNCTIONS = {
    "covered_call": "run_covered_call_analysis",
    "cash_secured_put": "run_cash_secured_put_analysis",
    "open_call_monitor": "run_open_call_monitor",
    "open_put_monitor": "run_open_put_monitor",
}


def _run_agent_in_background(agent_type: str, scheduler):
    """Run a single agent's analysis in a background thread."""
    import asyncio
    from src.covered_call_agent import run_covered_call_analysis
    from src.cash_secured_put_agent import run_cash_secured_put_analysis
    from src.open_call_monitor_agent import run_open_call_monitor
    from src.open_put_monitor_agent import run_open_put_monitor

    funcs = {
        "covered_call": run_covered_call_analysis,
        "cash_secured_put": run_cash_secured_put_analysis,
        "open_call_monitor": run_open_call_monitor,
        "open_put_monitor": run_open_put_monitor,
    }
    func = funcs[agent_type]
    try:
        asyncio.run(func(scheduler.config, scheduler.runner))
    except Exception as e:
        print(f"ERROR running {agent_type} trigger: {e}")


@app.post("/api/trigger/{agent_type}")
async def trigger_agent(request: Request, agent_type: str):
    if agent_type not in AGENT_FUNCTIONS:
        return JSONResponse({"error": f"Unknown agent type: {agent_type}"}, status_code=404)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse({"error": "Scheduler not running — cannot trigger agents"}, status_code=503)

    thread = threading.Thread(
        target=_run_agent_in_background,
        args=(agent_type, scheduler),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": agent_type})


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

    # Build context from recent signals + decisions, grouped by symbol
    # First pass: count total signals to decide budget limits
    all_signal_groups: Dict[str, Dict[str, List[Dict]]] = {}
    all_decision_groups: Dict[str, Dict[str, List[Dict]]] = {}
    total_signals = 0

    for agent_key, info in AGENT_TYPES.items():
        is_pos = info.get("is_position_monitor", False)

        sig_by_sym: Dict[str, List[Dict]] = defaultdict(list)
        for e in read_jsonl(info["signal_log"]):
            sig_by_sym[_signal_key(e, is_pos)].append(e)
        all_signal_groups[agent_key] = sig_by_sym
        total_signals += sum(min(2, len(v)) for v in sig_by_sym.values())

        dec_by_sym: Dict[str, List[Dict]] = defaultdict(list)
        for e in read_jsonl(info["decision_log"]):
            dec_by_sym[_signal_key(e, is_pos)].append(e)
        all_decision_groups[agent_key] = dec_by_sym

    # Reduced mode when too many signals
    if total_signals > 20:
        sig_limit, dec_limit = 1, 2
    else:
        sig_limit, dec_limit = 2, 4

    # Second pass: build formatted context
    context_parts: List[str] = []
    for agent_key, info in AGENT_TYPES.items():
        is_pos = info.get("is_position_monitor", False)
        sig_groups = all_signal_groups[agent_key]
        dec_groups = all_decision_groups[agent_key]
        all_keys = dict.fromkeys(list(sig_groups.keys()) + list(dec_groups.keys()))

        if not all_keys:
            continue

        context_parts.append(f"\n--- {info['label']} ---")
        for sym_key in all_keys:
            signals = sig_groups.get(sym_key, [])[-sig_limit:]
            decisions = dec_groups.get(sym_key, [])[-dec_limit:]
            if not signals and not decisions:
                continue

            # Use the most recent entry to build the display label
            sample = (signals or decisions)[-1]
            display = _symbol_display(sample, is_pos)
            context_parts.append(f"\n## {display}")

            if signals:
                context_parts.append(f"Signals (last {len(signals)}):")
                for s in reversed(signals):
                    context_parts.append(json.dumps(s, indent=2, default=str))
            if decisions:
                context_parts.append(f"Decisions (last {len(decisions)}):")
                for d in reversed(decisions):
                    context_parts.append(json.dumps(d, indent=2, default=str))

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
