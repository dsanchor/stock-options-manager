import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional

import yaml
from croniter import croniter
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Agent type metadata — labels only; data comes from CosmosDB
AGENT_TYPES = {
    "open_call_monitor": {"label": "Open Call Monitor", "is_position_monitor": True},
    "open_put_monitor": {"label": "Open Put Monitor", "is_position_monitor": True},
    "covered_call": {"label": "Following · Covered Call", "is_position_monitor": False},
    "cash_secured_put": {"label": "Following · Cash-Secured Put", "is_position_monitor": False},
}

# ---------------------------------------------------------------------------
# Config utilities
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load raw config.yaml without env-var substitution (web doesn't need secrets)."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _write_config(config: Dict[str, Any]):
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def _resolve_env(s: str) -> str:
    """Resolve ${VAR_NAME} patterns in a string."""
    def _repl(m):
        var_name = m.group(1)
        value = os.environ.get(var_name, "")
        if not value:
            logger.warning("Environment variable %s is not set", var_name)
        return value
    return re.sub(r'\$\{([^}]+)\}', _repl, s)


def parse_timestamp(ts: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _count_by_range(entries: List[Dict[str, Any]]) -> Dict[str, int]:
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


_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean_doc(doc: dict) -> dict:
    """Strip CosmosDB system properties for API responses."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Stock Options Manager Dashboard")

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")),
          name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _json_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)

templates.env.filters["json_pretty"] = _json_pretty


# ── Startup — initialise CosmosDB ─────────────────────────────────────────

async def init_cosmos(app_instance):
    """Initialise CosmosDB on the given FastAPI app. Safe to call from
    either the on_event("startup") handler or an external lifespan."""
    try:
        config = _load_config()
        cosmos_cfg = config.get("cosmosdb", {})
        endpoint = _resolve_env(cosmos_cfg.get("endpoint", ""))
        key = _resolve_env(cosmos_cfg.get("key", ""))
        database = cosmos_cfg.get("database", "stock-options-manager")

        logger.info("CosmosDB config — endpoint: %s, database: %s, "
                     "key present: %s, key length: %d",
                     endpoint or "(empty)", database,
                     bool(key), len(key))

        if endpoint and key:
            from src.cosmos_db import CosmosDBService
            cosmos = CosmosDBService(
                endpoint=endpoint, key=key, database_name=database,
            )
            # Eagerly validate the connection so failures surface at startup
            cosmos.database.read()
            app_instance.state.cosmos = cosmos
            app_instance.state.cosmos_error = None
            logger.info("CosmosDB initialized successfully: %s, database=%s",
                        endpoint, database)
        else:
            missing = []
            if not endpoint:
                missing.append("COSMOSDB_ENDPOINT")
            if not key:
                missing.append("COSMOSDB_KEY")
            error_msg = (f"{' and '.join(missing)} environment variable"
                         f"{'s' if len(missing) > 1 else ''} not set")
            app_instance.state.cosmos = None
            app_instance.state.cosmos_error = error_msg
            logger.warning("CosmosDB not initialized: %s", error_msg)
    except Exception as e:
        logger.exception("CosmosDB init failed")
        app_instance.state.cosmos = None
        app_instance.state.cosmos_error = str(e)


@app.on_event("startup")
async def startup():
    await init_cosmos(app)


def _get_cosmos(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error = getattr(request.app.state, "cosmos_error", "unknown")
        raise RuntimeError(f"CosmosDB not available: {error}")
    return cosmos


# ===========================================================================
# REST API — Symbol Management
# ===========================================================================

@app.get("/api/symbols")
async def api_list_symbols(request: Request):
    try:
        cosmos = _get_cosmos(request)
        symbols = cosmos.list_symbols()
        return JSONResponse([_clean_doc(s) for s in symbols])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols")
async def api_create_symbol(request: Request):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        symbol = body.get("symbol", "").strip().upper()
        exchange = body.get("exchange", "").strip().upper()
        display_name = body.get("display_name", "").strip()
        if not display_name:
            display_name = f"{exchange}:{symbol}"
        covered_call = bool(body.get("covered_call", False))
        cash_secured_put = bool(body.get("cash_secured_put", False))

        if not symbol or not exchange:
            return JSONResponse({"error": "symbol and exchange are required"},
                                status_code=400)

        existing = cosmos.get_symbol(symbol)
        if existing:
            return JSONResponse({"error": f"Symbol {symbol} already exists"},
                                status_code=409)

        doc = cosmos.create_symbol(symbol, exchange, display_name,
                                   covered_call, cash_secured_put)
        return JSONResponse(_clean_doc(doc), status_code=201)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}")
async def api_get_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        return JSONResponse(_clean_doc(doc))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/symbols/{symbol}")
async def api_update_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)

        body = await request.json()
        if "display_name" in body:
            doc["display_name"] = body["display_name"]
        if "covered_call" in body:
            doc["watchlist"]["covered_call"] = bool(body["covered_call"])
        if "cash_secured_put" in body:
            doc["watchlist"]["cash_secured_put"] = bool(body["cash_secured_put"])
        if "exchange" in body:
            doc["exchange"] = body["exchange"].strip().upper()

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        updated = cosmos.container.replace_item(item=doc["id"], body=doc)

        # Cascade-delete decisions & signals when a watchlist agent is toggled OFF
        sym = symbol.upper()
        if "covered_call" in body and not bool(body["covered_call"]):
            cosmos.delete_decisions_by_agent_type(sym, "covered_call")
        if "cash_secured_put" in body and not bool(body["cash_secured_put"]):
            cosmos.delete_decisions_by_agent_type(sym, "cash_secured_put")

        return JSONResponse(_clean_doc(updated))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}")
async def api_delete_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        cosmos.delete_symbol(symbol.upper())
        return JSONResponse({"status": "deleted", "symbol": symbol.upper()})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Position Management
# ===========================================================================

@app.post("/api/symbols/{symbol}/positions")
async def api_add_position(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        position_type = body.get("type", "").strip().lower()
        strike = body.get("strike")
        expiration = body.get("expiration", "").strip()
        notes = body.get("notes", "").strip()

        if position_type not in ("call", "put"):
            return JSONResponse({"error": "type must be 'call' or 'put'"},
                                status_code=400)
        if not strike or not expiration:
            return JSONResponse({"error": "strike and expiration are required"},
                                status_code=400)
        try:
            strike = float(strike)
        except (TypeError, ValueError):
            return JSONResponse({"error": "strike must be a number"},
                                status_code=400)

        doc = cosmos.add_position(symbol.upper(), position_type, strike,
                                  expiration, notes)
        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/symbols/{symbol}/positions/{position_id}/close")
async def api_close_position(request: Request, symbol: str, position_id: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.close_position(symbol.upper(), position_id)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}/positions/{position_id}")
async def api_delete_position(request: Request, symbol: str, position_id: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.delete_position(symbol.upper(), position_id)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Chart Data
# ===========================================================================

@app.get("/api/symbols/{symbol}/chart-data")
async def symbol_chart_data(request: Request, symbol: str):
    """Return price history + decision/signal markers for charting."""
    import yfinance as yf

    sym = symbol.upper()

    # Fetch OHLC data via yfinance in a thread pool (blocking I/O)
    try:
        hist = await asyncio.to_thread(
            lambda: yf.Ticker(sym).history(period="3mo")
        )
    except Exception as e:
        logger.warning("yfinance error for %s: %s", sym, e)
        return JSONResponse({"candles": [], "markers": []})

    candles = []
    if hist is not None and not hist.empty:
        for date, row in hist.iterrows():
            candles.append({
                "time": date.strftime("%Y-%m-%d"),
                "open": round(row["Open"], 2),
                "high": round(row["High"], 2),
                "low": round(row["Low"], 2),
                "close": round(row["Close"], 2),
            })

    # Fetch decisions and signals from CosmosDB for marker overlay
    markers = []
    try:
        cosmos = _get_cosmos(request)
        since_date = (datetime.now(timezone.utc) - timedelta(days=93)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )

        decisions: List[Dict] = []
        signals: List[Dict] = []
        for agent_type in AGENT_TYPES:
            decisions.extend(
                cosmos.get_recent_decisions(sym, agent_type, max_entries=50)
            )
            signals.extend(
                cosmos.get_recent_signals(sym, agent_type, max_entries=50)
            )

        # Build a set of decision IDs that are signals for quick lookup
        signal_decision_ids = {s.get("decision_id") for s in signals}

        for dec in decisions:
            ts = dec.get("timestamp", "")[:10]
            if not ts:
                continue
            is_signal = dec.get("is_signal", False) or dec.get("id") in signal_decision_ids
            markers.append({
                "time": ts,
                "position": "aboveBar" if is_signal else "belowBar",
                "color": "#f59e0b" if is_signal else "#6b7280",
                "shape": "arrowDown" if is_signal else "circle",
                "text": "⚡" if is_signal else "📊",
                "id": dec.get("id", ""),
                "agent_type": dec.get("agent_type", ""),
            })

        # Sort markers by time (required by Lightweight Charts)
        markers.sort(key=lambda m: m["time"])
    except Exception as e:
        logger.warning("Failed to load chart markers for %s: %s", sym, e)

    return JSONResponse({"candles": candles, "markers": markers})


# ===========================================================================
# REST API — Data Views
# ===========================================================================

@app.get("/api/signals")
async def api_signals(request: Request, agent_type: str = None,
                      since: str = None, limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        results = cosmos.get_all_signals(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/decisions")
async def api_decisions(request: Request, agent_type: str = None,
                        symbol: str = None, since: str = None,
                        limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        if symbol:
            conditions = ["c.doc_type = 'decision'"]
            params: List[dict] = []
            if agent_type:
                conditions.append("c.agent_type = @agent_type")
                params.append({"name": "@agent_type", "value": agent_type})
            if since:
                conditions.append("c.timestamp >= @since")
                params.append({"name": "@since", "value": since})
            query = (
                f"SELECT TOP @limit * FROM c "
                f"WHERE {' AND '.join(conditions)} "
                f"ORDER BY c.timestamp DESC"
            )
            params.append({"name": "@limit", "value": limit})
            results = list(cosmos.container.query_items(
                query=query, parameters=params,
                partition_key=symbol.upper(),
            ))
        else:
            results = cosmos.get_all_decisions(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Page Routes — Dashboard
# ===========================================================================

def _build_dashboard_tables(cosmos, all_symbols, all_signals, all_decisions):
    """Build per-agent table data for the dashboard from CosmosDB data."""
    agent_tables = []
    grand_totals = {"today": 0, "week": 0, "month": 0, "total": 0}

    for agent_key, agent_meta in AGENT_TYPES.items():
        is_pm = agent_meta["is_position_monitor"]
        agent_signals = [s for s in all_signals
                         if s.get("agent_type") == agent_key]

        groups: Dict[str, List[Dict]] = {}
        display_map: Dict[str, str] = {}

        # Seed rows from symbol configs so every watched symbol/position appears
        for sym_cfg in all_symbols:
            sym = sym_cfg["symbol"]
            if is_pm:
                ptype = "call" if agent_key == "open_call_monitor" else "put"
                for pos in sym_cfg.get("positions", []):
                    if pos.get("status") == "active" and pos["type"] == ptype:
                        key = f"{sym}_{pos['strike']}_{pos['expiration']}"
                        display_map[key] = (
                            f"{sym} ${pos['strike']} exp {pos['expiration']}"
                        )
                        groups.setdefault(key, [])
            else:
                wl = sym_cfg.get("watchlist", {})
                if ((agent_key == "covered_call" and wl.get("covered_call"))
                        or (agent_key == "cash_secured_put"
                            and wl.get("cash_secured_put"))):
                    groups.setdefault(sym, [])
                    display_map.setdefault(
                        sym, sym_cfg.get("display_name", sym))

        # Layer signals onto groups
        for sig in agent_signals:
            sym = sig.get("symbol", "")
            if is_pm:
                strike = (sig.get("current_strike")
                          or sig.get("strike", ""))
                exp = (sig.get("current_expiration")
                       or sig.get("expiration", ""))
                key = f"{sym}_{strike}_{exp}" if strike and exp else sym
                if key not in display_map:
                    display_map[key] = (
                        f"{sym} ${strike} exp {exp}" if strike and exp
                        else sym
                    )
            else:
                key = sym
                display_map.setdefault(key, sym)
            groups.setdefault(key, []).append(sig)

        # Latest decision per key — for health metrics and risk flags
        agent_decs = [d for d in all_decisions
                      if d.get("agent_type") == agent_key]
        latest_by_key: Dict[str, Dict] = {}
        for d in agent_decs:
            sym = d.get("symbol", "")
            if is_pm:
                strike = (d.get("current_strike")
                          or d.get("strike", ""))
                exp = (d.get("current_expiration")
                       or d.get("expiration", ""))
                key = f"{sym}_{strike}_{exp}" if strike and exp else sym
            else:
                key = sym
            prev = latest_by_key.get(key)
            if (prev is None
                    or d.get("timestamp", "") > prev.get("timestamp", "")):
                latest_by_key[key] = d

        rows = []
        for key, group in groups.items():
            counts = _count_by_range(group)
            # Extract the base symbol from the key for linking
            base_symbol = key.split("_")[0] if "_" in key else key
            row: Dict[str, Any] = {
                "key": key,
                "symbol": base_symbol,
                "display": display_map.get(key, key),
                "today": counts["today"],
                "week": counts["week"],
                "month": counts["month"],
                "total": counts["total"],
                "risk_flags": latest_by_key.get(key, {}).get(
                    "risk_flags", []),
            }
            if is_pm:
                dec = latest_by_key.get(key, {})
                row["dte"] = dec.get("dte_remaining")
                row["moneyness"] = dec.get("moneyness")
                row["assignment_risk"] = dec.get("assignment_risk")
                row["delta"] = dec.get("delta")
            rows.append(row)

        total_counts = _count_by_range(agent_signals)
        for k in grand_totals:
            grand_totals[k] += total_counts[k]

        agent_tables.append({
            "key": agent_key,
            "label": agent_meta["label"],
            "rows": rows,
            "totals": total_counts,
            "is_position_monitor": is_pm,
        })

    return agent_tables, grand_totals


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "")

    next_run = ""
    if cron_expr:
        try:
            cron = croniter(cron_expr, datetime.now())
            next_run = cron.get_next(datetime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            next_run = "Invalid cron"

    cosmos = getattr(request.app.state, "cosmos", None)
    empty_ctx = {
        "request": request,
        "agent_tables": [],
        "grand_totals": {"today": 0, "week": 0, "month": 0, "total": 0},
        "last_run": "", "next_run": next_run, "cron_expr": cron_expr,
        "symbol_count": 0, "position_count": 0, "activity": [],
    }
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        empty_ctx["error"] = f"CosmosDB not available: {error_detail}"
        return templates.TemplateResponse("dashboard.html", empty_ctx)

    try:
        all_symbols = cosmos.list_symbols()
        all_signals = cosmos.get_all_signals(limit=500)
        all_decisions = cosmos.get_all_decisions(limit=50)
    except Exception as e:
        empty_ctx["error"] = f"CosmosDB query failed: {e}"
        return templates.TemplateResponse("dashboard.html", empty_ctx)

    # Build set of closed position IDs so we can exclude their data
    closed_position_ids: set = set()
    for sym_cfg in all_symbols:
        for pos in sym_cfg.get("positions", []):
            if pos.get("status") != "active":
                closed_position_ids.add(pos["position_id"])

    # Exclude decisions/signals linked to closed positions from dashboard
    if closed_position_ids:
        closed_decision_ids = {
            d["id"] for d in all_decisions
            if d.get("position_id") in closed_position_ids
        }
        all_decisions = [
            d for d in all_decisions
            if d.get("position_id") not in closed_position_ids
        ]
        all_signals = [
            s for s in all_signals
            if s.get("position_id") not in closed_position_ids
            and s.get("decision_id") not in closed_decision_ids
        ]

    symbol_count = len(all_symbols)
    position_count = sum(
        len([p for p in s.get("positions", []) if p.get("status") == "active"])
        for s in all_symbols
    )

    agent_tables, grand_totals = _build_dashboard_tables(
        cosmos, all_symbols, all_signals, all_decisions)

    last_run = ""
    if all_decisions:
        last_run = all_decisions[0].get("timestamp", "")[:19]

    activity = []
    for d in all_decisions[:10]:
        agent_key = d.get("agent_type", "")
        d["_agent_label"] = AGENT_TYPES.get(agent_key, {}).get(
            "label", agent_key)
        activity.append(d)

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


# ===========================================================================
# Page Routes — Symbols
# ===========================================================================

@app.get("/symbols", response_class=HTMLResponse)
async def symbols_page(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    symbols = cosmos.list_symbols() if cosmos else []
    for s in symbols:
        s["_active_count"] = len(
            [p for p in s.get("positions", [])
             if p.get("status") == "active"]
        )
    return templates.TemplateResponse("symbols.html", {
        "request": request,
        "symbols": symbols,
    })


@app.get("/symbols/{symbol}", response_class=HTMLResponse)
async def symbol_detail_page(request: Request, symbol: str):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return HTMLResponse(f"Symbol {symbol} not found", status_code=404)

    # Gather recent decisions across all agent types
    decisions: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        decs = cosmos.get_recent_decisions(
            symbol.upper(), agent_type, max_entries=20)
        for d in decs:
            d["_agent_label"] = meta["label"]
        decisions.extend(decs)
    decisions.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    decisions = decisions[:20]

    # Gather recent signals
    signals: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        sigs = cosmos.get_recent_signals(
            symbol.upper(), agent_type, max_entries=10)
        for s in sigs:
            s["_agent_label"] = meta["label"]
        signals.extend(sigs)
    signals.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    return templates.TemplateResponse("symbol_detail.html", {
        "request": request,
        "symbol_doc": doc,
        "decisions": decisions,
        "signals": signals,
    })


# ===========================================================================
# Page Routes — Decision Detail
# ===========================================================================

@app.get("/decisions/{decision_id}", response_class=HTMLResponse)
async def decision_detail_page(request: Request, decision_id: str):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    decision = cosmos.get_decision_by_id(decision_id)
    if not decision:
        return HTMLResponse("Decision not found", status_code=404)

    symbol = decision.get("symbol", "")
    agent_type = decision.get("agent_type", "")
    agent_label = AGENT_TYPES.get(agent_type, {}).get("label", agent_type)
    is_signal = decision.get("is_signal", False)

    # Build display_name from symbol config (for back link)
    sym_doc = cosmos.get_symbol(symbol)
    display_name = sym_doc["display_name"] if sym_doc else symbol

    return templates.TemplateResponse("decision_detail.html", {
        "request": request,
        "decision": decision,
        "symbol": symbol,
        "display_name": display_name,
        "agent_label": agent_label,
        "agent_type": agent_type,
        "is_signal": is_signal,
    })


# ===========================================================================
# Page Routes — Settings
# ===========================================================================

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
    cosmos_endpoint = _resolve_env(
        config.get("cosmosdb", {}).get("endpoint", ""))
    cosmos_database = config.get("cosmosdb", {}).get(
        "database", "stock-options-manager")
    cosmos_status = ("Connected"
                     if getattr(request.app.state, "cosmos", None)
                     else "Not connected")
    cosmos_error = getattr(request.app.state, "cosmos_error", None)

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "cron_expr": cron_expr,
        "cosmos_endpoint": cosmos_endpoint,
        "cosmos_database": cosmos_database,
        "cosmos_status": cosmos_status,
        "cosmos_error": cosmos_error,
    })


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    form = await request.form()
    saved: List[str] = []

    new_cron = str(form.get("cron_expr", "")).strip()
    if new_cron:
        try:
            croniter(new_cron)
            config = _load_config()
            config.setdefault("scheduler", {})["cron"] = new_cron
            _write_config(config)
            saved.append("Cron schedule")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule(new_cron)
        except (ValueError, KeyError):
            pass

    config = _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
    cosmos_endpoint = _resolve_env(
        config.get("cosmosdb", {}).get("endpoint", ""))
    cosmos_database = config.get("cosmosdb", {}).get(
        "database", "stock-options-manager")
    cosmos_status = ("Connected"
                     if getattr(request.app.state, "cosmos", None)
                     else "Not connected")
    cosmos_error = getattr(request.app.state, "cosmos_error", None)

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "cron_expr": cron_expr,
        "saved": saved,
        "cosmos_endpoint": cosmos_endpoint,
        "cosmos_database": cosmos_database,
        "cosmos_status": cosmos_status,
        "cosmos_error": cosmos_error,
    })


# ===========================================================================
# Trigger (Run Now)
# ===========================================================================

AGENT_FUNCTIONS = {
    "covered_call": "run_covered_call_analysis",
    "cash_secured_put": "run_cash_secured_put_analysis",
    "open_call_monitor": "run_open_call_monitor",
    "open_put_monitor": "run_open_put_monitor",
}


def _run_agent_in_background(agent_type: str, scheduler):
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
        asyncio.run(func(scheduler.config, scheduler.runner,
                         scheduler.cosmos, scheduler.context_provider))
    except Exception as e:
        print(f"ERROR running {agent_type} trigger: {e}")


@app.post("/api/trigger/{agent_type}")
async def trigger_agent(request: Request, agent_type: str):
    if agent_type not in AGENT_FUNCTIONS:
        return JSONResponse({"error": f"Unknown agent type: {agent_type}"},
                            status_code=404)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger agents"},
            status_code=503)

    thread = threading.Thread(
        target=_run_agent_in_background,
        args=(agent_type, scheduler),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": agent_type})


# ===========================================================================
# Chat
# ===========================================================================

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.post("/api/chat")
async def chat_api(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"},
                            status_code=400)

    cosmos = getattr(request.app.state, "cosmos", None)
    context_parts: List[str] = []

    if cosmos:
        try:
            for agent_key, meta in AGENT_TYPES.items():
                signals = cosmos.get_all_signals(
                    agent_type=agent_key, limit=20)
                decisions = cosmos.get_all_decisions(
                    agent_type=agent_key, limit=20)
                if not signals and not decisions:
                    continue

                context_parts.append(f"\n--- {meta['label']} ---")

                # Group by symbol
                sym_data: Dict[str, Dict[str, list]] = defaultdict(
                    lambda: {"signals": [], "decisions": []})
                for s in signals:
                    sym_data[s.get("symbol", "?")]["signals"].append(s)
                for d in decisions:
                    sym_data[d.get("symbol", "?")]["decisions"].append(d)

                for sym, data in sym_data.items():
                    context_parts.append(f"\n## {sym}")
                    if data["signals"]:
                        context_parts.append(
                            f"Signals (last {len(data['signals'])}):")
                        for s in data["signals"][:2]:
                            context_parts.append(
                                json.dumps(_clean_doc(s), indent=2,
                                           default=str))
                    if data["decisions"]:
                        context_parts.append(
                            f"Decisions (last {len(data['decisions'])}):")
                        for d in data["decisions"][:4]:
                            context_parts.append(
                                json.dumps(_clean_doc(d), indent=2,
                                           default=str))
        except Exception:
            context_parts.append("(Error loading context from CosmosDB)")

    context_text = ("\n".join(context_parts) if context_parts
                    else "No recent decisions available.")

    system_prompt = (
        "You are a stock options manager advisor. You have access to recent "
        "analysis decisions for the user's portfolio. Answer questions about "
        "positions, risks, and recommended actions based on this data.\n\n"
        f"Recent analysis data:\n{context_text}"
    )

    config = _load_config()
    azure_cfg = config.get("azure", {})
    endpoint = _resolve_env(azure_cfg.get("project_endpoint", ""))
    model = _resolve_env(azure_cfg.get("model_deployment", "gpt-4o"))
    api_key = _resolve_env(azure_cfg.get("api_key", ""))

    if not endpoint:
        return JSONResponse({"error": "Azure endpoint not configured"},
                            status_code=500)
    if not api_key:
        return JSONResponse({"error": "Azure API key not configured"},
                            status_code=500)

    if endpoint.endswith("/api"):
        endpoint = endpoint[:-4]

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
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


# ===========================================================================
# Per-Symbol Chat
# ===========================================================================

@app.get("/symbols/{symbol}/chat", response_class=HTMLResponse)
async def symbol_chat_page(request: Request, symbol: str):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return HTMLResponse(f"Symbol {symbol} not found", status_code=404)

    return templates.TemplateResponse("symbol_chat.html", {
        "request": request,
        "symbol_doc": doc,
    })


async def _build_symbol_context(symbol: str, cosmos) -> dict:
    """Build context data for a symbol (CosmosDB + TradingView).

    Returns dict with keys: context, exchange, display_name.
    """
    context_parts: List[str] = []
    symbol_doc = None
    exchange = "NYSE"

    if cosmos:
        try:
            symbol_doc = cosmos.get_symbol(symbol)
            if symbol_doc:
                exchange = symbol_doc.get("exchange", "NYSE")
                context_parts.append("--- Symbol Config ---")
                context_parts.append(json.dumps(
                    {k: v for k, v in symbol_doc.items()
                     if k in ("symbol", "display_name", "exchange",
                              "watchlist", "positions")},
                    indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load symbol doc: %s", exc)

    if cosmos:
        try:
            decisions: List[Dict] = []
            for agent_type, meta in AGENT_TYPES.items():
                decs = cosmos.get_recent_decisions(
                    symbol, agent_type, max_entries=5)
                for d in decs:
                    d["_agent_label"] = meta["label"]
                decisions.extend(decs)
            decisions.sort(key=lambda d: d.get("timestamp", ""),
                           reverse=True)
            decisions = decisions[:5]

            if decisions:
                context_parts.append("\n--- Recent Decisions ---")
                for d in decisions:
                    context_parts.append(json.dumps(
                        _clean_doc(d), indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load decisions: %s", exc)
            context_parts.append("(Error loading decisions from CosmosDB)")

    try:
        from src.tv_data_fetcher import TradingViewFetcher

        full_symbol = f"{exchange}-{symbol}"
        async with TradingViewFetcher() as fetcher:
            tv_data = await fetcher.fetch_all(full_symbol)

        tv_sections = []
        for section_key, section_label in [
            ("overview", "Overview"),
            ("technicals", "Technicals"),
            ("forecast", "Forecast"),
            ("options_chain", "Options Chain"),
        ]:
            content = tv_data.get(section_key, "")
            if content and not content.startswith("[ERROR"):
                tv_sections.append(
                    f"\n--- TradingView {section_label} ---\n{content}")

        if tv_sections:
            context_parts.append("\n".join(tv_sections))
    except Exception as exc:
        logger.warning("symbol_chat: TradingView fetch failed: %s", exc)
        context_parts.append("(Live TradingView data unavailable)")

    context_text = ("\n".join(context_parts) if context_parts
                    else "No context data available.")
    display_name = (symbol_doc.get("display_name", symbol)
                    if symbol_doc else symbol)

    return {
        "context": context_text,
        "exchange": exchange,
        "display_name": display_name,
    }


def _build_symbol_system_prompt(symbol: str, exchange: str,
                                context_text: str) -> str:
    """Build the system prompt for per-symbol chat."""
    return (
        f"You are a stock options advisor focused exclusively on "
        f"{symbol} ({exchange}:{symbol}).\n"
        f"You have access to:\n"
        f"1. Recent analysis decisions for this symbol\n"
        f"2. Live market data from TradingView "
        f"(overview, technicals, forecast, options chain)\n"
        f"3. Current positions and watchlist status\n\n"
        f"Answer questions about this symbol's options opportunities, "
        f"risks, positions, and market conditions.\n"
        f"Stay focused on {symbol} — redirect if the user asks about "
        f"other symbols.\n\n"
        f"Context data:\n{context_text}"
    )


@app.post("/api/symbols/{symbol}/chat/context")
async def symbol_chat_context(request: Request, symbol: str):
    """Pre-fetch all heavy context (CosmosDB + TradingView) for a symbol."""
    symbol = symbol.upper()
    cosmos = getattr(request.app.state, "cosmos", None)

    try:
        result = await _build_symbol_context(symbol, cosmos)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/chat")
async def symbol_chat_api(request: Request, symbol: str):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"},
                            status_code=400)

    symbol = symbol.upper()

    # Use pre-fetched context if provided, otherwise fetch fresh
    pre_context = body.get("context")
    if pre_context:
        context_text = pre_context
        # Infer exchange from context or fall back
        cosmos = getattr(request.app.state, "cosmos", None)
        exchange = "NYSE"
        if cosmos:
            try:
                symbol_doc = cosmos.get_symbol(symbol)
                if symbol_doc:
                    exchange = symbol_doc.get("exchange", "NYSE")
            except Exception:
                pass
    else:
        cosmos = getattr(request.app.state, "cosmos", None)
        result = await _build_symbol_context(symbol, cosmos)
        context_text = result["context"]
        exchange = result["exchange"]

    system_prompt = _build_symbol_system_prompt(symbol, exchange, context_text)

    # --- Call Azure OpenAI ---
    config = _load_config()
    azure_cfg = config.get("azure", {})
    endpoint = _resolve_env(azure_cfg.get("project_endpoint", ""))
    model = _resolve_env(azure_cfg.get("model_deployment", "gpt-4o"))
    api_key = _resolve_env(azure_cfg.get("api_key", ""))

    if not endpoint:
        return JSONResponse({"error": "Azure endpoint not configured"},
                            status_code=500)
    if not api_key:
        return JSONResponse({"error": "Azure API key not configured"},
                            status_code=500)

    if endpoint.endswith("/api"):
        endpoint = endpoint[:-4]

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
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
