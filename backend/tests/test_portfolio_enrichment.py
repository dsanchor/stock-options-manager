"""Focused tests for Yahoo symbol resolution wired into portfolio enrichment.

Ref: danny-yahoo-symbol-resolution-contract.md §7.

Hermetic — no network, no real CosmosDB, no real yfinance. Mocks
``analyze_single_symbol`` (the yfinance boundary) as existing tests do, and
uses an in-memory fake symbols container so ``CosmosSecuritiesService`` can
look up security_master docs exactly as production code does.

Covers:
  - XMAD/XLON/XETR/XSWX suffix routing (fetcher invoked with suffixed
    symbol, not the bare ticker).
  - Explicit per-security override takes precedence over the suffix table.
  - Unknown/missing MIC -> skip + warning, counted in errors, no crash,
    and no call into the yfinance boundary at all (fail closed).
  - XNYS/XNAS (and legacy free-text US exchange labels) remain bare —
    zero behavior change for the existing US screener universe.
"""

from __future__ import annotations

import asyncio

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError

import src.portfolio_enrichment as portfolio_enrichment
from src.portfolio.cosmos_securities import security_id_to_doc_id


def _make_symbol_doc(symbol: str, exchange: str, security_id: str | None = None) -> dict:
    return {
        "id": f"config_{symbol}",
        "symbol": symbol,
        "doc_type": "symbol_config",
        "exchange": exchange,
        "security_id": security_id,
        "display_name": symbol,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


class FakeContainer:
    """In-memory stand-in for the Cosmos 'symbols' container.

    Only implements ``read_item`` (what ``CosmosSecuritiesService.get_security``
    needs) keyed by (doc_id, partition_key=ticker).
    """

    def __init__(self, security_master_docs: dict[str, dict] | None = None):
        # keyed by doc_id (e.g. "sec_XSWX_NESN")
        self._docs = dict(security_master_docs or {})

    def read_item(self, item: str, partition_key: str):
        doc = self._docs.get(item)
        if doc is None:
            raise CosmosResourceNotFoundError(message="not found")
        return doc


class FakeCosmos:
    """In-memory fake for CosmosDBService (subset needed by enrichment)."""

    def __init__(self, symbol_docs: list[dict], container: FakeContainer):
        self._symbol_docs = symbol_docs
        self.container = container
        self.updated: dict[str, dict] = {}
        self.snapshots: list[tuple] = []

    def list_symbols(self) -> list[dict]:
        return list(self._symbol_docs)

    def update_symbol_enrichment(self, symbol: str, enrichment: dict) -> dict:
        self.updated[symbol] = enrichment
        return enrichment

    def record_enrichment_snapshot(self, symbol, tech_timing, momentum):
        self.snapshots.append((symbol, tech_timing, momentum))
        return None


def _fake_analyze_ok(fetch_calls: list[str]):
    def _analyze(symbol: str, filters: dict = None, yf_symbol: str = None) -> dict:
        fetch_calls.append(yf_symbol or symbol)
        return {
            "symbol": symbol,
            "quality_score": 50.0,
            "quality_detail": {},
            "category": "core",
            "entry_tag": "Buy",
            "momentum": "Bullish",
            "metrics": {},
            "technicals": {"score": 60},
            "has_dividends": True,
            "filter_detail": None,
        }
    return _analyze


def _run(cosmos):
    return asyncio.run(portfolio_enrichment.run_portfolio_enrichment(cosmos))


class TestSuffixRouting:
    """XMAD/XLON/XETR/XSWX — fetcher must receive the suffixed symbol."""

    @pytest.mark.parametrize("ticker,mic,expected", [
        ("ENG", "XMAD", "ENG.MC"),
        ("ULVR", "XLON", "ULVR.L"),
        ("SAP", "XETR", "SAP.DE"),
        ("NESN", "XSWX", "NESN.SW"),
    ])
    def test_suffix_applied_for_known_mic(self, monkeypatch, ticker, mic, expected):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        doc = _make_symbol_doc(ticker, mic)
        cosmos = FakeCosmos([doc], FakeContainer())

        result = _run(cosmos)

        assert fetch_calls == [expected]
        assert result["success"] == 1
        assert result["errors"] == 0
        assert ticker in cosmos.updated

    def test_xnys_bare_ticker_unchanged(self, monkeypatch):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))
        doc = _make_symbol_doc("AAPL", "XNYS")
        cosmos = FakeCosmos([doc], FakeContainer())

        result = _run(cosmos)

        assert fetch_calls == ["AAPL"]
        assert result["success"] == 1

    def test_legacy_us_display_name_exchange_unchanged(self, monkeypatch):
        # Pre-security_master watchlist docs store "NASDAQ", not a MIC code.
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))
        doc = _make_symbol_doc("MSFT", "NASDAQ")
        cosmos = FakeCosmos([doc], FakeContainer())

        result = _run(cosmos)

        assert fetch_calls == ["MSFT"]
        assert result["success"] == 1


class TestOverridePrecedence:
    def test_provider_symbols_override_wins_over_suffix(self, monkeypatch):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        security_id = "XSWX:NESN"
        doc = _make_symbol_doc("NESN", "XSWX", security_id=security_id)
        sec_doc = {
            "id": security_id_to_doc_id(security_id),
            "symbol": "NESN",
            "doc_type": "security_master",
            "security_id": security_id,
            "exchange_mic": "XSWX",
            "provider_symbols": {"yfinance": "NESN.SW"},
        }
        container = FakeContainer({sec_doc["id"]: sec_doc})
        cosmos = FakeCosmos([doc], container)

        result = _run(cosmos)

        assert fetch_calls == ["NESN.SW"]
        assert result["success"] == 1


class TestUnknownMicFailsClosed:
    def test_unknown_mic_skips_without_crash(self, monkeypatch):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        doc = _make_symbol_doc("FOO", "XZZZ")
        cosmos = FakeCosmos([doc], FakeContainer())

        result = _run(cosmos)

        # Fail closed: never falls back to the bare ticker for an unknown MIC.
        assert fetch_calls == []
        assert result["errors"] == 1
        assert result["success"] == 0
        assert "FOO" not in cosmos.updated

    def test_missing_exchange_skips_without_crash(self, monkeypatch):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        doc = _make_symbol_doc("BAR", "")
        cosmos = FakeCosmos([doc], FakeContainer())

        result = _run(cosmos)

        assert fetch_calls == []
        assert result["errors"] == 1
        assert result["success"] == 0

    def test_mixed_batch_partial_success(self, monkeypatch):
        """One resolvable symbol and one unresolvable symbol in the same run."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        good = _make_symbol_doc("DGE", "XLON")
        bad = _make_symbol_doc("BAYGN", "XZZZ")
        cosmos = FakeCosmos([good, bad], FakeContainer())

        result = _run(cosmos)

        assert fetch_calls == ["DGE.L"]
        assert result["success"] == 1
        assert result["errors"] == 1
        assert result["total"] == 2


# ===========================================================================
# Persistence identity — canonical ticker stored, not YF symbol
# Contract §2: "local ticker (for storage keys, logging, Cosmos doc identity
# — unchanged) and the resolved Yahoo symbol (for the fetch call only)".
# ===========================================================================

class TestPersistenceIdentity:
    """The storage key in Cosmos must always be the local/canonical ticker,
    regardless of what Yahoo Finance symbol was used to fetch data."""

    @pytest.mark.parametrize("ticker,mic,yf_expected", [
        ("ENG",   "XMAD", "ENG.MC"),
        ("ULVR",  "XLON", "ULVR.L"),
        ("SAP",   "XETR", "SAP.DE"),
        ("NESN",  "XSWX", "NESN.SW"),
    ])
    def test_cosmos_key_is_canonical_ticker_not_yf_symbol(self, monkeypatch, ticker, mic, yf_expected):
        """Yahoo symbol used for fetch; canonical ticker used for storage."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        doc = _make_symbol_doc(ticker, mic)
        cosmos = FakeCosmos([doc], FakeContainer())
        _run(cosmos)

        # Fetch boundary received the suffixed YF symbol
        assert fetch_calls == [yf_expected], (
            f"Expected fetch with YF symbol {yf_expected!r}, got {fetch_calls}"
        )
        # Storage key is the local ticker, not the YF symbol
        assert ticker in cosmos.updated, (
            f"Enrichment must be stored under local ticker {ticker!r}, not {yf_expected!r}"
        )
        assert yf_expected not in cosmos.updated, (
            f"YF symbol {yf_expected!r} must NOT be used as the Cosmos storage key"
        )

    def test_override_symbol_fetch_but_ticker_stored(self, monkeypatch):
        """Even when an explicit provider_symbols override is used for fetch,
        the canonical ticker remains the Cosmos storage key."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        ticker = "NESN"
        security_id = "XSWX:NESN"
        doc = _make_symbol_doc(ticker, "XSWX", security_id=security_id)
        sec_doc = {
            "id": security_id_to_doc_id(security_id),
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "exchange_mic": "XSWX",
            "provider_symbols": {"yfinance": "NESN.SW"},
        }
        container = FakeContainer({sec_doc["id"]: sec_doc})
        cosmos = FakeCosmos([doc], container)
        _run(cosmos)

        assert fetch_calls == ["NESN.SW"]
        assert ticker in cosmos.updated
        assert "NESN.SW" not in cosmos.updated


# ===========================================================================
# enrich_symbol() direct tests
# Contract §7: "enrich_symbol/run_portfolio_enrichment with … fixtures"
# ===========================================================================

class TestEnrichSymbolDirect:
    """Direct tests for enrich_symbol() — verifies it threads yf_symbol
    through to analyze_single_symbol correctly."""

    def test_enrich_symbol_passes_yf_symbol_to_analyzer(self, monkeypatch):
        """yf_symbol forwarded to analyze_single_symbol, not shadowed by symbol."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(
            portfolio_enrichment, "analyze_single_symbol",
            _fake_analyze_ok(fetch_calls),
        )
        result = portfolio_enrichment.enrich_symbol("NESN", yf_symbol="NESN.SW")
        assert fetch_calls == ["NESN.SW"], (
            "enrich_symbol must pass yf_symbol to analyze_single_symbol"
        )
        assert result is not None
        assert isinstance(result.get("quality_score"), float)

    def test_enrich_symbol_defaults_to_symbol_when_no_yf_symbol(self, monkeypatch):
        """Backward compat: yf_symbol=None → analyze_single_symbol receives symbol."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(
            portfolio_enrichment, "analyze_single_symbol",
            _fake_analyze_ok(fetch_calls),
        )
        portfolio_enrichment.enrich_symbol("AAPL", yf_symbol=None)
        assert fetch_calls == ["AAPL"], (
            "Without yf_symbol, enrich_symbol must default to the canonical ticker"
        )

    def test_enrich_symbol_returns_none_on_analyzer_error(self, monkeypatch):
        """enrich_symbol returns None when analyze_single_symbol reports an error."""
        def _error_analyze(symbol, filters=None, yf_symbol=None):
            return {"error": "No data available for NESN.SW"}
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol", _error_analyze)
        result = portfolio_enrichment.enrich_symbol("NESN", yf_symbol="NESN.SW")
        assert result is None

    def test_enrich_symbol_returns_expected_fields(self, monkeypatch):
        """Enrichment dict has the fields expected by the storage layer."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(
            portfolio_enrichment, "analyze_single_symbol",
            _fake_analyze_ok(fetch_calls),
        )
        result = portfolio_enrichment.enrich_symbol("SAP", yf_symbol="SAP.DE")
        assert result is not None
        for field in ("last_updated", "quality_score", "category", "entry_tag",
                      "momentum", "metrics", "technicals", "has_dividends"):
            assert field in result, f"Expected field {field!r} missing from enrichment dict"


# ===========================================================================
# Multi-MIC batch — all four target exchanges in a single run
# ===========================================================================

class TestMultiMicBatch:
    """All four contract exchanges (XMAD, XLON, XETR, XSWX) enriched together.
    Ensures no cross-contamination of symbols and all stored under local tickers.
    """

    def test_all_four_exchanges_resolved_and_stored(self, monkeypatch):
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        symbols = [
            _make_symbol_doc("ENG",  "XMAD"),
            _make_symbol_doc("ULVR", "XLON"),
            _make_symbol_doc("SAP",  "XETR"),
            _make_symbol_doc("NESN", "XSWX"),
        ]
        cosmos = FakeCosmos(symbols, FakeContainer())
        result = _run(cosmos)

        assert result["success"] == 4
        assert result["errors"] == 0
        assert set(fetch_calls) == {"ENG.MC", "ULVR.L", "SAP.DE", "NESN.SW"}
        # Storage keys are all canonical tickers
        assert set(cosmos.updated.keys()) == {"ENG", "ULVR", "SAP", "NESN"}

    def test_unknown_mic_symbol_does_not_pollute_others(self, monkeypatch):
        """An unknown-MIC symbol in the middle of the batch doesn't abort the run
        or corrupt the resolved symbols on either side."""
        fetch_calls: list[str] = []
        monkeypatch.setattr(portfolio_enrichment, "analyze_single_symbol",
                            _fake_analyze_ok(fetch_calls))

        symbols = [
            _make_symbol_doc("ENG",   "XMAD"),
            _make_symbol_doc("BAYGN", "XZZZ"),   # unknown
            _make_symbol_doc("ULVR",  "XLON"),
        ]
        cosmos = FakeCosmos(symbols, FakeContainer())
        result = _run(cosmos)

        assert result["success"] == 2
        assert result["errors"] == 1
        assert "BAYGN" not in cosmos.updated
        assert fetch_calls == ["ENG.MC", "ULVR.L"]
