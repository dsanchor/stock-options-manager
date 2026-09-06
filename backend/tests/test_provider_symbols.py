"""Unit tests for provider_symbols module.

Covers PS-B10 through PS-B12 (suggest_yfinance_symbol) and validation
edge cases from §5 of the danny-provider-symbol-import-contract.md.
"""

import pytest
from src.portfolio.provider_symbols import (
    suggest_yfinance_symbol,
    resolve_yfinance_symbol,
    validate_provider_symbols,
    MIC_TO_YFINANCE_SUFFIX,
)


# ---------------------------------------------------------------------------
# suggest_yfinance_symbol — PS-B10, PS-B11, PS-B12
# ---------------------------------------------------------------------------

class TestSuggestYfinanceSymbol:
    def test_xmad_appends_mc(self):
        # PS-B10
        assert suggest_yfinance_symbol("ENG", "XMAD") == "ENG.MC"

    def test_xnys_no_suffix(self):
        # PS-B11
        assert suggest_yfinance_symbol("AAPL", "XNYS") == "AAPL"

    def test_xnas_no_suffix(self):
        assert suggest_yfinance_symbol("MSFT", "XNAS") == "MSFT"

    def test_unknown_mic_returns_none(self):
        # PS-B12
        assert suggest_yfinance_symbol("FOO", "XZZZ") is None

    def test_mic_case_insensitive(self):
        assert suggest_yfinance_symbol("ENG", "xmad") == "ENG.MC"

    def test_ticker_uppercased(self):
        assert suggest_yfinance_symbol("eng", "XMAD") == "ENG.MC"

    def test_all_known_mics_return_string(self):
        for mic in MIC_TO_YFINANCE_SUFFIX:
            result = suggest_yfinance_symbol("TST", mic)
            assert isinstance(result, str), f"Expected str for MIC {mic}, got {result!r}"

    def test_xams_euronext_amsterdam(self):
        assert suggest_yfinance_symbol("ASML", "XAMS") == "ASML.AS"

    def test_xlon_london(self):
        assert suggest_yfinance_symbol("HSBA", "XLON") == "HSBA.L"

    def test_xpar_paris(self):
        assert suggest_yfinance_symbol("BNP", "XPAR") == "BNP.PA"

    def test_xetr_xetra(self):
        assert suggest_yfinance_symbol("SAP", "XETR") == "SAP.DE"

    def test_xswx_swiss_exchange(self):
        # Contract §1b — XSWX → .SW
        assert suggest_yfinance_symbol("NESN", "XSWX") == "NESN.SW"

    def test_xbru_brussels(self):
        assert suggest_yfinance_symbol("ABI", "XBRU") == "ABI.BR"

    def test_xlis_lisbon(self):
        assert suggest_yfinance_symbol("EDP", "XLIS") == "EDP.LS"


# ---------------------------------------------------------------------------
# validate_provider_symbols — §5
# ---------------------------------------------------------------------------

class TestValidateProviderSymbols:
    def test_valid_yfinance_value_with_dot(self):
        result = validate_provider_symbols({"yfinance": "ENG.MC"})
        assert result == {"yfinance": "ENG.MC"}

    def test_valid_yfinance_hyphen(self):
        # BRK-B is a valid yfinance symbol
        result = validate_provider_symbols({"yfinance": "BRK-B"})
        assert result == {"yfinance": "BRK-B"}

    def test_valid_yfinance_caret(self):
        # Caret is allowed per contract §5.1
        result = validate_provider_symbols({"yfinance": "^GSPC"})
        assert result == {"yfinance": "^GSPC"}

    def test_valid_bare_ticker(self):
        result = validate_provider_symbols({"yfinance": "AAPL"})
        assert result == {"yfinance": "AAPL"}

    def test_empty_dict_returns_empty(self):
        assert validate_provider_symbols({}) == {}

    def test_none_returns_empty(self):
        assert validate_provider_symbols(None) == {}

    def test_empty_value_key_dropped(self):
        # PS-B9
        result = validate_provider_symbols({"yfinance": ""})
        assert result == {}

    def test_whitespace_only_value_dropped(self):
        result = validate_provider_symbols({"yfinance": "   "})
        assert result == {}

    def test_value_trimmed_before_validation(self):
        result = validate_provider_symbols({"yfinance": " ENG.MC "})
        assert result == {"yfinance": "ENG.MC"}

    def test_invalid_key_yahoo_bang(self):
        # PS-B6 — key contains '!'
        with pytest.raises(ValueError, match="invalid key"):
            validate_provider_symbols({"Yahoo!": "ENG.MC"})

    def test_invalid_key_uppercase(self):
        # Keys must be lowercase
        with pytest.raises(ValueError, match="invalid key"):
            validate_provider_symbols({"Yfinance": "ENG.MC"})

    def test_invalid_key_with_dot(self):
        with pytest.raises(ValueError, match="invalid key"):
            validate_provider_symbols({"y.finance": "ENG.MC"})

    def test_invalid_value_with_space(self):
        # PS-B7 — space in value
        with pytest.raises(ValueError, match="invalid value"):
            validate_provider_symbols({"yfinance": "ENG MC"})

    def test_invalid_value_too_long(self):
        # PS-B8 — value > 30 chars
        long_val = "A" * 31
        with pytest.raises(ValueError, match="invalid value"):
            validate_provider_symbols({"yfinance": long_val})

    def test_value_exactly_30_chars_ok(self):
        val = "A" * 30
        result = validate_provider_symbols({"yfinance": val})
        assert result == {"yfinance": val}

    def test_too_many_keys_raises(self):
        ps = {f"provider{i}": "SYM" for i in range(11)}
        with pytest.raises(ValueError, match="max 10"):
            validate_provider_symbols(ps)

    def test_exactly_10_keys_ok(self):
        ps = {f"provider{i}": "SYM" for i in range(10)}
        result = validate_provider_symbols(ps)
        assert len(result) == 10

    def test_multiple_valid_providers(self):
        ps = {"yfinance": "ENG.MC", "bloomberg": "ENG-SM"}
        result = validate_provider_symbols(ps)
        assert result == {"yfinance": "ENG.MC", "bloomberg": "ENG-SM"}

    def test_non_dict_input_returns_empty(self):
        assert validate_provider_symbols("yfinance=ENG.MC") == {}
        assert validate_provider_symbols(42) == {}

    def test_invalid_value_with_at_sign(self):
        with pytest.raises(ValueError, match="invalid value"):
            validate_provider_symbols({"yfinance": "ENG@MC"})


# ---------------------------------------------------------------------------
# resolve_yfinance_symbol — danny-yahoo-symbol-resolution-contract.md
# ---------------------------------------------------------------------------

class TestResolveYfinanceSymbol:
    def test_override_takes_precedence_over_suffix(self):
        # Nestlé: SIX-listed NESN would otherwise suggest NESN.SW via suffix,
        # but an explicit override must win.
        sec_doc = {"provider_symbols": {"yfinance": "NESN.SW"}}
        assert resolve_yfinance_symbol("NESN", "XSWX", sec_doc) == "NESN.SW"

    def test_override_wins_even_with_different_value_than_suffix(self):
        sec_doc = {"provider_symbols": {"yfinance": "WEIRD.OVERRIDE"}}
        assert resolve_yfinance_symbol("ABC", "XMAD", sec_doc) == "WEIRD.OVERRIDE"

    def test_missing_provider_symbols_falls_through_to_suffix(self):
        assert resolve_yfinance_symbol("ULVR", "XLON", {}) == "ULVR.L"
        assert resolve_yfinance_symbol("ULVR", "XLON", None) == "ULVR.L"

    def test_provider_symbols_without_yfinance_key_falls_through(self):
        sec_doc = {"provider_symbols": {"bloomberg": "ULVR-LN"}}
        assert resolve_yfinance_symbol("ULVR", "XLON", sec_doc) == "ULVR.L"

    def test_unknown_mic_returns_none(self):
        assert resolve_yfinance_symbol("FOO", "XZZZ") is None

    def test_unknown_mic_returns_none_even_with_security_doc(self):
        assert resolve_yfinance_symbol("FOO", "XZZZ", {}) is None

    def test_missing_mic_returns_none(self):
        assert resolve_yfinance_symbol("FOO", "") is None
        assert resolve_yfinance_symbol("FOO", None) is None

    def test_xnys_xnas_remain_bare(self):
        assert resolve_yfinance_symbol("AAPL", "XNYS") == "AAPL"
        assert resolve_yfinance_symbol("MSFT", "XNAS") == "MSFT"

    def test_legacy_us_display_name_aliases_remain_bare(self):
        # Pre-security_master docs store display names, not MIC codes.
        assert resolve_yfinance_symbol("AAPL", "NASDAQ") == "AAPL"
        assert resolve_yfinance_symbol("IBM", "NYSE") == "IBM"
        assert resolve_yfinance_symbol("AMC", "AMEX") == "AMC"

    def test_empty_override_value_falls_through_to_suffix(self):
        sec_doc = {"provider_symbols": {"yfinance": ""}}
        assert resolve_yfinance_symbol("ULVR", "XLON", sec_doc) == "ULVR.L"


# ---------------------------------------------------------------------------
# resolve_yfinance_symbol — per-MIC explicit suffix cases (no security_doc)
# Contract §1b, §1c: suffix table fallback for each exchange in scope.
# ---------------------------------------------------------------------------

class TestResolveYfinanceSymbolPerMic:
    """Verify every exchange MIC in scope resolves via the suffix table
    (no security_master doc needed — suffix table is always the fallback).
    """

    @pytest.mark.parametrize("ticker,mic,expected", [
        # US — bare ticker (no suffix)
        ("AAPL", "XNYS", "AAPL"),
        ("MSFT", "XNAS", "MSFT"),
        # European exchanges in scope
        ("ENG",   "XMAD", "ENG.MC"),
        ("ULVR",  "XLON", "ULVR.L"),
        ("SAP",   "XETR", "SAP.DE"),
        ("NESN",  "XSWX", "NESN.SW"),
        ("BNP",   "XPAR", "BNP.PA"),
        ("ASML",  "XAMS", "ASML.AS"),
        ("ABI",   "XBRU", "ABI.BR"),
        ("EDP",   "XLIS", "EDP.LS"),
    ])
    def test_suffix_applied_no_security_doc(self, ticker, mic, expected):
        """§1b: suffix table used when no security_master doc provided."""
        assert resolve_yfinance_symbol(ticker, mic) == expected

    def test_xmad_real_ticker_acs(self):
        """Explicit real-world example: ACS traded on XMAD → ACS.MC."""
        assert resolve_yfinance_symbol("ACS", "XMAD") == "ACS.MC"

    def test_xlon_real_ticker_hsba(self):
        """HSBA on London → HSBA.L."""
        assert resolve_yfinance_symbol("HSBA", "XLON") == "HSBA.L"

    def test_xetr_real_ticker_bayer(self):
        """Bayer on Xetra → BAYN.DE."""
        assert resolve_yfinance_symbol("BAYN", "XETR") == "BAYN.DE"

    def test_xswx_real_ticker_abb(self):
        """ABB on SIX → ABB.SW."""
        assert resolve_yfinance_symbol("ABB", "XSWX") == "ABB.SW"

    def test_unknown_mic_no_bare_ticker_fallback(self):
        """§1c: unknown MIC returns None — must never fall back to bare ticker."""
        result = resolve_yfinance_symbol("BAYGN", "XZZZ")
        assert result is None, (
            "Unknown MIC must fail closed; bare ticker must NOT be returned "
            "to avoid wrong-security enrichment on Yahoo."
        )

    def test_unknown_mic_no_bare_ticker_fallback_with_security_doc(self):
        """§1c enforced even when a security_master doc is present (but has no override)."""
        sec_doc = {"provider_symbols": {"bloomberg": "BAYGN-GR"}}  # no yfinance key
        result = resolve_yfinance_symbol("BAYGN", "XZZZ", sec_doc)
        assert result is None

    def test_override_beats_suffix_for_every_mic(self):
        """§1a: explicit override always wins over any suffix-table result."""
        override = "EXPLICIT.OVERRIDE"
        for mic in ("XMAD", "XLON", "XETR", "XSWX", "XNYS", "XNAS"):
            sec_doc = {"provider_symbols": {"yfinance": override}}
            result = resolve_yfinance_symbol("ANY", mic, sec_doc)
            assert result == override, (
                f"Override must win over suffix for MIC {mic}"
            )
