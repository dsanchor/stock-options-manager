"""FastAPI router for portfolio, securities catalog, and import session endpoints.

All endpoints follow the frozen contract v1.1.
Error shapes: { "error": "<code>", "detail": "<human message>" }
Storage-unavailable: 503 with { "error": "storage_unavailable", "detail": "..." }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.portfolio.cosmos_portfolio import CosmosPortfolioService, StorageUnavailableError
from src.portfolio.cosmos_securities import CosmosSecuritiesService, _CollisionError
from src.portfolio.holdings_service import HoldingsService
from src.portfolio.import_service import (
    ImportService,
    StateError,
    UnresolvedQuestionsError,
    AlreadyCommittedError,
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
    if txn_type and txn_type not in {"BUY", "SELL", "DIVIDEND"}:
        return _err("validation_error", "txn_type must be BUY, SELL, or DIVIDEND", 400)

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
