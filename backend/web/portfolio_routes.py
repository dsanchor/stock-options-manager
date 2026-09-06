"""FastAPI router for portfolio, securities catalog, and import session endpoints.

All endpoints follow the frozen contract v1.1 (Phase 1) and Phase 2 extensions.
Error shapes: { "error": "<code>", "detail": "<human message>" }
Storage-unavailable: 503 with { "error": "storage_unavailable", "detail": "..." }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.portfolio.cosmos_portfolio import (
    CosmosPortfolioService,
    StorageUnavailableError,
    InsufficientSharesError,
)
from src.portfolio.cosmos_securities import CosmosSecuritiesService, _CollisionError
from src.portfolio.holdings_service import HoldingsService
from src.portfolio.import_service import (
    ImportService,
    StateError,
    UnresolvedQuestionsError,
    AlreadyCommittedError,
)
from src.portfolio.provider_symbols import validate_provider_symbols
from src.portfolio.fx_service import (
    FxUnavailableError,
    FxRateNotFoundError,
    get_fx_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def _get_cosmos(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error = getattr(request.app.state, "cosmos_error", "unknown")
        raise RuntimeError(f"CosmosDB not available: {error}")
    return cosmos


def _get_securities_svc(request: Request) -> CosmosSecuritiesService:
    cosmos = _get_cosmos(request)
    return CosmosSecuritiesService(cosmos.container)


def _get_portfolio_svc(request: Request) -> CosmosPortfolioService:
    cosmos = _get_cosmos(request)
    portfolio_container = getattr(cosmos, "portfolio_container", None)
    import_sessions_container = getattr(cosmos, "import_sessions_container", None)
    return CosmosPortfolioService(portfolio_container, import_sessions_container)


def _get_import_service(request: Request) -> ImportService:
    return ImportService(_get_portfolio_svc(request), _get_securities_svc(request))


def _get_holdings_service(request: Request) -> HoldingsService:
    return HoldingsService(_get_portfolio_svc(request), _get_securities_svc(request))


def _storage_503(detail: str) -> JSONResponse:
    return JSONResponse(
        {"error": "storage_unavailable", "detail": detail},
        status_code=503,
    )


def _err(code: str, detail: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


# ===========================================================================
# Securities Catalog
# ===========================================================================

@router.get("/api/securities")
async def list_securities(request: Request):
    """GET /api/securities — list all security_master docs."""
    try:
        svc = _get_securities_svc(request)
        docs = svc.list_securities()
        return JSONResponse({
            "securities": [
                {
                    "security_id": d.get("security_id"),
                    "ticker": d.get("ticker"),
                    "company_name": d.get("company_name"),
                    "exchange_mic": d.get("exchange_mic"),
                    "asset_class": d.get("asset_class", "Equity"),
                    "listing_currency": d.get("listing_currency"),
                    "isin": d.get("isin"),
                    "status": d.get("status", "ACTIVE"),
                    **({"provider_symbols": d["provider_symbols"]} if d.get("provider_symbols") else {}),
                }
                for d in docs
            ]
        })
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("list_securities failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/securities")
async def create_security(request: Request):
    """POST /api/securities — create a new security_master."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["ticker", "company_name", "exchange_mic"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    # Validate and normalise provider_symbols if provided
    raw_ps = body.get("provider_symbols")
    if raw_ps is not None:
        try:
            cleaned_ps = validate_provider_symbols(raw_ps)
        except ValueError as exc:
            return _err("validation_error", str(exc), 400)
        body["provider_symbols"] = cleaned_ps if cleaned_ps else None

    try:
        svc = _get_securities_svc(request)
        doc = svc.create_security(body)
        return JSONResponse(_clean(doc), status_code=201)
    except _CollisionError as exc:
        return JSONResponse(
            {"error": "collision", "existing": exc.existing},
            status_code=409,
        )
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_security failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/securities/{security_id:path}")
async def get_security(request: Request, security_id: str):
    """GET /api/securities/{security_id} — lookup by MIC:TICKER."""
    # URL decode the security_id (colon may be %-encoded)
    from urllib.parse import unquote
    security_id = unquote(security_id)

    try:
        svc = _get_securities_svc(request)
        doc = svc.get_security(security_id)
        if doc is None:
            return _err("not_found", f"Security {security_id} not found", 404)
        return JSONResponse(_clean(doc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_security failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Import Sessions
# ===========================================================================

@router.post("/api/import/sessions")
async def create_import_session(
    request: Request,
    file: UploadFile = File(...),
    format_hint: Optional[str] = Form(default=None),
    currency: str = Form(default="EUR"),
    account_id: str = Form(default="_unassigned"),
):
    """POST /api/import/sessions — upload CSV and create import session."""
    try:
        content = await file.read()
    except Exception as exc:
        return _err("parse_error", f"Failed to read file: {exc}", 400)

    if not content:
        return _err("parse_error", "Empty file", 400)

    # Validate format_hint
    valid_formats = {"dividends", "purchases", "sales", None}
    if format_hint not in valid_formats:
        return _err(
            "validation_error",
            f"format_hint must be one of: dividends, purchases, sales",
            400,
        )

    try:
        svc = _get_import_service(request)
        session = svc.create_session(
            content,
            format_hint=format_hint,
            currency=currency or "EUR",
            account_id=account_id or "_unassigned",
        )
        # Add staged_summary to response per contract
        session["staged_summary"] = {
            "total_rows": session.get("row_count", 0),
            "currencies": [session.get("currency", "EUR")],
            "date_range": [],
        }
        return JSONResponse(session, status_code=201)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        return _err("parse_error", str(exc), 400)
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_import_session failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/import/sessions/{session_id}")
async def get_import_session(request: Request, session_id: str):
    """GET /api/import/sessions/{session_id} — get session state and questions."""
    try:
        svc = _get_import_service(request)
        session = svc.get_session(session_id)
        if session is None:
            return _err("not_found", f"Session {session_id} not found or expired", 404)
        return JSONResponse(session)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_import_session failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/answers")
async def submit_answer(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/answers — submit one answer."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["question_id", "answer_type"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    valid_answer_types = {
        "SELECTED_CANDIDATE", "CREATED_NEW_SECURITY", "SKIPPED_COMPANY",
        "EXCLUDED_COMPANY", "BATCH_VALUE",
    }
    if body.get("answer_type") not in valid_answer_types:
        return _err(
            "invalid_answer",
            f"answer_type must be one of: {', '.join(sorted(valid_answer_types))}",
            400,
        )

    try:
        svc = _get_import_service(request)
        session = svc.answer_question(session_id, body)
        return JSONResponse(session)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except ValueError as exc:
        return _err("invalid_answer", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("submit_answer failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/securities")
async def inline_create_security(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/securities — inline security creation."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    question_id = body.pop("question_id", None)
    if not question_id:
        return _err("validation_error", "question_id is required", 400)

    required = ["ticker", "company_name", "exchange_mic"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    # Validate and normalise provider_symbols if provided
    raw_ps = body.get("provider_symbols")
    if raw_ps is not None:
        try:
            cleaned_ps = validate_provider_symbols(raw_ps)
        except ValueError as exc:
            return _err("validation_error", str(exc), 400)
        body["provider_symbols"] = cleaned_ps if cleaned_ps else None

    try:
        svc = _get_import_service(request)
        session = svc.inline_create_security(session_id, question_id, body)
        return JSONResponse(session)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except _CollisionError as exc:
        return JSONResponse(
            {"error": "collision", "existing": exc.existing},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("inline_create_security failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/preview")
async def generate_preview(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/preview — generate commit preview."""
    try:
        svc = _get_import_service(request)
        result = svc.generate_preview(session_id)
        return JSONResponse(result)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except UnresolvedQuestionsError as exc:
        return JSONResponse(
            {"error": "unresolved_questions", "pending": exc.pending},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("generate_preview failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/commit")
async def commit_session(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/commit — commit movements to ledger."""
    try:
        svc = _get_import_service(request)
        result = svc.commit_session(session_id)
        return JSONResponse(result)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except AlreadyCommittedError:
        return _err("already_committed", "Session already committed", 409)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except UnresolvedQuestionsError as exc:
        return JSONResponse(
            {"error": "unresolved_questions", "pending": exc.pending},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("commit_session failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Portfolio — Holdings & Movements
# ===========================================================================

@router.get("/api/portfolio/holdings")
async def get_holdings(
    request: Request,
    account_id: Optional[str] = Query(default=None),
):
    """GET /api/portfolio/holdings — derived holdings from ledger."""
    try:
        svc = _get_holdings_service(request)
        result = svc.compute_holdings(account_id=account_id)
        return JSONResponse(result)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_holdings failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/portfolio/movements")
async def get_movements(
    request: Request,
    account_id: Optional[str] = Query(default=None),
    security_id: Optional[str] = Query(default=None),
    txn_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """GET /api/portfolio/movements — paginated ledger entries."""
    _ALLOWED_TXN_TYPES = {"BUY", "SELL", "DIVIDEND", "TRANSFER_OUT", "TRANSFER_IN"}
    if txn_type and txn_type not in _ALLOWED_TXN_TYPES:
        return _err(
            "validation_error",
            "txn_type must be BUY, SELL, DIVIDEND, TRANSFER_OUT, or TRANSFER_IN",
            400,
        )

    try:
        svc = _get_portfolio_svc(request)
        movements, total = svc.get_movements(
            account_id=account_id,
            security_id=security_id,
            txn_type=txn_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({
            "movements": [_clean(m) for m in movements],
            "total_count": total,
            "limit": limit,
            "offset": offset,
        })
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_movements failed")
        return _err("internal_error", str(exc), 500)


@router.delete("/api/portfolio/movements/{movement_id}")
async def delete_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default=None),
):
    """DELETE /api/portfolio/movements/{movement_id} — soft-delete a movement."""
    if not account_id:
        account_id = "_unassigned"

    try:
        svc = _get_portfolio_svc(request)
        updated = svc.soft_delete_movement(movement_id, account_id)
        if updated is None:
            return _err("not_found", f"Movement {movement_id} not found", 404)
        return JSONResponse({
            "id": updated.get("id"),
            "deleted_at": updated.get("deleted_at"),
        })
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("delete_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Accounts
# ===========================================================================

@router.get("/api/portfolio/accounts")
async def list_accounts(request: Request):
    """GET /api/portfolio/accounts — list all broker accounts."""
    try:
        svc = _get_portfolio_svc(request)
        accounts = svc.list_accounts()
        return JSONResponse({"accounts": [_clean(a) for a in accounts]})
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("list_accounts failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/accounts")
async def create_account(request: Request):
    """POST /api/portfolio/accounts — create a broker account."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    valid_brokers = {"fidelity", "heytrade", "ing", "interactive_brokers", "other"}
    broker = body.get("broker", "")
    if broker not in valid_brokers:
        return _err(
            "validation_error",
            f"broker must be one of: {', '.join(sorted(valid_brokers))}",
            400,
        )
    name = str(body.get("name", "")).strip()
    if not name:
        return _err("validation_error", "name is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        doc = svc.create_account(
            broker=broker,
            name=name,
            currency=str(body.get("currency", "EUR")).upper(),
            description=body.get("description"),
        )
        return JSONResponse(_clean(doc), status_code=201)
    except ValueError as exc:
        msg = str(exc)
        if "already exists" in msg:
            return _err("conflict", msg, 409)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_account failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/portfolio/accounts/{account_id}")
async def get_account(request: Request, account_id: str):
    """GET /api/portfolio/accounts/{account_id} — get a single account."""
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.get_account(account_id)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse(_clean(doc))
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_account failed")
        return _err("internal_error", str(exc), 500)


@router.put("/api/portfolio/accounts/{account_id}")
async def update_account(request: Request, account_id: str):
    """PUT /api/portfolio/accounts/{account_id} — update mutable account fields.

    account_id is immutable; supplying a different id in the body is ignored.
    Accepted fields: broker, name, currency, description.
    Returns 404 for a missing or soft-deleted account.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    valid_brokers = {"fidelity", "heytrade", "ing", "interactive_brokers", "other"}
    broker = body.get("broker")
    if broker is not None and broker not in valid_brokers:
        return _err(
            "validation_error",
            f"broker must be one of: {', '.join(sorted(valid_brokers))}",
            400,
        )

    updates: Dict[str, Any] = {}
    if broker is not None:
        updates["broker"] = broker
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            return _err("validation_error", "name cannot be blank", 400)
        updates["name"] = name
    if "currency" in body:
        updates["currency"] = str(body["currency"]).upper()
    if "description" in body:
        updates["description"] = body["description"]

    if not updates:
        return _err("validation_error", "No updatable fields provided", 400)

    try:
        svc = _get_portfolio_svc(request)
        doc = svc.update_account(account_id, updates)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse(_clean(doc))
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("update_account failed")
        return _err("internal_error", str(exc), 500)


@router.delete("/api/portfolio/accounts/{account_id}")
async def delete_account(request: Request, account_id: str):
    """DELETE /api/portfolio/accounts/{account_id} — soft-delete an account.

    Blocked with 409 if the account has active movements.
    """
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.delete_account(account_id)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse({"id": doc.get("id"), "deleted_at": doc.get("deleted_at")})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("account_has_movements:"):
            count = int(msg.split(":", 1)[1])
            return JSONResponse(
                {
                    "error": "account_has_movements",
                    "detail": f"Cannot delete account with {count} active movement(s)",
                    "movement_count": count,
                },
                status_code=409,
            )
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("delete_account failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Manual Movement Creation
# ===========================================================================

@router.post("/api/portfolio/movements")
async def create_movement(request: Request):
    """POST /api/portfolio/movements — create a manual BUY, SELL, or DIVIDEND."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    txn_type = body.get("txn_type", "")
    if txn_type not in {"BUY", "SELL", "DIVIDEND"}:
        return _err(
            "validation_error",
            "txn_type must be BUY, SELL, or DIVIDEND (use POST /api/portfolio/transfers for transfers)",
            400,
        )

    for field in ("security_id", "trade_date", "gross"):
        if not body.get(field):
            return _err("validation_error", f"{field} is required", 400)

    if not isinstance(body.get("gross"), dict) or not body["gross"].get("eur_amount"):
        return _err("validation_error", "gross must include eur_amount", 400)

    try:
        svc = _get_portfolio_svc(request)

        # Duplicate check before writing
        duplicate = svc.find_probable_duplicate(
            security_id=body["security_id"],
            txn_type=txn_type,
            trade_date=body["trade_date"],
            quantity=str(body.get("quantity", "0")),
            gross_eur=str(body["gross"].get("eur_amount", "0")),
        )
        if duplicate:
            return JSONResponse(
                {"error": "probable_duplicate", "existing": _clean(duplicate)},
                status_code=409,
            )

        doc = svc.create_manual_movement(body)
        return JSONResponse(_clean(doc), status_code=201)
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Movement Detail & Correction
# ===========================================================================

@router.get("/api/portfolio/movements/{movement_id}")
async def get_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default="_unassigned"),
):
    """GET /api/portfolio/movements/{movement_id} — movement detail with correction chain."""
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.get_movement(movement_id, account_id or "_unassigned")
        if doc is None:
            return _err("not_found", f"Movement {movement_id!r} not found", 404)

        # If superseded, include the replacement doc
        superseded_by = doc.get("superseded_by")
        replacement = None
        if superseded_by:
            replacement = svc.get_movement(superseded_by, account_id or "_unassigned")
            if replacement is None:
                # replacement may be in the same partition but not found — best-effort
                pass

        return JSONResponse({
            "movement": _clean(doc),
            "superseded_by": _clean(replacement) if replacement else None,
        })
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_movement failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/{movement_id}/correct")
async def correct_movement(request: Request, movement_id: str):
    """POST /api/portfolio/movements/{movement_id}/correct — replace a movement."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    account_id = body.get("account_id", "")
    if not account_id:
        return _err("validation_error", "account_id is required", 400)
    if not body.get("correction_note", "").strip():
        return _err("validation_error", "correction_note is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.correct_movement(movement_id, account_id, body)
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except ValueError as exc:
        msg = str(exc)
        if "already_superseded" in msg:
            return _err("already_superseded", msg, 409)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("correct_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Transfers
# ===========================================================================

@router.post("/api/portfolio/transfers")
async def create_transfer(request: Request):
    """POST /api/portfolio/transfers — create paired TRANSFER_OUT + TRANSFER_IN."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["security_id", "trade_date", "quantity", "source_account_id", "dest_account_id"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err("validation_error", f"Missing required fields: {', '.join(missing)}", 400)

    if body["source_account_id"] == body["dest_account_id"]:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.create_transfer_pair(
            security_id=body["security_id"],
            trade_date=body["trade_date"],
            quantity=str(body["quantity"]),
            source_account_id=body["source_account_id"],
            dest_account_id=body["dest_account_id"],
            cost_basis_override_eur=body.get("cost_basis_override_eur"),
            transfer_fee=body.get("transfer_fee"),
            notes=body.get("notes"),
        )
        return JSONResponse(result, status_code=201)
    except InsufficientSharesError as exc:
        return JSONResponse(
            {
                "error": "insufficient_shares",
                "detail": f"Source account has {exc.available} shares, requested {exc.requested}",
                "available": exc.available,
                "requested": exc.requested,
            },
            status_code=409,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_transfer failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Movement Reassignment
# ===========================================================================

@router.post("/api/portfolio/movements/{movement_id}/reassign")
async def reassign_movement(request: Request, movement_id: str):
    """POST /api/portfolio/movements/{movement_id}/reassign — move to different account."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        return _err("validation_error", "reason is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.reassign_movement(
            movement_id=movement_id,
            source_account_id=source,
            dest_account_id=dest,
            reason=reason,
        )
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except ValueError as exc:
        msg = str(exc)
        if "same_account" in msg:
            return _err("same_account", msg, 409)
        if "already_reassigned" in msg:
            return _err("already_reassigned", msg, 409)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("reassign_movement failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/batch-reassign/preview")
async def preview_batch_reassign(request: Request):
    """POST /api/portfolio/movements/batch-reassign/preview — dry-run count.

    Uses the exact same selection predicate as batch-reassign execution.
    Read-only: no writes are performed.

    The client must NOT pass the returned count back to execution; the
    server always re-derives the candidate set at execution time.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    if source == dest:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.preview_batch_reassign(
            source_account_id=source,
            dest_account_id=dest,
            security_id=body.get("security_id"),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
        )
        return JSONResponse(result)
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("preview_batch_reassign failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/batch-reassign")
async def batch_reassign_movements(request: Request):
    """POST /api/portfolio/movements/batch-reassign — bulk reassignment."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    if source == dest:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        return _err("validation_error", "reason is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.batch_reassign_movements(
            source_account_id=source,
            dest_account_id=dest,
            security_id=body.get("security_id"),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
            reason=reason,
        )
        return JSONResponse(result)
    except ValueError as exc:
        msg = str(exc)
        if "batch_reassign_failed" in msg:
            return _err("batch_reassign_failed", msg, 500)
        if "same_account" in msg:
            return _err("validation_error", msg, 400)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("batch_reassign_movements failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — FX Rates
# ===========================================================================

@router.get("/api/fx/rates")
async def get_fx_rate_endpoint(
    request: Request,
    from_currency: str = Query(..., description="Source currency (e.g. USD)"),
    to_currency: str = Query(default="EUR", description="Target currency (must be EUR)"),
    date: Optional[str] = Query(default=None, description="Date YYYY-MM-DD; defaults to today"),
):
    """GET /api/fx/rates — look up EUR reference rate from ECB."""
    from_currency = from_currency.strip().upper()
    to_currency = to_currency.strip().upper()

    if to_currency != "EUR":
        return _err("validation_error", "Only EUR is supported as to_currency", 400)

    try:
        rate = get_fx_rate(from_currency, to_currency, rate_date=date)
        from datetime import date as _date
        effective_date = date or _date.today().isoformat()
        return JSONResponse({
            "from_currency": from_currency,
            "to_currency": to_currency,
            "date": effective_date,
            "rate": rate,
            "rate_source": "ECB",
            "note": None,
        })
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except FxRateNotFoundError as exc:
        return _err("rate_not_found", str(exc), 404)
    except FxUnavailableError as exc:
        return JSONResponse(
            {"error": "fx_unavailable", "detail": str(exc)},
            status_code=503,
        )
    except Exception as exc:
        logger.exception("get_fx_rate_endpoint failed")
        return _err("internal_error", str(exc), 500)
