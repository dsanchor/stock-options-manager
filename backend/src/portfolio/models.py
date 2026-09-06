"""Pydantic models for the portfolio domain.

Frozen enums and shapes from contract v1.1.
All financial amounts are represented as Decimal to guarantee arithmetic
precision; JSON serialisation uses string representation.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Frozen enums (contract §Frozen Enums)
# ---------------------------------------------------------------------------

class TxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"


class ImportFormat(str, Enum):
    dividends = "dividends"
    purchases = "purchases"
    sales = "sales"


class CostBasisStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class WarningType(str, Enum):
    NEGATIVE_INVENTORY = "NEGATIVE_INVENTORY"
    ZERO_COST_ACQUISITION = "ZERO_COST_ACQUISITION"
    RIGHTS_AMOUNT = "RIGHTS_AMOUNT"
    PROBABLE_DUPLICATE = "PROBABLE_DUPLICATE"


class SessionState(str, Enum):
    CREATED = "CREATED"
    FILE_PARSED = "FILE_PARSED"
    BATCH_QUESTIONS = "BATCH_QUESTIONS"
    ENTITY_QUESTIONS = "ENTITY_QUESTIONS"
    ROW_GROUP_QUESTIONS = "ROW_GROUP_QUESTIONS"
    PREVIEW_READY = "PREVIEW_READY"
    COMMIT_CONFIRMED = "COMMIT_CONFIRMED"
    COMMITTED = "COMMITTED"
    EXPIRED = "EXPIRED"


class QuestionScope(str, Enum):
    BATCH = "BATCH"
    ENTITY = "ENTITY"
    ROW_GROUP = "ROW_GROUP"


class AnswerType(str, Enum):
    SELECTED_CANDIDATE = "SELECTED_CANDIDATE"
    CREATED_NEW_SECURITY = "CREATED_NEW_SECURITY"
    SKIPPED_COMPANY = "SKIPPED_COMPANY"
    EXCLUDED_COMPANY = "EXCLUDED_COMPANY"
    BATCH_VALUE = "BATCH_VALUE"


class AssetClass(str, Enum):
    Equity = "Equity"


class SecurityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"


class FxRateSource(str, Enum):
    ECB = "ECB"
    BROKER = "BROKER"
    MANUAL = "MANUAL"


class ImportSource(str, Enum):
    csv_import = "csv_import"
    manual = "manual"


# ---------------------------------------------------------------------------
# Security Master
# ---------------------------------------------------------------------------

class SecurityAlias(BaseModel):
    source: str
    value: str
    normalized: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.normalized:
            object.__setattr__(self, "normalized", self.value.lower().strip())


class SecurityMasterCreate(BaseModel):
    ticker: str
    company_name: str
    exchange_mic: str
    asset_class: AssetClass = AssetClass.Equity
    listing_currency: str = "USD"
    isin: Optional[str] = None
    cusip: Optional[str] = None
    sedol: Optional[str] = None
    broker_ids: Optional[Dict[str, str]] = None
    aliases: Optional[List[SecurityAlias]] = None
    provider_symbols: Optional[Dict[str, str]] = None

    @field_validator("ticker")
    @classmethod
    def ticker_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("exchange_mic")
    @classmethod
    def mic_upper(cls, v: str) -> str:
        return v.strip().upper()


class SecurityMasterDoc(BaseModel):
    """Returned shape for a security_master document (contract §GET /api/securities)."""
    security_id: str
    ticker: str
    company_name: str
    exchange_mic: str
    asset_class: str = "Equity"
    listing_currency: str
    isin: Optional[str] = None
    status: str = "ACTIVE"
    provider_symbols: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class MoneyAmount(BaseModel):
    amount: str
    currency: str
    eur_amount: str


class WithholdingDetail(BaseModel):
    country: Optional[str] = None
    rate_pct: Optional[str] = None
    amount_eur: str


class WithholdingInfo(BaseModel):
    source: Optional[WithholdingDetail] = None
    destination: Optional[WithholdingDetail] = None


class FxInfo(BaseModel):
    rate: str
    rate_source: str = "ECB"


class ImportWarning(BaseModel):
    type: str
    message: str
    security_id: Optional[str] = None
    security: Optional[str] = None
    shares: Optional[str] = None
    row_index: Optional[int] = None
    company: Optional[str] = None
    amount: Optional[str] = None
    count: Optional[int] = None
    row_indices: Optional[List[int]] = None
    existing_movement_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Import session
# ---------------------------------------------------------------------------

class SecurityCandidate(BaseModel):
    security_id: str
    company_name: str
    score: float = 0.0


class ImportQuestion(BaseModel):
    question_id: str
    scope: str
    company_name: Optional[str] = None
    normalized_name: Optional[str] = None
    candidates: Optional[List[SecurityCandidate]] = None
    answer: Optional[str] = None
    answer_type: Optional[str] = None
    selected_security_id: Optional[str] = None
    batch_key: Optional[str] = None
    batch_value: Optional[str] = None


class AnswerRequest(BaseModel):
    question_id: str
    answer_type: AnswerType
    selected_security_id: Optional[str] = None
    batch_value: Optional[str] = None


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

class HoldingItem(BaseModel):
    security_id: str
    ticker: str
    company_name: str
    total_shares: str
    avg_cost_basis_eur: Optional[str]
    cost_basis_status: str
    total_invested_eur: str
    total_purchases_eur: str
    total_sales_eur: str
    total_dividends_eur: str
    accounts: List[str]
    warnings: List[ImportWarning]


class HoldingsSummary(BaseModel):
    total_securities: int
    total_invested_eur: str
    total_purchases_eur: str
    total_sales_eur: str
    current_invested_eur: str
    total_dividends_eur: str


class HoldingsResponse(BaseModel):
    holdings: List[HoldingItem]
    summary: HoldingsSummary
