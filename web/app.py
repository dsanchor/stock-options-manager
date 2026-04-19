import asyncio
import json
import logging
import os
import re
import threading
import time
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
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    counts = {"today": 0, "week": 0, "month": 0, "total": len(entries)}
    for e in entries:
        ts = parse_timestamp(e.get("timestamp", ""))
        if ts is None:
            continue
        if ts >= today_start:
            counts["today"] += 1
        if ts >= seven_days_ago:
            counts["week"] += 1
        if ts >= thirty_days_ago:
            counts["month"] += 1
    return counts


_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean_doc(doc: dict) -> dict:
    """Strip CosmosDB system properties for API responses."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _format_time_dual_tz(dt: datetime, tz_str: str) -> str:
    """Format datetime showing both configured timezone and UTC.
    
    Args:
        dt: timezone-aware datetime object
        tz_str: configured timezone string (e.g., 'America/New_York')
    
    Returns:
        Formatted string: "YYYY-MM-DD HH:MM TZ (HH:MM UTC)"
    """
    if dt is None:
        return ""
    
    try:
        # Ensure dt is timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Convert to configured timezone
        tz = pytz.timezone(tz_str)
        dt_tz = dt.astimezone(tz)
        
        # Convert to UTC
        dt_utc = dt.astimezone(timezone.utc)
        
        # Format: "2026-04-01 08:00 PST (16:00 UTC)"
        tz_abbr = dt_tz.strftime("%Z")
        formatted = f"{dt_tz.strftime('%Y-%m-%d %H:%M')} {tz_abbr} ({dt_utc.strftime('%H:%M')} UTC)"
        
        return formatted
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


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
        if "telegram_notifications_enabled" in body:
            doc["telegram_notifications_enabled"] = bool(body["telegram_notifications_enabled"])

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        updated = cosmos.container.replace_item(item=doc["id"], body=doc)

        # Activities are kept when watchlist agents are toggled OFF.
        # CosmosDB TTL (30 days) handles cleanup automatically.

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
        source_activity_id = body.get("source_activity_id", "").strip() if body.get("source_activity_id") else ""

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

        source = None
        if source_activity_id:
            activity = cosmos.get_activity_by_id(source_activity_id)
            if activity is not None:
                source = {
                    "source_type": "manual_with_alert",
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

        doc = cosmos.add_position(symbol.upper(), position_type, strike,
                                  expiration, notes, source=source)
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
    """Create a position from an existing activity and disable watchlist.
    Activities are preserved (CosmosDB TTL handles cleanup)."""
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
    """Manually roll a position to a new strike/expiration, optionally attaching alert data."""
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
        source_activity_id = body.get("source_activity_id", "").strip() if body.get("source_activity_id") else ""

        # Build source from activity if provided
        source = None
        if source_activity_id:
            activity = cosmos.get_activity_by_id(source_activity_id)
            if activity is not None:
                source = {
                    "source_type": "manual_with_alert",
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
            symbol.upper(), position_id, pos["type"],
            float(new_strike), new_expiration,
            source=source,
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


@app.patch("/api/symbols/{symbol}/positions/{position_id}/notes")
async def api_update_position_notes(request: Request, symbol: str,
                                    position_id: str):
    """Update notes on a position."""
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        notes = body.get("notes", "")
        if not isinstance(notes, str):
            return JSONResponse({"error": "notes must be a string"},
                                status_code=400)
        doc = cosmos.update_position_notes(symbol.upper(), position_id, notes)
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
        # Filter out SKIPPED activities so we show meaningful data
        agent_acts = [d for d in all_activities
                      if d.get("agent_type") == agent_key
                      and d.get("activity", "").upper() != "SKIPPED"]
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

        # Sort position monitors by DTE ascending (soonest expiration first)
        if is_pm:
            rows.sort(key=lambda r: (r.get("dte") is None, r.get("dte") or 0))

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
    cosmos = getattr(request.app.state, "cosmos", None)

    empty_ctx = {
        "request": request,
        "agent_tables": [],
        "grand_totals": {"today": 0, "week": 0, "month": 0, "total": 0},
        "symbol_count": 0, "position_count": 0, "activity": [],
        "agent_types": AGENT_TYPES,
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

    activity = []
    for d in all_activities[:100]:
        agent_key = str(d.get("agent_type", ""))
        d["_agent_key"] = agent_key
        d["_agent_label"] = AGENT_TYPES.get(agent_key, {}).get(
            "label", agent_key)
        activity.append(d)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "agent_tables": agent_tables,
        "grand_totals": grand_totals,
        "symbol_count": symbol_count,
        "position_count": position_count,
        "activity": activity,
        "agent_types": AGENT_TYPES,
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

    # Gather recent activities AND alerts across all agent types (unified list)
    activities: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        # Get non-alert activities
        acts = cosmos.get_recent_activities(
            symbol.upper(), agent_type, max_entries=50)
        for d in acts:
            d["_agent_key"] = str(d.get("agent_type", ""))
            d["_agent_label"] = meta["label"]
        activities.extend(acts)
        
        # Get alerts
        alts = cosmos.get_recent_alerts(
            symbol.upper(), agent_type, max_entries=30)
        for s in alts:
            s["_agent_key"] = str(s.get("agent_type", ""))
            s["_agent_label"] = meta["label"]
        activities.extend(alts)
    
    # Sort unified list by timestamp and cap at ~80 items
    activities.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    activities = activities[:80]

    # Enrich open positions with latest monitor data (assignment_risk, moneyness)
    _monitor_agents = {"open_call_monitor", "open_put_monitor"}
    latest_monitor: Dict[str, Dict] = {}  # position_id -> latest activity
    for act in activities:
        pid = act.get("position_id")
        if pid and act.get("agent_type") in _monitor_agents and pid not in latest_monitor:
            latest_monitor[pid] = act
    for pos in doc.get("positions", []):
        mon = latest_monitor.get(pos.get("position_id"))
        if mon:
            pos["_assignment_risk"] = mon.get("assignment_risk")
            pos["_moneyness"] = mon.get("moneyness")

    # Gather alerts separately for latest_sell_alerts computation
    alerts: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        alts = cosmos.get_recent_alerts(
            symbol.upper(), agent_type, max_entries=30)
        for s in alts:
            s["_agent_label"] = meta["label"]
        alerts.extend(alts)
    alerts.sort(key=lambda s: s.get("timestamp", ""), reverse=True)

    # Latest SELL alert per watchlist agent type (for position pre-fill)
    latest_sell_alerts: Dict[str, Dict | None] = {
        "covered_call": None,
        "cash_secured_put": None,
    }
    for alt in alerts:
        at = alt.get("agent_type")
        if at in latest_sell_alerts and latest_sell_alerts[at] is None:
            latest_sell_alerts[at] = {
                "agent_type": at,
                "strike": alt.get("strike"),
                "expiration": alt.get("expiration"),
                "premium": alt.get("premium"),
                "confidence": alt.get("confidence"),
                "reason": alt.get("reason"),
                "iv": alt.get("iv"),
                "underlying_price": alt.get("underlying_price"),
                "risk_flags": alt.get("risk_flags", []),
                "activity_id": alt.get("activity_id"),
                "timestamp": alt.get("timestamp"),
            }

    return templates.TemplateResponse("symbol_detail.html", {
        "request": request,
        "symbol_doc": doc,
        "activities": activities,
        "alerts": alerts,
        "latest_sell_alerts": latest_sell_alerts,
        "agent_types": AGENT_TYPES,
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


# ===========================================================================
# API — Symbol Position Report (LLM-generated)
# ===========================================================================

@app.post("/api/symbols/{symbol}/report")
async def symbol_report_api(request: Request, symbol: str):
    """Generate a comprehensive position/situation report for a symbol.

    Uses the ReportAgent (same pattern as other agents) to produce a
    structured markdown report from cached TradingView data + CosmosDB
    activities/alerts.
    """
    symbol = symbol.upper()

    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    symbol_doc = cosmos.get_symbol(symbol)
    if not symbol_doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    # Build an AgentRunner on demand (no scheduler dependency)
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
        from src.agent_runner import AgentRunner
        from src.report_agent import run_report_analysis
        from src.config import Config

        runner = AgentRunner(
            project_endpoint=endpoint,
            model=model,
            api_key=api_key,
        )
        config_obj = Config()

        result = await run_report_analysis(
            config=config_obj,
            runner=runner,
            cosmos=cosmos,
            symbol=symbol,
        )

        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=404)

        return JSONResponse(result)

    except Exception as e:
        logger.exception("Report generation failed for %s", symbol)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/symbols/{symbol}/report", response_class=HTMLResponse)
async def symbol_report_page(request: Request, symbol: str):
    """Render the dedicated report page for a symbol."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return HTMLResponse(f"Symbol {symbol} not found", status_code=404)

    return templates.TemplateResponse("symbol_report.html", {
        "request": request,
        "symbol_doc": doc,
    })


@app.get("/symbols/{symbol}/options-chain", response_class=HTMLResponse)
async def symbol_options_chain_page(request: Request, symbol: str):
    """Render the option chain visualisation page for a symbol."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return HTMLResponse(f"CosmosDB not available: {error_detail}",
                            status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return HTMLResponse(f"Symbol {symbol} not found", status_code=404)

    return templates.TemplateResponse("symbol_options_chain.html", {
        "request": request,
        "symbol_doc": doc,
    })


@app.get("/api/symbols/{symbol}/options-chain")
async def api_symbol_options_chain(request: Request, symbol: str):
    """Return parsed option chain data from the TV cache."""
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    from src.tv_cache import get_tv_cache
    cache = get_tv_cache()
    # Cache key uses exchange-symbol format (e.g. "NASDAQ-MSFT") to match
    # how the scheduler and fetch_all store the data.
    cache_key = doc.get("exchange", "NASDAQ") + "-" + doc["symbol"]
    entry = cache.get(cache_key, "options_chain")

    # Fallback: if no cached data, fetch live and populate cache
    if entry is None or not entry.data:
        try:
            from src.tv_data_fetcher import create_fetcher
            from src.config import Config
            config = Config()
            full_sym = doc.get("exchange", "NASDAQ") + ":" + doc["symbol"]
            async with create_fetcher(config) as fetcher:
                raw_data = await fetcher.fetch_options_chain(full_sym)
                if raw_data and not raw_data.startswith("[ERROR"):
                    # Validate the data is parseable before caching
                    from src.options_chain_parser import parse_options_chain as _test_parse
                    test_result = _test_parse(raw_data, symbol.upper())
                    if test_result["calls"] or test_result["puts"]:
                        cache.set(cache_key, "options_chain", raw_data,
                                  {"source": "live_fallback"})
                        entry = cache.get(cache_key, "options_chain")
                    else:
                        logger.warning("Live fetch returned unparseable data for %s (length=%d)",
                                       symbol, len(raw_data))
        except Exception as e:
            logger.exception("Live options chain fetch failed for %s", symbol)
            return JSONResponse(
                {"error": f"Failed to fetch options chain: {e}", "symbol": symbol.upper()},
                status_code=500,
            )

    if entry is None or not entry.data:
        return JSONResponse(
            {"error": "No options chain data available. Try running a full analysis first, or wait for the options chain scheduler.",
             "symbol": symbol.upper()},
            status_code=404,
        )

    raw = entry.data
    from src.options_chain_parser import parse_options_chain
    result = parse_options_chain(raw, symbol.upper())

    if not result["calls"] and not result["puts"]:
        logger.error("Failed to parse options chain for %s (raw length=%d, first 500 chars=%s)",
                      symbol, len(raw) if raw else 0, repr(raw[:500]) if raw else "empty")
        return JSONResponse(
            {"error": "Failed to parse options chain data", "symbol": symbol.upper()},
            status_code=404,
        )

    return JSONResponse({
        "symbol": symbol.upper(),
        "timestamp": result["timestamp"],
        "cache_age_seconds": round(time.time() - entry.timestamp, 1),
        "calls": result["calls"],
        "puts": result["puts"],
    })


@app.get("/api/symbols/{symbol}/fetch-preview")
async def api_fetch_preview(request: Request, symbol: str):
    """Fetch raw TradingView data for a symbol and return as JSON.
    
    Always forces a fresh fetch (debug endpoint).
    """
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    full_symbol = doc["exchange"] + "-" + doc["symbol"]

    from src.tv_data_fetcher import create_fetcher
    from src.tv_cache import get_tv_cache
    from src.config import Config
    try:
        config = Config()
        async with create_fetcher(config) as fetcher:
            data = await fetcher.fetch_all(full_symbol,
                                           force_refresh=True,
                                           cache=get_tv_cache())
            stats = fetcher.last_fetch_stats
    except Exception as e:
        logger.exception("Fetch preview failed for %s", full_symbol)
        return JSONResponse({"error": f"Fetch failed: {e}"}, status_code=500)

    resources = {}
    for key in ("overview", "technicals", "forecast", "dividends", "options_chain"):
        text = data.get(key, "")
        st = stats.get(key, {})
        resources[key] = {
            "text": text,
            "size": st.get("size", len(text)),
            "duration_seconds": st.get("duration", 0),
            "error": st.get("error", False),
            "cached": st.get("cached", False),
        }

    return JSONResponse({
        "symbol": full_symbol,
        "resources": resources,
        "cached_resources": data.get("cached_resources", []),
    })


@app.get("/api/cache/status")
async def cache_status():
    """Return TradingView cache statistics."""
    from src.tv_cache import get_tv_cache
    cache = get_tv_cache()
    info = cache.stats()
    # Add per-symbol detail
    detail = {}
    for sym in info["symbols"]:
        entries = cache.get_all(sym)
        detail[sym] = {
            res: {
                "size": entry.fetch_stats.get("size", len(entry.data)),
                "age_seconds": round(time.time() - entry.timestamp, 1),
            }
            for res, entry in entries.items()
        }
    info["detail"] = detail
    return JSONResponse(info)


@app.delete("/api/cache")
async def cache_clear(request: Request):
    """Clear TradingView cache.  Pass ``{"symbol": "NYSE-MO"}`` to clear
    a single symbol, or empty body to clear everything."""
    from src.tv_cache import get_tv_cache
    cache = get_tv_cache()
    try:
        body = await request.json()
    except Exception:
        body = {}
    sym = body.get("symbol")
    if sym:
        cache.clear(sym)
        return JSONResponse({"cleared": sym})
    cache.clear_all()
    return JSONResponse({"cleared": "all"})

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
    
    # Summary agent settings
    summary_cfg = config.get("summary_agent", {})
    summary_enabled = summary_cfg.get("enabled", True)
    summary_cron = summary_cfg.get("cron", "0 8 * * *")
    summary_activity_count = summary_cfg.get("activity_count", 3)
    
    # Options chain scheduler settings
    options_chain_cfg = config.get("options_chain_scheduler", {})
    options_chain_enabled = options_chain_cfg.get("enabled", True)
    options_chain_cron = options_chain_cfg.get("cron", "0 * * * *")
    
    # Resolve env vars for display
    if telegram_bot_token.startswith("${"):
        telegram_bot_token = _resolve_env(telegram_bot_token)
    if telegram_chat_id.startswith("${"):
        telegram_chat_id = _resolve_env(telegram_chat_id)
    
    # Calculate scheduler times for Monitoring Agent
    try:
        tz = pytz.timezone(timezone)
    except Exception:
        tz = pytz.timezone("America/New_York")
    
    monitoring_last_run = ""
    monitoring_next_run = ""
    
    # Get last run from most recent activity
    if cosmos:
        try:
            all_activities = cosmos.get_all_activities(limit=1)
            if all_activities:
                timestamp_str = all_activities[0].get("timestamp", "")
                if timestamp_str:
                    try:
                        last_run_dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if last_run_dt.tzinfo is None:
                            last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                        monitoring_last_run = _format_time_dual_tz(last_run_dt, timezone)
                    except Exception:
                        pass
        except Exception:
            pass
    
    # Calculate next run from cron
    if cron_expr:
        try:
            now_tz = datetime.now(tz)
            cron = croniter(cron_expr, now_tz)
            next_run_dt = cron.get_next(datetime)
            monitoring_next_run = _format_time_dual_tz(next_run_dt, timezone)
        except Exception:
            monitoring_next_run = "Invalid cron"
    
    # Calculate scheduler times for Summarization Agent
    summary_last_run = ""
    summary_next_run = ""
    
    # For summary agent, we'd need to track summary-specific runs
    # For now, we'll just calculate next run from cron
    if summary_cron:
        try:
            now_tz = datetime.now(tz)
            cron = croniter(summary_cron, now_tz)
            next_run_dt = cron.get_next(datetime)
            summary_next_run = _format_time_dual_tz(next_run_dt, timezone)
        except Exception:
            summary_next_run = "Invalid cron"
    
    # Calculate scheduler times for Options Chain Scheduler
    options_chain_last_run = ""
    options_chain_next_run = ""
    
    if options_chain_cron:
        try:
            now_tz = datetime.now(tz)
            cron = croniter(options_chain_cron, now_tz)
            next_run_dt = cron.get_next(datetime)
            options_chain_next_run = _format_time_dual_tz(next_run_dt, timezone)
        except Exception:
            options_chain_next_run = "Invalid cron"
    
    return templates.TemplateResponse("settings_config.html", {
        "request": request,
        "cron_expr": cron_expr,
        "timezone": timezone,
        "telegram_enabled": telegram_enabled,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
        "summary_enabled": summary_enabled,
        "summary_cron": summary_cron,
        "summary_activity_count": summary_activity_count,
        "monitoring_last_run": monitoring_last_run,
        "monitoring_next_run": monitoring_next_run,
        "summary_last_run": summary_last_run,
        "summary_next_run": summary_next_run,
        "options_chain_enabled": options_chain_enabled,
        "options_chain_cron": options_chain_cron,
        "options_chain_last_run": options_chain_last_run,
        "options_chain_next_run": options_chain_next_run,
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
                scheduler.reschedule(new_cron, new_timezone)
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

    # Summary agent settings
    summary_enabled = form.get("summary_enabled") == "true"
    summary_cron = str(form.get("summary_cron", "0 8 * * *")).strip()
    summary_activity_count_str = str(form.get("summary_activity_count", "3")).strip()
    try:
        summary_activity_count = int(summary_activity_count_str)
        summary_activity_count = max(1, min(10, summary_activity_count))  # Clamp to 1-10
    except ValueError:
        summary_activity_count = 3
    
    # Validate cron if provided
    if summary_cron:
        try:
            croniter(summary_cron)
            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("summary_agent", {})
                cosmos_settings["summary_agent"]["enabled"] = summary_enabled
                cosmos_settings["summary_agent"]["cron"] = summary_cron
                cosmos_settings["summary_agent"]["activity_count"] = summary_activity_count
                _save_settings_to_cosmos(cosmos, cosmos_settings)
            
            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("summary_agent", {})
            config["summary_agent"]["enabled"] = summary_enabled
            config["summary_agent"]["cron"] = summary_cron
            config["summary_agent"]["activity_count"] = summary_activity_count
            _write_config(config)
            saved.append("Summary agent")
            
            # Notify scheduler of change
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_summary(summary_cron)
        except (ValueError, KeyError):
            pass

    # Options chain scheduler settings
    options_chain_enabled = form.get("options_chain_enabled") == "true"
    options_chain_cron = str(form.get("options_chain_cron", "0 * * * *")).strip()
    
    # Validate cron if provided
    if options_chain_cron:
        try:
            croniter(options_chain_cron)
            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("options_chain_scheduler", {})
                cosmos_settings["options_chain_scheduler"]["enabled"] = options_chain_enabled
                cosmos_settings["options_chain_scheduler"]["cron"] = options_chain_cron
                _save_settings_to_cosmos(cosmos, cosmos_settings)
            
            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("options_chain_scheduler", {})
            config["options_chain_scheduler"]["enabled"] = options_chain_enabled
            config["options_chain_scheduler"]["cron"] = options_chain_cron
            _write_config(config)
            saved.append("Options chain scheduler")
            
            # Notify scheduler of change
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_options_chain(options_chain_cron)
        except (ValueError, KeyError):
            pass

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
    
    # Summary agent settings
    summary_cfg = config.get("summary_agent", {})
    sum_enabled = summary_cfg.get("enabled", True)
    sum_cron = summary_cfg.get("cron", "0 8 * * *")
    sum_activity_count = summary_cfg.get("activity_count", 3)
    
    # Options chain scheduler settings
    options_chain_cfg = config.get("options_chain_scheduler", {})
    oc_enabled = options_chain_cfg.get("enabled", True)
    oc_cron = options_chain_cfg.get("cron", "0 * * * *")

    return templates.TemplateResponse("settings_config.html", {
        "request": request,
        "cron_expr": cron_expr,
        "timezone": timezone,
        "saved": saved,
        "telegram_enabled": tg_enabled,
        "telegram_bot_token": tg_bot_token,
        "telegram_chat_id": tg_chat_id,
        "summary_enabled": sum_enabled,
        "summary_cron": sum_cron,
        "summary_activity_count": sum_activity_count,
        "options_chain_enabled": oc_enabled,
        "options_chain_cron": oc_cron,
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

    # Compute last_run / next_run for scheduler status
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    config = cosmos_settings if cosmos_settings else _load_config()
    cron_expr = config.get("scheduler", {}).get("cron", "")
    scheduler_tz_str = config.get("scheduler", {}).get("timezone", "America/New_York")
    try:
        scheduler_tz = pytz.timezone(scheduler_tz_str)
    except Exception:
        scheduler_tz = pytz.timezone("America/New_York")
        scheduler_tz_str = "America/New_York"

    next_run = ""
    if cron_expr:
        try:
            now_tz = datetime.now(scheduler_tz)
            cron = croniter(cron_expr, now_tz)
            next_run_dt = cron.get_next(datetime)
            next_run = _format_time_dual_tz(next_run_dt, scheduler_tz_str)
        except Exception:
            next_run = "Invalid cron"

    last_run = ""
    if cosmos:
        try:
            all_activities = cosmos.get_all_activities(limit=1)
            if all_activities:
                timestamp_str = all_activities[0].get("timestamp", "")
                if timestamp_str:
                    try:
                        last_run_dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if last_run_dt.tzinfo is None:
                            last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                        last_run = _format_time_dual_tz(last_run_dt, scheduler_tz_str)
                    except Exception:
                        last_run = timestamp_str[:19]
        except Exception:
            pass

    return templates.TemplateResponse("settings_runtime.html", {
        "request": request,
        "telemetry_stats": telemetry_stats,
        "last_run": last_run,
        "next_run": next_run,
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
    cosmos = getattr(request.app.state, "cosmos", None)
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    config = cosmos_settings if cosmos_settings else _load_config()
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


def _run_agent_in_background(agent_type: str, scheduler, symbol: str = None):
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
                         scheduler.cosmos, scheduler.context_provider,
                         symbol=symbol))
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

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    symbol = body.get("symbol")

    thread = threading.Thread(
        target=_run_agent_in_background,
        args=(agent_type, scheduler, symbol),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": agent_type, "symbol": symbol})


# ---------------------------------------------------------------------------
# Full analysis — sequential execution of all 4 agents
# ---------------------------------------------------------------------------

_FULL_ANALYSIS_AGENT_ORDER = [
    "covered_call", "cash_secured_put", "open_call_monitor", "open_put_monitor"
]


def _default_full_analysis_status() -> dict:
    return {"running": False, "current": None, "completed": [], "total": 4, "errors": []}


def _run_all_agents_sequentially(scheduler, status: dict):
    """Run all 4 agent types sequentially in a single thread."""
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

    for agent_type in _FULL_ANALYSIS_AGENT_ORDER:
        status["current"] = agent_type
        try:
            asyncio.run(funcs[agent_type](
                scheduler.config, scheduler.runner,
                scheduler.cosmos, scheduler.context_provider,
            ))
            status["completed"].append(agent_type)
        except Exception as e:
            logger.error("Full analysis error running %s: %s", agent_type, e)
            status["errors"].append({"agent": agent_type, "error": str(e)})
            status["completed"].append(agent_type)

    status["running"] = False
    status["current"] = None

    # Auto-reset status after 30 seconds
    def _reset():
        import time
        time.sleep(30)
        status.clear()
        status.update(_default_full_analysis_status())

    threading.Thread(target=_reset, daemon=True).start()


@app.post("/api/trigger-all")
async def trigger_all_agents(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger agents"},
            status_code=503)

    status = getattr(request.app.state, "_full_analysis_status", None)
    if status and status.get("running"):
        return JSONResponse(
            {"error": "Full analysis already running", "status": status},
            status_code=409)

    status = _default_full_analysis_status()
    status["running"] = True
    request.app.state._full_analysis_status = status

    thread = threading.Thread(
        target=_run_all_agents_sequentially,
        args=(scheduler, status),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "started"})


@app.get("/api/trigger-all/status")
async def trigger_all_status(request: Request):
    status = getattr(request.app.state, "_full_analysis_status", None)
    if status is None:
        return JSONResponse(_default_full_analysis_status())
    return JSONResponse(dict(status))


# ===========================================================================
# Chat
# ===========================================================================

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.post("/api/chat/fetch-symbol")
async def fetch_symbol_data(request: Request):
    """Fetch TradingView data for a symbol without saving to database.
    
    Uses cache by default.  Pass ``"refresh": true`` in the JSON body
    to force a fresh fetch from TradingView.
    """
    body = await request.json()
    symbol = body.get("symbol", "").strip().upper()
    market = body.get("market", "").strip().upper()
    option_type = body.get("option_type", "").strip().lower()
    force_refresh = body.get("refresh", False)
    
    if not symbol or not market:
        return JSONResponse(
            {"error": "Symbol and market are required"},
            status_code=400
        )
    
    if option_type not in ("call", "put"):
        return JSONResponse(
            {"error": "Option type must be 'call' or 'put'"},
            status_code=400
        )
    
    # Format as MARKET-SYMBOL (e.g., NYSE-AAPL)
    full_symbol = f"{market}-{symbol}"
    
    try:
        # Import and use TradingViewFetcher
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from tv_data_fetcher import create_fetcher
        from tv_cache import get_tv_cache
        from config import Config
        
        config = Config()
        async with create_fetcher(config) as fetcher:
            data = await fetcher.fetch_all(full_symbol,
                                           force_refresh=force_refresh,
                                           cache=get_tv_cache())
            
            if data.get("tv_403", False):
                return JSONResponse(
                    {"error": "TradingView returned 403 (rate limit or block). Please try again later."},
                    status_code=403
                )
            
            return JSONResponse({
                "symbol": symbol,
                "market": market,
                "option_type": option_type,
                "full_symbol": full_symbol,
                "data": data,
                "cached_resources": data.get("cached_resources", []),
            })
            
    except Exception as e:
        logger.error("Error fetching symbol data: %s", e, exc_info=True)
        return JSONResponse(
            {"error": f"Failed to fetch data: {str(e)}"},
            status_code=500
        )


@app.post("/api/chat")
async def chat_api(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    mode = body.get("mode", "portfolio")
    symbol_data = body.get("symbol_data")
    first_analysis = body.get("first_analysis", False)
    
    if not messages and not first_analysis:
        return JSONResponse({"error": "No messages provided"},
                            status_code=400)

    context_parts: List[str] = []
    
    # Build context based on mode
    if mode == "portfolio":
        # Existing portfolio mode logic
        cosmos = getattr(request.app.state, "cosmos", None)
        if cosmos:
            try:
                # Build set of closed position IDs to exclude
                closed_position_ids: set = set()
                all_symbols = cosmos.list_symbols() if cosmos else []
                for sym_cfg in all_symbols:
                    for pos in sym_cfg.get("positions", []):
                        if pos.get("status") != "active":
                            closed_position_ids.add(pos["position_id"])

                for agent_key, meta in AGENT_TYPES.items():
                    alerts = cosmos.get_all_alerts(
                        agent_type=agent_key, limit=20)
                    activities = cosmos.get_all_activities(
                        agent_type=agent_key, limit=20)

                    # Filter out activities/alerts linked to closed positions
                    if closed_position_ids:
                        activities = [d for d in activities
                                      if d.get("position_id") not in closed_position_ids]
                        closed_activity_ids = {d.get("id") for d in cosmos.get_all_activities(
                            agent_type=agent_key, limit=100)
                            if d.get("position_id") in closed_position_ids}
                        alerts = [s for s in alerts
                                  if s.get("position_id") not in closed_position_ids
                                  and s.get("activity_id") not in closed_activity_ids]

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
    
    elif mode == "quick-analysis":
        # Quick analysis mode using fetched symbol data
        if not symbol_data:
            return JSONResponse(
                {"error": "Symbol data required for quick analysis mode"},
                status_code=400
            )
        
        symbol = symbol_data.get("symbol", "?")
        market = symbol_data.get("market", "?")
        option_type = symbol_data.get("option_type", "call")
        data = symbol_data.get("data", {})
        
        # Build context from fetched data
        context_parts.append(f"Symbol: {market}:{symbol}\n")
        
        if "overview" in data and data["overview"]:
            context_parts.append("=== OVERVIEW PAGE ===")
            context_parts.append(data["overview"])
        
        if "technicals" in data and data["technicals"]:
            context_parts.append("\n=== TECHNICALS PAGE ===")
            context_parts.append(data["technicals"])
        
        if "forecast" in data and data["forecast"]:
            context_parts.append("\n=== FORECAST PAGE ===")
            context_parts.append(data["forecast"])
        
        if "dividends" in data and data["dividends"]:
            context_parts.append("\n=== DIVIDENDS ===")
            context_parts.append(data["dividends"])
        
        if "options_chain" in data and data["options_chain"]:
            context_parts.append("\n=== OPTIONS CHAIN ===")
            context_parts.append(data["options_chain"])
        
        context_text = "\n\n".join(context_parts)
        
        # For first analysis, use conversational chat instructions (not monitoring agent JSON output)
        if first_analysis:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "src"))
            
            if option_type == "call":
                from tv_open_call_chat_instructions import TV_OPEN_CALL_CHAT_INSTRUCTIONS
                instructions = TV_OPEN_CALL_CHAT_INSTRUCTIONS
            else:  # put
                from tv_open_put_chat_instructions import TV_OPEN_PUT_CHAT_INSTRUCTIONS
                instructions = TV_OPEN_PUT_CHAT_INSTRUCTIONS
            
            system_prompt = f"{instructions}\n\n{context_text}"
        else:
            # Normal chat mode after first analysis
            system_prompt = (
                f"You are a friendly and knowledgeable options analyst discussing {option_type} options for {market}:{symbol}. "
                "Provide conversational, human-friendly responses. Use the TradingView data provided below to answer questions about "
                "the stock's price, technicals, earnings, dividends, and options. Avoid JSON or structured output — talk naturally.\n\n"
                f"TradingView Data:\n{context_text}"
            )
    
    else:
        return JSONResponse(
            {"error": f"Invalid mode: {mode}"},
            status_code=400
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


async def _build_symbol_context(symbol: str, cosmos, 
                                preferences: dict = None,
                                force_refresh: bool = False) -> dict:
    """Build context data for a symbol (CosmosDB + TradingView).
    
    Args:
        symbol: Stock symbol
        cosmos: CosmosDB client
        preferences: Dict with keys: tradingview, positions, activities (all bool)
        force_refresh: When True, bypass TradingView cache.

    Returns dict with keys: context, exchange, display_name, cached_resources.
    """
    # Default to all enabled for backward compatibility
    if preferences is None:
        preferences = {
            'tradingview': True,
            'positions': True,
            'activities': True
        }
    
    context_parts: List[str] = []
    symbol_doc = None
    exchange = "NYSE"
    cached_resources: list = []

    if cosmos:
        try:
            symbol_doc = cosmos.get_symbol(symbol)
            if symbol_doc:
                exchange = symbol_doc.get("exchange", "NYSE")
                # Only include positions if requested
                if preferences.get('positions', True):
                    context_parts.append("--- Symbol Config ---")
                    context_parts.append(json.dumps(
                        {k: v for k, v in symbol_doc.items()
                         if k in ("symbol", "display_name", "exchange",
                                  "watchlist", "positions")},
                        indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load symbol doc: %s", exc)

    # Only include activities if requested
    if cosmos and preferences.get('activities', True):
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
            # Limit to last 3 activities as per requirements
            activities = activities[:3]

            if activities:
                context_parts.append("\n--- Recent Activities (Last 3) ---")
                for d in activities:
                    context_parts.append(json.dumps(
                        _clean_doc(d), indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load activities: %s", exc)
            context_parts.append("(Error loading activities from CosmosDB)")

    # Only include TradingView data if requested
    if preferences.get('tradingview', True):
        try:
            from src.tv_data_fetcher import create_fetcher
            from src.tv_cache import get_tv_cache
            from src.config import Config

            config = Config()
            full_symbol = f"{exchange}-{symbol}"
            async with create_fetcher(config) as fetcher:
                tv_data = await fetcher.fetch_all(full_symbol,
                                                  force_refresh=force_refresh,
                                                  cache=get_tv_cache())

            cached_resources = tv_data.get("cached_resources", [])

            tv_sections = []
            for section_key, section_label in [
                ("overview", "Overview"),
                ("technicals", "Technicals"),
                ("forecast", "Forecast"),
                ("dividends", "Dividends"),
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
        "cached_resources": cached_resources,
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
        f"(overview, technicals, forecast, dividends, options chain)\n"
        f"3. Current positions and watchlist status\n\n"
        f"Answer questions about this symbol's options opportunities, "
        f"risks, positions, and market conditions.\n"
        f"Stay focused on {symbol} — redirect if the user asks about "
        f"other symbols.\n\n"
        f"Context data:\n{context_text}"
    )


@app.post("/api/symbols/{symbol}/chat/context")
async def symbol_chat_context(request: Request, symbol: str):
    """Pre-fetch all heavy context (CosmosDB + TradingView) for a symbol.
    
    Pass ``"refresh": true`` in the JSON body to bypass the TradingView cache.
    """
    symbol = symbol.upper()
    cosmos = getattr(request.app.state, "cosmos", None)
    
    # Get preferences from request body
    try:
        body = await request.json()
        preferences = body.get('preferences', {})
        force_refresh = body.get('refresh', False)
    except Exception:
        preferences = {}
        force_refresh = False

    try:
        result = await _build_symbol_context(symbol, cosmos, preferences,
                                             force_refresh=force_refresh)
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
