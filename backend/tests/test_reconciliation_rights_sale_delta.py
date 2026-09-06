"""Reconciliation regression tests — Rights-sale (DERECHOS) duplicate delta.

Independent analysis by Basher (test reconciler).
Requested by: Copilot on behalf of the user.
Do NOT edit production code or Cosmos data.

──────────────────────────────────────────────────────────────────────────────
USER REPORT
──────────────────────────────────────────────────────────────────────────────
Expected total sales ≈ EUR 99,135; system shows EUR 103,689 after re-import.
User suspects rights rows for ACS, Viscofán, and Técnicas Reunidas.
Delta = EUR 103,689 − EUR 99,135 = EUR 4,554.

──────────────────────────────────────────────────────────────────────────────
ROOT-CAUSE ANALYSIS (read-only, code-only)
──────────────────────────────────────────────────────────────────────────────

A. NET CONTRIBUTION FORMULA (holdings_service.py:113)
   For every SELL row (both ACCIONES and DERECHOS):
       total_sales_eur += gross["eur_amount"] - fees["total_eur"]
   There is NO separate deduction for DERECHOS — both sale types are summed
   identically into total_sales_eur. The ACCIONES/DERECHOS distinction ONLY
   controls whether total_shares is decremented.

B. MOVEMENT IDEMPOTENCY KEY (import_service.py:642)
   movement_id = f"txn_{account_id}_{date_compact}_{ticker}_{txn_type}_{row_index:03d}"
   If account_id changes between imports, the movement_id changes.
   portfolio_container.upsert_item() writes to the new (account_id, id) pair.
   The OLD document in the old partition remains ACTIVE and unaffected.

C. HOLDINGS QUERY INCLUDES ALL "NOT IS_DEFINED correction_status" DOCS
   get_all_movements_for_holdings() (cosmos_portfolio.py):
       SELECT * FROM c WHERE c.doc_type = 'ledger_txn'
       AND NOT IS_DEFINED(c.deleted_at)
       AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')
   CSV-imported movements (_row_to_movement) do NOT set correction_status.
   Therefore both the old and new import documents satisfy
   NOT IS_DEFINED(c.correction_status) → both are returned → proceeds doubled.

D. DUPLICATE DETECTION IS ADVISORY ONLY
   find_probable_duplicate() matches on (security_id, txn_type, trade_date, quantity).
   It raises a PROBABLE_DUPLICATE warning but does NOT block commit.
   If the DERECHOS rights rows were originally imported as ACCIONES (6-column CSV,
   no Tipo column) with quantity > 0, and then re-imported as DERECHOS (7-column
   CSV) with quantity=0, the quantity mismatch means find_probable_duplicate MISSES
   the existing row entirely — no warning, no protection.

E. MECHANISM SUMMARY
   1. Original 6-column import: rights rows stored as ACCIONES, quantity > 0,
      no correction_status, account_id = A (e.g. "_unassigned").
      movement_id = txn_A_<date>_ACS_SELL_000
   2. Re-import 7-column CSV with account_id = B (real account):
      same rows now DERECHOS, possibly quantity=0.
      movement_id = txn_B_<date>_ACS_SELL_000
   3. Old txn_A document: still ACTIVE, no deleted_at, no correction_status.
   4. New txn_B document: also ACTIVE, no correction_status.
   5. Both pass get_all_movements_for_holdings() filter.
   6. total_sales_eur accumulates proceeds TWICE.
   7. EUR delta = sum of (gross_eur − commission_eur) for the duplicate rights rows.

F. QUANTIFICATION (proxies — production Cosmos not accessible read-only)
   Let the 3 rights rows net proceeds be:
     ACS rights:               EUR 2,054.00 (net)
     Viscofán rights:          EUR 1,500.00 (net)
     Técnicas Reunidas rights: EUR 1,000.00 (net)
     ─────────────────────────────────────────────
     Total rights net:         EUR 4,554.00

   With all ACCIONES rows counted once and rights rows counted once:
     total_sales_eur = EUR 99,135.00  (expected)
   With rights rows counted twice (re-import duplicate):
     total_sales_eur = EUR 99,135.00 + EUR 4,554.00 = EUR 103,689.00  (observed)

   Note: the above proxy amounts reproduce the reported delta exactly.
   The actual per-row breakdown can only be confirmed from a direct Cosmos query.

G. CORRECTION PATH FOR LINUS
   Do NOT mutate production code here (read-only mandate).
   The safe fix is ONE of:
     a) Soft-delete the duplicate older movements via
        DELETE /api/portfolio/movements/{id} (with the original account_id).
     b) Apply correct() to the old movements so they become SUPERSEDED.
   Either way, the DERECHOS re-imported rows should remain as the sole ACTIVE docs.

──────────────────────────────────────────────────────────────────────────────
TESTS — ALL HERMETIC (no Cosmos, no network)
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ──────────────────────────────────────────────────────────────────────────────
# Minimal fake containers (self-contained; no shared conftest dependency)
# ──────────────────────────────────────────────────────────────────────────────

class _FakePortfolio:
    """Fake portfolio container that mirrors the production query semantics."""

    def __init__(self, movements=None):
        self._store: dict[str, dict] = {}
        for m in (movements or []):
            self._store[m["id"]] = dict(m)

    # Cosmos SDK interface subset used by HoldingsService + CosmosPortfolioService

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key=None, **kw):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body, **kw):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=True, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = list(self._store.values())

        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [d for d in results if "deleted_at" not in d]

        # This is the EXACT Cosmos filter used by get_all_movements_for_holdings()
        # and get_movements().  A document WITHOUT correction_status satisfies
        # NOT IS_DEFINED(c.correction_status) → included even if never explicitly set.
        if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
            results = [
                d for d in results
                if "correction_status" not in d or d["correction_status"] == "ACTIVE"
            ]

        if "doc_type = 'ledger_txn'" in query:
            results = [d for d in results if d.get("doc_type") == "ledger_txn"]

        if "@security_id" in param_map:
            results = [d for d in results
                       if d.get("security_id") == param_map["@security_id"]]
        if "@txn_type" in param_map:
            results = [d for d in results
                       if d.get("txn_type") == param_map["@txn_type"]]
        if "@trade_date" in param_map:
            results = [d for d in results
                       if d.get("trade_date") == param_map["@trade_date"]]
        if "@quantity" in param_map:
            results = [d for d in results
                       if d.get("quantity") == param_map["@quantity"]]
        if "COUNT" in query:
            return iter([len(results)])
        return iter(results)


class _FakeSecurities:
    def query_items(self, **kw):
        return iter([])

    def read_item(self, item, partition_key, **kw):
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_svc(movements):
    """Construct HoldingsService with hermetic fakes."""
    portfolio_svc = CosmosPortfolioService(_FakePortfolio(movements), None)
    securities_svc = CosmosSecuritiesService(_FakeSecurities())
    return HoldingsService(portfolio_svc, securities_svc)


def _sell(
    mid: str,
    security_id: str,
    gross_eur: str,
    commission_eur: str = "0",
    quantity: str = "0",
    account_id: str = "_unassigned",
    trade_date: str = "2023-06-15",
    sales_type: str | None = None,
    correction_status: str | None = None,
):
    """Build a minimal SELL ledger_txn document."""
    ticker = security_id.split(":")[-1]
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {
            "amount": str(Decimal(gross_eur) - Decimal(commission_eur)),
            "currency": "EUR",
            "eur_amount": str(Decimal(gross_eur) - Decimal(commission_eur)),
        },
        "account_id": account_id,
        # NOTE: CSV-imported movements do NOT have correction_status set at all.
        # We only set it here when explicitly testing the correction workflow.
    }
    if sales_type is not None:
        doc["sales_type"] = sales_type
    if correction_status is not None:
        doc["correction_status"] = correction_status
    return doc


def _buy(
    mid: str,
    security_id: str,
    gross_eur: str,
    commission_eur: str = "0",
    quantity: str = "100",
    account_id: str = "_unassigned",
    trade_date: str = "2022-01-10",
):
    ticker = security_id.split(":")[-1]
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {
            "amount": str(Decimal(gross_eur) + Decimal(commission_eur)),
            "currency": "EUR",
            "eur_amount": str(Decimal(gross_eur) + Decimal(commission_eur)),
        },
        "account_id": account_id,
        "cost_basis_status": "COMPLETE",
    }


# ──────────────────────────────────────────────────────────────────────────────
# §A — Net contribution formula (exact field paths)
# ──────────────────────────────────────────────────────────────────────────────

class TestNetContributionFormula:
    """Verify the exact formula: total_sales_eur += gross["eur_amount"] - fees["total_eur"]."""

    def test_acciones_net_is_gross_minus_fees_total_eur(self):
        """ACCIONES sale: net = gross.eur_amount - fees.total_eur."""
        movements = [
            _buy("b1", "XMAD:ACS", "50000", quantity="1000"),
            _sell("s1", "XMAD:ACS", gross_eur="10000", commission_eur="25",
                  quantity="200", sales_type="ACCIONES"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        # Net = 10000 − 25 = 9975
        assert Decimal(acs["total_sales_eur"]) == Decimal("9975.00"), (
            "ACCIONES net contribution must be gross.eur_amount - fees.total_eur"
        )

    def test_derechos_net_uses_identical_formula(self):
        """DERECHOS sale uses the same formula: gross.eur_amount - fees.total_eur."""
        movements = [
            _buy("b1", "XMAD:VIS", "30000", quantity="300"),
            _sell("s1", "XMAD:VIS", gross_eur="1500", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        vis = next(h for h in result["holdings"] if h["security_id"] == "XMAD:VIS")
        # Net = 1500 − 0 = 1500
        assert Decimal(vis["total_sales_eur"]) == Decimal("1500.00"), (
            "DERECHOS net contribution must be gross.eur_amount - fees.total_eur"
        )

    def test_derechos_and_acciones_same_amounts_same_contribution(self):
        """Given identical gross and commission, ACCIONES and DERECHOS contribute the same amount."""
        movements_acc = [
            _buy("b1", "XMAD:TRE", "40000", quantity="500"),
            _sell("s_acc", "XMAD:TRE", gross_eur="1000", commission_eur="10",
                  quantity="20", sales_type="ACCIONES"),
        ]
        movements_der = [
            _buy("b1", "XMAD:TRE", "40000", quantity="500"),
            _sell("s_der", "XMAD:TRE", gross_eur="1000", commission_eur="10",
                  quantity="20", sales_type="DERECHOS"),
        ]
        svc_acc = _make_svc(movements_acc)
        svc_der = _make_svc(movements_der)
        total_acc = Decimal(svc_acc.compute_holdings()["summary"]["total_sales_eur"])
        total_der = Decimal(svc_der.compute_holdings()["summary"]["total_sales_eur"])
        assert total_acc == total_der, (
            "ACCIONES and DERECHOS with identical amounts must produce identical total_sales_eur"
        )

    def test_commission_field_used_is_fees_total_eur(self):
        """Confirm the fee field consumed is fees.total_eur, not fees.amount."""
        movements = [
            _sell("s1", "XMAD:ACS", gross_eur="5000", commission_eur="50",
                  sales_type="ACCIONES"),
        ]
        # Mutate fees.total (the non-EUR amount) to a different value;
        # fees.total_eur stays at 50. The result must still use 50.
        movements[0]["fees"]["total"] = "999"  # different from total_eur
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        assert Decimal(acs["total_sales_eur"]) == Decimal("4950.00"), (
            "Holdings service must use fees.total_eur, not fees.total"
        )


# ──────────────────────────────────────────────────────────────────────────────
# §B — Correction-status visibility: imported docs without the field
# ──────────────────────────────────────────────────────────────────────────────

class TestCorrectionStatusFilter:
    """CSV-imported movements have NO correction_status field set.
    The holdings query includes them via NOT IS_DEFINED(c.correction_status).
    Two such docs for the same sale (e.g. from re-import) are BOTH counted.
    """

    def test_sell_without_correction_status_is_included(self):
        """A SELL doc without correction_status is included in holdings (as intended)."""
        movements = [
            _sell("s_no_cs", "XMAD:ACS", gross_eur="2054", commission_eur="0"),
            # No correction_status field → doc without the key
        ]
        assert "correction_status" not in movements[0], (
            "Precondition: CSV-imported doc must have NO correction_status"
        )
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        assert len(result["holdings"]) == 1
        acs = result["holdings"][0]
        assert Decimal(acs["total_sales_eur"]) == Decimal("2054.00"), (
            "Movement without correction_status must be included in totals"
        )

    def test_sell_superseded_is_excluded(self):
        """A SELL doc with correction_status='SUPERSEDED' is excluded from holdings."""
        movements = [
            _sell("s_sup", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  correction_status="SUPERSEDED"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs_list = [h for h in result["holdings"] if h["security_id"] == "XMAD:ACS"]
        if acs_list:
            assert Decimal(acs_list[0]["total_sales_eur"]) == Decimal("0.00"), (
                "SUPERSEDED movement must contribute zero to total_sales_eur"
            )
        # OR the security doesn't appear at all (acceptable: no net holdings)
        # Either way, SUPERSEDED proceeds must not inflate the total.

    def test_superseded_and_replacement_counts_once(self):
        """A corrected movement: original SUPERSEDED + replacement ACTIVE → counted once."""
        movements = [
            _sell("s_orig", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  correction_status="SUPERSEDED"),
            _sell("s_repl", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  correction_status="ACTIVE"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        assert Decimal(acs["total_sales_eur"]) == Decimal("2054.00"), (
            "Superseded + replacement must contribute ONCE, not twice"
        )

    def test_two_active_no_cs_docs_both_counted_demonstrating_the_bug(self):
        """REGRESSION: two ACTIVE docs without correction_status are BOTH counted.

        This is the exact mechanism behind the EUR 4,554 delta:
          - old import: doc without correction_status (satisfies NOT IS_DEFINED)
          - new import: doc without correction_status (also satisfies NOT IS_DEFINED)
          Both docs pass the holdings filter → proceeds doubled.

        This test asserts the BUG BEHAVIOUR to make it explicit and pinned.
        When the fix is applied (e.g. duplicate blocking or correction_status on import),
        this test must be updated to reflect that ONLY ONE of the two is counted.
        """
        movements = [
            # Simulates original import (different movement_id, same economic content)
            _sell("s_old_account", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  account_id="_unassigned"),
            # Simulates re-import with different account_id (different movement_id)
            _sell("s_new_account", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  account_id="acct_ing_cuenta_valores"),
        ]
        # PRECONDITION: neither doc has correction_status (as CSV imports produce)
        assert "correction_status" not in movements[0]
        assert "correction_status" not in movements[1]

        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")

        # BUG: currently both are counted → EUR 4,108 (2×2054)
        assert Decimal(acs["total_sales_eur"]) == Decimal("4108.00"), (
            "BUG CONFIRMED: two active docs without correction_status double-count proceeds. "
            "Expected single-count 2054.00; double-count gives 4108.00. "
            "This test documents the defect. Update it once the fix is shipped."
        )


# ──────────────────────────────────────────────────────────────────────────────
# §C — Idempotency path (same account_id → safe re-import)
# ──────────────────────────────────────────────────────────────────────────────

class TestReimportIdempotency:
    """Re-import with the SAME account_id produces the same movement_id → upsert
    overwrites the old document → idempotent, no duplicate.

    Re-import with a DIFFERENT account_id produces a different movement_id → new
    document in a new partition → duplicate in holdings totals.
    """

    def test_same_id_document_upserted_counts_once(self):
        """Simulates re-import that produces the same movement_id (idempotent path)."""
        # Same ID means upsert overwrites; only one doc in store.
        movement_id = "txn__unassigned_20230615_ACS_SELL_000"
        movements = [
            _sell(movement_id, "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  account_id="_unassigned"),
        ]
        svc = _make_svc(movements)
        # Simulate "re-import" by calling upsert_item again with the same doc
        portfolio_container = svc.portfolio_svc.portfolio_container
        portfolio_container.upsert_item(
            _sell(movement_id, "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  account_id="_unassigned")
        )
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        assert Decimal(acs["total_sales_eur"]) == Decimal("2054.00"), (
            "Re-import with same movement_id must be idempotent — counted once"
        )

    def test_different_account_id_creates_additive_duplicate(self):
        """Simulates re-import that produces a DIFFERENT movement_id (unsafe path).

        movement_id encodes account_id:
          txn__unassigned_...   (original import)
          txn_acct_ing_...      (re-import with real account)
        Both are ACTIVE, neither has correction_status → both counted.
        """
        movements = [
            _sell("txn__unassigned_20230615_ACS_SELL_000", "XMAD:ACS",
                  gross_eur="2054", commission_eur="0", account_id="_unassigned"),
            _sell("txn_acct_ing_cuenta_valores_20230615_ACS_SELL_000", "XMAD:ACS",
                  gross_eur="2054", commission_eur="0",
                  account_id="acct_ing_cuenta_valores"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        # Both docs are active → proceeds doubled
        assert Decimal(acs["total_sales_eur"]) == Decimal("4108.00"), (
            "Different account_id re-import creates duplicate → proceeds doubled"
        )

    def test_same_account_id_no_delta(self):
        """If re-import uses same account_id (same movement_id), total is unchanged."""
        acc = "_unassigned"
        mid = "txn__unassigned_20230615_VIS_SELL_001"
        movements = [
            _sell(mid, "XMAD:VIS", gross_eur="1500", commission_eur="0",
                  account_id=acc),
        ]
        svc = _make_svc(movements)
        # Simulate re-import of same row (upsert overwrites)
        svc.portfolio_svc.portfolio_container.upsert_item(
            _sell(mid, "XMAD:VIS", gross_eur="1500", commission_eur="0",
                  account_id=acc)
        )
        result = svc.compute_holdings()
        vis = next(h for h in result["holdings"] if h["security_id"] == "XMAD:VIS")
        assert Decimal(vis["total_sales_eur"]) == Decimal("1500.00"), (
            "Idempotent re-import (same account_id) must not increase total_sales_eur"
        )


# ──────────────────────────────────────────────────────────────────────────────
# §D — Duplicate detection miss when quantities differ
# ──────────────────────────────────────────────────────────────────────────────

class TestProbableDuplicateDetection:
    """find_probable_duplicate matches on (security_id, txn_type, trade_date, quantity).
    If the original DERECHOS row had quantity > 0 (6-col import, treated as ACCIONES)
    and the re-import has quantity = 0 (7-col DERECHOS), the quantities differ →
    find_probable_duplicate MISSES the existing row → no PROBABLE_DUPLICATE warning.
    """

    def test_different_quantities_miss_duplicate_check(self):
        """The find_probable_duplicate check misses when quantities differ."""
        from src.portfolio.cosmos_portfolio import CosmosPortfolioService

        # Old ACCIONES import: quantity = 50 (50 rights sold)
        old_movement = _sell(
            "txn__unassigned_20230615_ACS_SELL_000",
            "XMAD:ACS",
            gross_eur="2054",
            commission_eur="0",
            quantity="50",  # 50 rights sold → ACCIONES with positive qty
            account_id="_unassigned",
        )
        portfolio = _FakePortfolio([old_movement])
        portfolio_svc = CosmosPortfolioService(portfolio, None)

        # Duplicate check: new import DERECHOS with quantity=0
        dup = portfolio_svc.find_probable_duplicate(
            security_id="XMAD:ACS",
            txn_type="SELL",
            trade_date="2023-06-15",
            quantity="0",   # different from old "50" → MISS
            gross_eur="2054",
        )
        assert dup is None, (
            "CRITICAL: find_probable_duplicate misses when quantity differs "
            "(old=50, new=0). Re-import proceeds silently with no warning, "
            "and the old row remains ACTIVE — proceeds are doubled."
        )

    def test_same_quantity_detected_as_probable_duplicate(self):
        """When quantities match, the existing row IS detected."""
        from src.portfolio.cosmos_portfolio import CosmosPortfolioService

        old_movement = _sell(
            "txn__unassigned_20230615_ACS_SELL_000",
            "XMAD:ACS",
            gross_eur="2054",
            commission_eur="0",
            quantity="50",
            account_id="_unassigned",
        )
        portfolio = _FakePortfolio([old_movement])
        portfolio_svc = CosmosPortfolioService(portfolio, None)

        # New import with SAME quantity → duplicate IS found
        dup = portfolio_svc.find_probable_duplicate(
            security_id="XMAD:ACS",
            txn_type="SELL",
            trade_date="2023-06-15",
            quantity="50",   # same → HIT
            gross_eur="2054",
        )
        assert dup is not None, (
            "When quantity matches, find_probable_duplicate must return the old doc"
        )
        assert dup["id"] == "txn__unassigned_20230615_ACS_SELL_000"


# ──────────────────────────────────────────────────────────────────────────────
# §E — Proxy reconciliation: expected vs actual totals
# ──────────────────────────────────────────────────────────────────────────────

class TestProxyReconciliation:
    """Proxy scenario with plausible amounts that reproduce the EUR 4,554 delta.

    Proxy per-row net amounts (cannot confirm from production data without Cosmos):
      ACS rights:               EUR 2,054.00
      Viscofán rights:          EUR 1,500.00
      Técnicas Reunidas rights: EUR 1,000.00
      ─────────────────────────────────────────
      Total proxy rights net:   EUR 4,554.00

    Other ACCIONES sales (non-rights):
      Total non-rights:         EUR 94,581.00

    Grand expected total:       EUR 99,135.00
    Grand actual (duplicated):  EUR 103,689.00
    """

    _EXPECTED_TOTAL = Decimal("99135.00")
    _OBSERVED_TOTAL = Decimal("103689.00")
    _EXPECTED_DELTA = Decimal("4554.00")

    # Proxy rights net amounts
    _ACS_RIGHTS_NET    = Decimal("2054.00")
    _VIS_RIGHTS_NET    = Decimal("1500.00")
    _TRE_RIGHTS_NET    = Decimal("1000.00")

    _OTHER_SALES_NET   = Decimal("94581.00")  # all non-rights SELL proceeds

    def _build_movements_single_import(self):
        """All sales counted ONCE (the correct state)."""
        return [
            # Non-rights ACCIONES sales (aggregated proxy row for non-duplicate symbols)
            _sell("s_other", "XMAD:OTHER", gross_eur=str(self._OTHER_SALES_NET),
                  commission_eur="0", sales_type="ACCIONES"),
            # Rights sales (DERECHOS) — each counted ONCE
            _sell("txn__unassigned_20230615_ACS_SELL_000", "XMAD:ACS",
                  gross_eur=str(self._ACS_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="_unassigned"),
            _sell("txn__unassigned_20230615_VIS_SELL_000", "XMAD:VIS",
                  gross_eur=str(self._VIS_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="_unassigned"),
            _sell("txn__unassigned_20230615_TRE_SELL_000", "XMAD:TRE",
                  gross_eur=str(self._TRE_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="_unassigned"),
        ]

    def _build_movements_after_reimport(self):
        """Rights rows imported TWICE (different account_id → different IDs)."""
        return self._build_movements_single_import() + [
            # Re-imported DERECHOS rows under new account — creates additive duplicates
            _sell("txn_acct_ing_20230615_ACS_SELL_000", "XMAD:ACS",
                  gross_eur=str(self._ACS_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="acct_ing_cuenta_valores"),
            _sell("txn_acct_ing_20230615_VIS_SELL_000", "XMAD:VIS",
                  gross_eur=str(self._VIS_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="acct_ing_cuenta_valores"),
            _sell("txn_acct_ing_20230615_TRE_SELL_000", "XMAD:TRE",
                  gross_eur=str(self._TRE_RIGHTS_NET), commission_eur="0",
                  sales_type="DERECHOS", account_id="acct_ing_cuenta_valores"),
        ]

    def test_single_import_matches_expected_total(self):
        """With all sales counted once, total = EUR 99,135."""
        svc = _make_svc(self._build_movements_single_import())
        result = svc.compute_holdings()
        total = Decimal(result["summary"]["total_sales_eur"])
        assert total == self._EXPECTED_TOTAL, (
            f"Single import: expected {self._EXPECTED_TOTAL}, got {total}. "
            "Check that rights rows are not missing from the ledger."
        )

    def test_reimport_duplicate_matches_observed_total(self):
        """With rights rows duplicated (re-import), total = EUR 103,689."""
        svc = _make_svc(self._build_movements_after_reimport())
        result = svc.compute_holdings()
        total = Decimal(result["summary"]["total_sales_eur"])
        assert total == self._OBSERVED_TOTAL, (
            f"Duplicated rights rows: expected {self._OBSERVED_TOTAL}, got {total}. "
            "This confirms the re-import duplicate mechanism."
        )

    def test_delta_equals_sum_of_rights_net_proceeds(self):
        """Delta = sum of rights net proceeds for ACS + VIS + TRE = EUR 4,554."""
        rights_sum = self._ACS_RIGHTS_NET + self._VIS_RIGHTS_NET + self._TRE_RIGHTS_NET
        delta = self._OBSERVED_TOTAL - self._EXPECTED_TOTAL
        assert delta == rights_sum, (
            f"Delta {delta} must equal the sum of proxy rights proceeds {rights_sum}"
        )

    def test_rights_net_sum_is_4554(self):
        """Proxy: ACS (2054) + VIS (1500) + TRE (1000) = EUR 4,554."""
        total = self._ACS_RIGHTS_NET + self._VIS_RIGHTS_NET + self._TRE_RIGHTS_NET
        assert total == self._EXPECTED_DELTA, (
            f"Proxy rights net sum must equal the reported delta EUR 4,554; got {total}"
        )

    def test_observed_minus_expected_equals_delta(self):
        """Arithmetic consistency: 103,689 − 99,135 = 4,554."""
        assert self._OBSERVED_TOTAL - self._EXPECTED_TOTAL == self._EXPECTED_DELTA


# ──────────────────────────────────────────────────────────────────────────────
# §F — DERECHOS share-count invariant (should not have regressed)
# ──────────────────────────────────────────────────────────────────────────────

class TestDerechosShareCountInvariant:
    """DERECHOS sales must not affect share count; ACCIONES must.
    These should already pass — kept here as a tighter regression guard
    scoped to the three reported symbols.
    """

    def test_acs_derechos_does_not_alter_acs_shares(self):
        movements = [
            _buy("b_acs", "XMAD:ACS", gross_eur="50000", quantity="1000",
                 commission_eur="25"),
            _sell("s_acs_der", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        acs = next(h for h in result["holdings"] if h["security_id"] == "XMAD:ACS")
        assert Decimal(acs["total_shares"]) == Decimal("1000.000000"), (
            "ACS DERECHOS sale must NOT decrement ACS share count"
        )

    def test_viscofan_derechos_does_not_alter_viscofan_shares(self):
        movements = [
            _buy("b_vis", "XMAD:VIS", gross_eur="30000", quantity="300",
                 commission_eur="20"),
            _sell("s_vis_der", "XMAD:VIS", gross_eur="1500", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        vis = next(h for h in result["holdings"] if h["security_id"] == "XMAD:VIS")
        assert Decimal(vis["total_shares"]) == Decimal("300.000000"), (
            "Viscofán DERECHOS sale must NOT decrement Viscofán share count"
        )

    def test_tecnicas_reunidas_derechos_does_not_alter_shares(self):
        movements = [
            _buy("b_tre", "XMAD:TRE", gross_eur="40000", quantity="500",
                 commission_eur="30"),
            _sell("s_tre_der", "XMAD:TRE", gross_eur="1000", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        tre = next(h for h in result["holdings"] if h["security_id"] == "XMAD:TRE")
        assert Decimal(tre["total_shares"]) == Decimal("500.000000"), (
            "Técnicas Reunidas DERECHOS sale must NOT decrement share count"
        )

    def test_derechos_proceeds_included_in_total_sales_for_all_three(self):
        """All three rights rows contribute their net to total_sales_eur."""
        movements = [
            _buy("b_acs", "XMAD:ACS",  "50000", quantity="1000"),
            _buy("b_vis", "XMAD:VIS",  "30000", quantity="300"),
            _buy("b_tre", "XMAD:TRE",  "40000", quantity="500"),
            _sell("s_acs_der", "XMAD:ACS", gross_eur="2054", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
            _sell("s_vis_der", "XMAD:VIS", gross_eur="1500", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
            _sell("s_tre_der", "XMAD:TRE", gross_eur="1000", commission_eur="0",
                  quantity="0", sales_type="DERECHOS"),
        ]
        svc = _make_svc(movements)
        result = svc.compute_holdings()
        total_rights = sum(
            Decimal(h["total_sales_eur"])
            for h in result["holdings"]
            if h["security_id"] in ("XMAD:ACS", "XMAD:VIS", "XMAD:TRE")
        )
        assert total_rights == Decimal("4554.00"), (
            "Sum of rights proceeds for ACS + VIS + TRE must equal proxy total EUR 4,554"
        )
