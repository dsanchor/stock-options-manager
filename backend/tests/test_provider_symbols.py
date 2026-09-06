"""Unit tests for provider_symbols module.

Covers PS-B10 through PS-B12 (suggest_yfinance_symbol) and validation
edge cases from §5 of the danny-provider-symbol-import-contract.md.
"""

import pytest
from src.portfolio.provider_symbols import (
    suggest_yfinance_symbol,
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
