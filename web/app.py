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

import pytz
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


def _load_settings_from_cosmos(cosmos) -> Optional[dict]:
    """Load settings from CosmosDB. Returns None if unavailable."""
    if cosmos is None:
        return None
    try:
        return cosmos.get_settings()
    except Exception:
        logger.warning("Failed to load settings from CosmosDB", exc_info=True)
        return None


def _save_settings_to_cosmos(cosmos, settings: dict):
    """Save settings to CosmosDB. Best-effort."""
    if cosmos is None:
        return
    try:
        cosmos.save_settings(settings)
        logger.info("Settings saved to CosmosDB")
    except Exception:
        logger.warning("Failed to save settings to CosmosDB", exc_info=True)


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
            
            # Merge config.yaml defaults into CosmosDB (first-run seed + new keys)
            settings_defaults = {
                k: v for k, v in config.items()
                if k not in ('azure', 'cosmosdb')
            }
            # Resolve env vars in defaults before storing
            from src.config import Config
            resolved_config = Config()
            resolved_defaults = {
                k: v for k, v in resolved_config.config.items()
                if k not in ('azure', 'cosmosdb')
            }
            cosmos.merge_defaults(resolved_defaults)
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

        # Cascade-delete activities & alerts when a watchlist agent is toggled OFF
        sym = symbol.upper()
        if "covered_call" in body and not bool(body["covered_call"]):
            cosmos.delete_activities_by_agent_type(sym, "covered_call")
        if "cash_secured_put" in body and not bool(body["cash_secured_put"]):
            cosmos.delete_activities_by_agent_type(sym, "cash_secured_put")

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


@app.post("/api/symbols/{symbol}/positions/from-activity/{activity_id}")
async def api_add_position_from_activity(request: Request, symbol: str,
                                         activity_id: str):
    """Create a position from an existing activity, disable watchlist, and
    cascade-delete related activities/alerts."""
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if activity is None:
            return JSONResponse({"error": f"Activity {activity_id} not found"},
                                status_code=404)

        strike = activity.get("strike")
        expiration = activity.get("expiration")
        agent_type = activity.get("agent_type")

        if not strike or not expiration or not agent_type:
            return JSONResponse(
                {"error": "Activity missing required fields (strike, expiration, agent_type)"},
                status_code=400,
            )

        agent_type_map = {"covered_call": "call", "cash_secured_put": "put"}
        position_type = agent_type_map.get(agent_type)
        if position_type is None:
            return JSONResponse(
                {"error": f"Unsupported agent_type '{agent_type}'"},
                status_code=400,
            )

        source = {
            "activity_id": activity["id"],
            "agent_type": activity.get("agent_type"),
            "activity": activity.get("activity"),
            "confidence": activity.get("confidence"),
            "reason": activity.get("reason"),
            "underlying_price": activity.get("underlying_price"),
            "premium": activity.get("premium"),
            "iv": activity.get("iv"),
            "risk_flags": activity.get("risk_flags", []),
            "timestamp": activity.get("timestamp"),
        }

        doc = cosmos.add_position(
            symbol.upper(), position_type, float(strike),
            expiration, notes="", source=source,
        )

        # Disable the watchlist for this agent type
        sym_doc = cosmos.get_symbol(symbol.upper())
        if agent_type in ("covered_call", "cash_secured_put"):
            sym_doc["watchlist"][agent_type] = False
            sym_doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
            cosmos.container.replace_item(item=sym_doc["id"], body=sym_doc)
            # Cascade-delete activities/alerts for this agent type
            cosmos.delete_activities_by_agent_type(symbol.upper(), agent_type)

        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/roll-from-activity/{activity_id}")
async def api_roll_position_from_activity(request: Request, symbol: str,
                                          activity_id: str):
    """Roll a position from a monitor-agent activity: close old + open new."""
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if activity is None:
            return JSONResponse({"error": f"Activity {activity_id} not found"},
                                status_code=404)

        strike = (activity.get("strike")
                  or activity.get("new_strike")
                  or activity.get("current_strike"))
        expiration = (activity.get("expiration")
                      or activity.get("new_expiration")
                      or activity.get("current_expiration"))
        agent_type = activity.get("agent_type")
        position_id = activity.get("position_id")

        if not strike or not expiration or not agent_type or not position_id:
            return JSONResponse(
                {"error": "Activity missing required fields (strike, expiration, agent_type, position_id)"},
                status_code=400,
            )

        monitor_type_map = {"open_call_monitor": "call", "open_put_monitor": "put"}
        position_type = monitor_type_map.get(agent_type)
        if position_type is None:
            return JSONResponse(
                {"error": f"Unsupported monitor agent_type '{agent_type}'"},
                status_code=400,
            )

        snapshot = {
            "activity_id": activity["id"],
            "agent_type": activity.get("agent_type"),
            "activity": activity.get("activity"),
            "confidence": activity.get("confidence"),
            "reason": activity.get("reason"),
            "underlying_price": activity.get("underlying_price"),
            "premium": activity.get("premium"),
            "iv": activity.get("iv"),
            "risk_flags": activity.get("risk_flags", []),
            "timestamp": activity.get("timestamp"),
        }

        doc = cosmos.roll_position(
            symbol.upper(), position_id, position_type,
            float(strike), expiration,
            source=snapshot, closing_source=snapshot,
        )

        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/{position_id}/roll")
async def api_manual_roll_position(request: Request, symbol: str,
                                   position_id: str):
    """Manually roll a position to a new strike/expiration without an alert."""
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()

        new_strike = body.get("new_strike")
        new_expiration = body.get("new_expiration")
        if new_strike is None or not new_expiration:
            return JSONResponse(
                {"error": "new_strike and new_expiration are required"},
                status_code=400,
            )

        # Determine position type from existing position
        sym_doc = cosmos.get_symbol(symbol.upper())
        if sym_doc is None:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        pos = None
        for p in sym_doc.get("positions", []):
            if p["position_id"] == position_id:
                pos = p
                break
        if pos is None:
            return JSONResponse(
                {"error": f"Position {position_id} not found"},
                status_code=404,
            )

        notes = body.get("notes", "")

        doc = cosmos.roll_position(
            symbol.upper(), position_id, pos["type"],
            float(new_strike), new_expiration,
            notes=notes,
        )

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
# REST API — Data Views
# ===========================================================================

@app.get("/api/alerts")
async def api_alerts(request: Request, agent_type: str = None,
                     since: str = None, limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        results = cosmos.get_all_alerts(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/activities")
async def api_activities(request: Request, agent_type: str = None,
                         symbol: str = None, since: str = None,
                         limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        if symbol:
            conditions = ["c.doc_type = 'activity'"]
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
            results = cosmos.get_all_activities(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Page Routes — Dashboard
# ===========================================================================

def _build_dashboard_tables(cosmos, all_symbols, all_alerts, all_activities):
    """Build per-agent table data for the dashboard from CosmosDB data."""
    agent_tables = []
    grand_totals = {"today": 0, "week": 0, "month": 0, "total": 0}

    for agent_key, agent_meta in AGENT_TYPES.items():
        is_pm = agent_meta["is_position_monitor"]
        agent_alerts = [s for s in all_alerts
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

        # Layer alerts onto groups
        for alert in agent_alerts:
            sym = alert.get("symbol", "")
            if is_pm:
                strike = (alert.get("current_strike")
                          or alert.get("strike", ""))
                exp = (alert.get("current_expiration")
                       or alert.get("expiration", ""))
                key = f"{sym}_{strike}_{exp}" if strike and exp else sym
                if key not in display_map:
                    display_map[key] = (
                        f"{sym} ${strike} exp {exp}" if strike and exp
                        else sym
                    )
            else:
                key = sym
                display_map.setdefault(key, sym)
            groups.setdefault(key, []).append(alert)

        # Latest activity per key — for health metrics and risk flags
        agent_acts = [d for d in all_activities
                      if d.get("agent_type") == agent_key]
        latest_by_key: Dict[str, Dict] = {}
        for d in agent_acts:
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

        total_counts = _count_by_range(agent_alerts)
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
    scheduler_tz_str = config.get("scheduler", {}).get("timezone", "America/New_York")
    
    # Use scheduler timezone for next_run calculation
    try:
        scheduler_tz = pytz.timezone(scheduler_tz_str)
    except Exception:
        scheduler_tz = pytz.timezone("America/New_York")
        scheduler_tz_str = "America/New_York"

    next_run = ""
    next_run_iso = ""
    if cron_expr:
        try:
            now_tz = datetime.now(scheduler_tz)
            cron = croniter(cron_expr, now_tz)
            next_run_dt = cron.get_next(datetime)
            next_run = next_run_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            next_run_iso = next_run_dt.isoformat()
        except Exception:
            next_run = "Invalid cron"

    cosmos = getattr(request.app.state, "cosmos", None)
    empty_ctx = {
        "request": request,
        "agent_tables": [],
        "grand_totals": {"today": 0, "week": 0, "month": 0, "total": 0},
        "last_run": "", "last_run_iso": "", "next_run": next_run, "next_run_iso": next_run_iso,
        "cron_expr": cron_expr, "scheduler_timezone": scheduler_tz_str,
        "symbol_count": 0, "position_count": 0, "activity": [],
    }
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        empty_ctx["error"] = f"CosmosDB not available: {error_detail}"
        return templates.TemplateResponse("dashboard.html", empty_ctx)

    try:
        all_symbols = cosmos.list_symbols()
        all_alerts = cosmos.get_all_alerts(limit=500)
        all_activities = cosmos.get_all_activities(limit=200)
    except Exception as e:
        empty_ctx["error"] = f"CosmosDB query failed: {e}"
        return templates.TemplateResponse("dashboard.html", empty_ctx)

    # Build set of closed position IDs so we can exclude their data
    closed_position_ids: set = set()
    for sym_cfg in all_symbols:
        for pos in sym_cfg.get("positions", []):
            if pos.get("status") != "active":
                closed_position_ids.add(pos["position_id"])

    # Exclude activities/alerts linked to closed positions from dashboard
    if closed_position_ids:
        closed_activity_ids = {
            d["id"] for d in all_activities
            if d.get("position_id") in closed_position_ids
        }
        all_activities = [
            d for d in all_activities
            if d.get("position_id") not in closed_position_ids
        ]
        all_alerts = [
            s for s in all_alerts
            if s.get("position_id") not in closed_position_ids
            and s.get("activity_id") not in closed_activity_ids
        ]

    symbol_count = len(all_symbols)
    position_count = sum(
        len([p for p in s.get("positions", []) if p.get("status") == "active"])
        for s in all_symbols
    )

    agent_tables, grand_totals = _build_dashboard_tables(
        cosmos, all_symbols, all_alerts, all_activities)

    last_run = ""
    last_run_iso = ""
    if all_activities:
        # Activities store timestamps in ISO format (UTC)
        timestamp_str = all_activities[0].get("timestamp", "")
        if timestamp_str:
            try:
                # Parse the UTC timestamp
                last_run_dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                # Convert to scheduler timezone
                if last_run_dt.tzinfo is None:
                    last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                last_run_dt = last_run_dt.astimezone(scheduler_tz)
                last_run = last_run_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
                last_run_iso = last_run_dt.isoformat()
            except Exception:
                # Fallback to simple string truncation
                last_run = timestamp_str[:19]

    activity = []
    for d in all_activities[:100]:
        agent_key = d.get("agent_type", "")
        d["_agent_label"] = AGENT_TYPES.get(agent_key, {}).get(
            "label", agent_key)
        activity.append(d)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "agent_tables": agent_tables,
        "grand_totals": grand_totals,
        "last_run": last_run,
        "last_run_iso": last_run_iso,
        "next_run": next_run,
        "next_run_iso": next_run_iso,
        "cron_expr": cron_expr,
        "scheduler_timezone": scheduler_tz_str,
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

    # Gather recent activities across all agent types
    activities: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        acts = cosmos.get_recent_activities(
            symbol.upper(), agent_type, max_entries=50)
        for d in acts:
            d["_agent_label"] = meta["label"]
        activities.extend(acts)
    activities.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    activities = activities[:50]

    # Gather recent alerts
    alerts: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        alts = cosmos.get_recent_alerts(
            symbol.upper(), agent_type, max_entries=30)
        for s in alts:
            s["_agent_label"] = meta["label"]
        alerts.extend(alts)
    alerts.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    return templates.TemplateResponse("symbol_detail.html", {
        "request": request,
        "symbol_doc": doc,
        "activities": activities,
        "alerts": alerts,
    })


# ===========================================================================
# Page Routes — Fetch Preview (raw TradingView data)
# ===========================================================================

@app.get("/symbols/{symbol}/fetch-preview", response_class=HTMLResponse)
async def fetch_preview_page(request: Request, symbol: str):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return HTMLResponse(f"Symbol {symbol} not found", status_code=404)

    return templates.TemplateResponse("fetch_preview.html", {
        "request": request,
        "symbol_doc": doc,
    })


@app.get("/api/symbols/{symbol}/fetch-preview")
async def api_fetch_preview(request: Request, symbol: str):
    """Fetch raw TradingView data for a symbol and return as JSON."""
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    full_symbol = doc["exchange"] + "-" + doc["symbol"]

    from src.tv_data_fetcher import TradingViewFetcher
    try:
        async with TradingViewFetcher() as fetcher:
            data = await fetcher.fetch_all(full_symbol)
            stats = fetcher.last_fetch_stats
    except Exception as e:
        logger.exception("Fetch preview failed for %s", full_symbol)
        return JSONResponse({"error": f"Fetch failed: {e}"}, status_code=500)

    resources = {}
    for key in ("overview", "technicals", "forecast", "options_chain"):
        text = data.get(key, "")
        st = stats.get(key, {})
        resources[key] = {
            "text": text,
            "size": st.get("size", len(text)),
            "duration_seconds": st.get("duration", 0),
        }

    return JSONResponse({
        "symbol": full_symbol,
        "resources": resources,
    })


# ===========================================================================
# Page Routes — Activity Detail
# ===========================================================================

@app.get("/activities/{activity_id}", response_class=HTMLResponse)
async def activity_detail_page(request: Request, activity_id: str):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    activity = cosmos.get_activity_by_id(activity_id)
    if not activity:
        return HTMLResponse("Activity not found", status_code=404)

    symbol = activity.get("symbol", "")
    agent_type = activity.get("agent_type", "")
    agent_label = AGENT_TYPES.get(agent_type, {}).get("label", agent_type)
    is_alert = activity.get("is_alert", False)

    # Build display_name from symbol config (for back link)
    sym_doc = cosmos.get_symbol(symbol)
    display_name = sym_doc["display_name"] if sym_doc else symbol

    return templates.TemplateResponse("activity_detail.html", {
        "request": request,
        "activity": activity,
        "symbol": symbol,
        "display_name": display_name,
        "agent_label": agent_label,
        "agent_type": agent_type,
        "is_alert": is_alert,
    })


# ===========================================================================
# Settings - Split Views
# ===========================================================================

@app.get("/settings/config", response_class=HTMLResponse)
async def settings_config_page(request: Request):
    """Configuration page — Scheduler and Telegram."""
    cosmos = getattr(request.app.state, "cosmos", None)
    
    # Try CosmosDB first, fall back to config.yaml
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    if cosmos_settings:
        config = cosmos_settings
    else:
        config = _load_config()
    
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
    timezone = config.get("scheduler", {}).get("timezone", "America/New_York")
    telegram_cfg = config.get("telegram", {})
    telegram_enabled = telegram_cfg.get("enabled", False)
    telegram_bot_token = telegram_cfg.get("bot_token", "")
    telegram_chat_id = telegram_cfg.get("chat_id", "")
    
    # Resolve env vars for display
    if telegram_bot_token.startswith("${"):
        telegram_bot_token = _resolve_env(telegram_bot_token)
    if telegram_chat_id.startswith("${"):
        telegram_chat_id = _resolve_env(telegram_chat_id)
    
    return templates.TemplateResponse("settings_config.html", {
        "request": request,
        "cron_expr": cron_expr,
        "timezone": timezone,
        "telegram_enabled": telegram_enabled,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
    })


@app.post("/settings/config", response_class=HTMLResponse)
async def settings_config_save(request: Request):
    """Save configuration settings."""
    form = await request.form()
    saved: List[str] = []
    cosmos = getattr(request.app.state, "cosmos", None)

    # Cron schedule
    new_cron = str(form.get("cron_expr", "")).strip()
    new_timezone = str(form.get("timezone", "America/New_York")).strip()
    if new_cron:
        try:
            croniter(new_cron)
            
            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("scheduler", {})["cron"] = new_cron
                cosmos_settings.setdefault("scheduler", {})["timezone"] = new_timezone
                _save_settings_to_cosmos(cosmos, cosmos_settings)
            
            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("scheduler", {})["cron"] = new_cron
            config.setdefault("scheduler", {})["timezone"] = new_timezone
            _write_config(config)
            saved.append("Cron schedule")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule(new_cron)
        except (ValueError, KeyError):
            pass

    # Telegram settings
    telegram_enabled = form.get("telegram_enabled") == "true"
    telegram_bot_token = str(form.get("telegram_bot_token", "")).strip()
    telegram_chat_id = str(form.get("telegram_chat_id", "")).strip()

    # Update CosmosDB first
    if cosmos:
        cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
        cosmos_settings.setdefault("telegram", {})
        cosmos_settings["telegram"]["enabled"] = telegram_enabled
        if telegram_bot_token:
            cosmos_settings["telegram"]["bot_token"] = telegram_bot_token
        if telegram_chat_id:
            cosmos_settings["telegram"]["chat_id"] = telegram_chat_id
        _save_settings_to_cosmos(cosmos, cosmos_settings)
    
    # Also update config.yaml for backward compat
    config = _load_config()
    config.setdefault("telegram", {})
    config["telegram"]["enabled"] = telegram_enabled
    if telegram_bot_token:
        config["telegram"]["bot_token"] = telegram_bot_token
    if telegram_chat_id:
        config["telegram"]["chat_id"] = telegram_chat_id
    _write_config(config)
    saved.append("Telegram settings")

    # Re-read for display
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    if cosmos_settings:
        config = cosmos_settings
    else:
        config = _load_config()
    
    cron_expr = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
    timezone = config.get("scheduler", {}).get("timezone", "America/New_York")
    telegram_cfg = config.get("telegram", {})
    tg_enabled = telegram_cfg.get("enabled", False)
    tg_bot_token = telegram_cfg.get("bot_token", "")
    tg_chat_id = telegram_cfg.get("chat_id", "")
    if tg_bot_token.startswith("${"):
        tg_bot_token = _resolve_env(tg_bot_token)
    if tg_chat_id.startswith("${"):
        tg_chat_id = _resolve_env(tg_chat_id)

    return templates.TemplateResponse("settings_config.html", {
        "request": request,
        "cron_expr": cron_expr,
        "timezone": timezone,
        "saved": saved,
        "telegram_enabled": tg_enabled,
        "telegram_bot_token": tg_bot_token,
        "telegram_chat_id": tg_chat_id,
    })


@app.get("/settings/runtime", response_class=HTMLResponse)
async def settings_runtime_page(request: Request):
    """Runtime stats page — Agent runs and fetch statistics."""
    cosmos = getattr(request.app.state, "cosmos", None)
    
    telemetry_stats = {}
    if cosmos:
        try:
            telemetry_stats = cosmos.get_telemetry_stats()
        except Exception:
            pass
    
    return templates.TemplateResponse("settings_runtime.html", {
        "request": request,
        "telemetry_stats": telemetry_stats,
    })


@app.get("/settings/debug", response_class=HTMLResponse)
async def settings_debug_page(request: Request):
    """Debug page — TradingView fetch and CosmosDB diagnostics."""
    cosmos = getattr(request.app.state, "cosmos", None)
    
    # CosmosDB connection info
    config = _load_config()
    cosmos_endpoint = _resolve_env(config.get("cosmosdb", {}).get("endpoint", ""))
    cosmos_database = config.get("cosmosdb", {}).get("database", "stock-options-manager")
    cosmos_status = "Connected" if cosmos else "Not connected"
    cosmos_error = getattr(request.app.state, "cosmos_error", None)
    
    # Get symbols for debug dropdown
    symbols = []
    if cosmos:
        try:
            symbols = cosmos.list_symbols()
        except Exception:
            pass
    
    return templates.TemplateResponse("settings_debug.html", {
        "request": request,
        "cosmos_endpoint": cosmos_endpoint,
        "cosmos_database": cosmos_database,
        "cosmos_status": cosmos_status,
        "cosmos_error": cosmos_error,
        "symbols": symbols,
    })


# Redirect old /settings to /settings/config for backward compatibility
@app.get("/settings", response_class=HTMLResponse)
async def settings_redirect(request: Request):
    """Redirect old settings URL to config page."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/settings/config", status_code=301)


# ===========================================================================
# Telegram Test
# ===========================================================================

@app.post("/api/telegram/test")
async def telegram_test(request: Request):
    """Send a test message via Telegram."""
    config = _load_config()
    telegram_cfg = config.get("telegram", {})
    if not telegram_cfg.get("enabled"):
        return JSONResponse({"ok": False, "error": "Telegram not enabled"})

    bot_token = _resolve_env(telegram_cfg.get("bot_token", ""))
    chat_id = _resolve_env(telegram_cfg.get("chat_id", ""))

    if not bot_token or not chat_id:
        return JSONResponse({"ok": False, "error": "Bot token or chat ID missing"})

    try:
        import requests as req
        resp = req.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Stock Options Manager — Telegram notifications are working!", "parse_mode": "HTML"},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": data.get("description", "Unknown error")})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


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
                alerts = cosmos.get_all_alerts(
                    agent_type=agent_key, limit=20)
                activities = cosmos.get_all_activities(
                    agent_type=agent_key, limit=20)
                if not alerts and not activities:
                    continue

                context_parts.append(f"\n--- {meta['label']} ---")

                # Group by symbol
                sym_data: Dict[str, Dict[str, list]] = defaultdict(
                    lambda: {"alerts": [], "activities": []})
                for s in alerts:
                    sym_data[s.get("symbol", "?")]["alerts"].append(s)
                for d in activities:
                    sym_data[d.get("symbol", "?")]["activities"].append(d)

                for sym, data in sym_data.items():
                    context_parts.append(f"\n## {sym}")
                    if data["alerts"]:
                        context_parts.append(
                            f"Alerts (last {len(data['alerts'])}):")
                        for s in data["alerts"][:2]:
                            context_parts.append(
                                json.dumps(_clean_doc(s), indent=2,
                                           default=str))
                    if data["activities"]:
                        context_parts.append(
                            f"Activities (last {len(data['activities'])}):")
                        for d in data["activities"][:4]:
                            context_parts.append(
                                json.dumps(_clean_doc(d), indent=2,
                                           default=str))
        except Exception:
            context_parts.append("(Error loading context from CosmosDB)")

    context_text = ("\n".join(context_parts) if context_parts
                    else "No recent activities available.")

    system_prompt = (
        "You are a stock options manager advisor. You have access to recent "
        "analysis activities for the user's portfolio. Answer questions about "
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
            activities: List[Dict] = []
            for agent_type, meta in AGENT_TYPES.items():
                acts = cosmos.get_recent_activities(
                    symbol, agent_type, max_entries=5)
                for d in acts:
                    d["_agent_label"] = meta["label"]
                activities.extend(acts)
            activities.sort(key=lambda d: d.get("timestamp", ""),
                            reverse=True)
            activities = activities[:5]

            if activities:
                context_parts.append("\n--- Recent Activities ---")
                for d in activities:
                    context_parts.append(json.dumps(
                        _clean_doc(d), indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load activities: %s", exc)
            context_parts.append("(Error loading activities from CosmosDB)")

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
        f"1. Recent analysis activities for this symbol\n"
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
