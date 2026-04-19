"""Tests for the shared TradingView options chain parser."""

import json

import pytest

from src.options_chain_parser import parse_options_chain

# ---------------------------------------------------------------------------
# Fixtures / sample data
# ---------------------------------------------------------------------------

SAMPLE_RAW = json.dumps(
    {
        "totalCount": 2,
        "fields": [
            "ask", "bid", "currency", "delta", "expiration", "gamma", "iv",
            "option-type", "pricescale", "rho", "root", "strike", "theoPrice",
            "theta", "vega", "bid_iv", "ask_iv",
        ],
        "symbols": [
            {
                "s": "OPRA:MSFT260427C475.0",
                "f": [
                    0.27, 0.22, "USD", 0.026170699504649623, 20260427,
                    0.0024162608428573555, 0.3641050696372986, "call", 100,
                    0.0028870576386780767, "MSFT", 475, 0.24,
                    -0.07856673944901946, 0.04191869514060787,
                    0.3579396480778897, 0.369889918728596,
                ],
            },
            {
                "s": "OPRA:MSFT260508P555.0",
                "f": [
                    133.9, 130.2, "USD", -0.9846641892771554, 20260508,
                    0.0007451740866495429, 0.5143734061323084, "put", 100,
                    -0.31180700777125736, "MSFT", 555, 132.6,
                    -0.0331475746037788, 0.03886748172387087,
                    0.5251052366300346, 0.6850668526564379,
                ],
            },
        ],
        "time": "2026-04-18T13:33:51Z",
    }
)


# ---------------------------------------------------------------------------
# 1. Valid JSON
# ---------------------------------------------------------------------------

class TestParseValidJson:
    def test_calls_and_puts_structure(self):
        result = parse_options_chain(SAMPLE_RAW, symbol="MSFT")
        assert "20260427" in result["calls"]
        assert len(result["calls"]["20260427"]) == 1
        assert "20260508" in result["puts"]
        assert len(result["puts"]["20260508"]) == 1

    def test_symbol(self):
        result = parse_options_chain(SAMPLE_RAW, symbol="MSFT")
        assert result["symbol"] == "MSFT"

    def test_timestamp(self):
        result = parse_options_chain(SAMPLE_RAW, symbol="MSFT")
        assert result["timestamp"] == "2026-04-18T13:33:51Z"

    def test_call_field_values(self):
        result = parse_options_chain(SAMPLE_RAW, symbol="MSFT")
        call = result["calls"]["20260427"][0]
        assert call["opra_symbol"] == "OPRA:MSFT260427C475.0"
        assert call["strike"] == 475
        assert call["bid"] == 0.22
        assert call["ask"] == 0.27
        assert call["mid"] == 0.24
        assert call["iv"] == pytest.approx(0.3641050696372986)
        assert call["delta"] == pytest.approx(0.026170699504649623)
        assert call["gamma"] == pytest.approx(0.0024162608428573555)
        assert call["theta"] == pytest.approx(-0.07856673944901946)
        assert call["vega"] == pytest.approx(0.04191869514060787)
        assert call["rho"] == pytest.approx(0.0028870576386780767)
        assert call["bid_iv"] == pytest.approx(0.3579396480778897)
        assert call["ask_iv"] == pytest.approx(0.369889918728596)

    def test_put_field_values(self):
        result = parse_options_chain(SAMPLE_RAW, symbol="MSFT")
        put = result["puts"]["20260508"][0]
        assert put["opra_symbol"] == "OPRA:MSFT260508P555.0"
        assert put["strike"] == 555
        assert put["bid"] == 130.2
        assert put["ask"] == 133.9
        assert put["mid"] == 132.6
        assert put["delta"] == pytest.approx(-0.9846641892771554)
        assert put["rho"] == pytest.approx(-0.31180700777125736)


# ---------------------------------------------------------------------------
# 2. Header prefix stripping
# ---------------------------------------------------------------------------

def test_parse_with_header_prefix():
    prefixed = "OPTIONS CHAIN DATA (API intercepted, 1 responses captured):\n" + SAMPLE_RAW
    result = parse_options_chain(prefixed, symbol="MSFT")
    assert "20260427" in result["calls"]
    assert "20260508" in result["puts"]
    assert result["timestamp"] == "2026-04-18T13:33:51Z"


# ---------------------------------------------------------------------------
# 3. Empty string
# ---------------------------------------------------------------------------

def test_parse_empty_string():
    result = parse_options_chain("", symbol="TEST")
    assert result["calls"] == {}
    assert result["puts"] == {}
    assert result["symbol"] == "TEST"
    assert result["timestamp"] is None


# ---------------------------------------------------------------------------
# 4. Malformed JSON
# ---------------------------------------------------------------------------

def test_parse_malformed_json():
    result = parse_options_chain("this is not json at all", symbol="X")
    assert result["calls"] == {}
    assert result["puts"] == {}


# ---------------------------------------------------------------------------
# 5. No symbols
# ---------------------------------------------------------------------------

def test_parse_no_symbols():
    raw = json.dumps({"totalCount": 0, "fields": [], "symbols": [], "time": "2026-01-01T00:00:00Z"})
    result = parse_options_chain(raw)
    assert result["calls"] == {}
    assert result["puts"] == {}
    assert result["timestamp"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 6. Short fields — item should be skipped
# ---------------------------------------------------------------------------

def test_parse_short_fields():
    raw = json.dumps({
        "symbols": [
            {"s": "OPRA:BAD", "f": [1.0, 2.0, "USD", 0.5, 20260101, 0.01]},  # only 6 fields
        ],
    })
    result = parse_options_chain(raw, symbol="BAD")
    assert result["calls"] == {}
    assert result["puts"] == {}


# ---------------------------------------------------------------------------
# 7. Sorting by strike within same expiration
# ---------------------------------------------------------------------------

def test_sorting_by_strike():
    def _make_call(strike):
        return {
            "s": f"OPRA:TEST260427C{strike}.0",
            "f": [1, 1, "USD", 0.5, 20260427, 0.01, 0.3, "call", 100, 0.01,
                  "TEST", strike, 1, -0.01, 0.01, 0.3, 0.3],
        }

    raw = json.dumps({
        "symbols": [_make_call(500), _make_call(400), _make_call(450)],
    })
    result = parse_options_chain(raw, symbol="TEST")
    strikes = [c["strike"] for c in result["calls"]["20260427"]]
    assert strikes == [400, 450, 500]


# ---------------------------------------------------------------------------
# 8. Multiple expirations sorted chronologically
# ---------------------------------------------------------------------------

def test_multiple_expirations_sorted():
    def _make_call(exp, strike=100):
        return {
            "s": f"OPRA:T{exp}C{strike}.0",
            "f": [1, 1, "USD", 0.5, exp, 0.01, 0.3, "call", 100, 0.01,
                  "T", strike, 1, -0.01, 0.01, 0.3, 0.3],
        }

    raw = json.dumps({
        "symbols": [_make_call(20260601), _make_call(20260401), _make_call(20260501)],
    })
    result = parse_options_chain(raw, symbol="T")
    expirations = list(result["calls"].keys())
    assert expirations == ["20260401", "20260501", "20260601"]


# ---------------------------------------------------------------------------
# 9. Fallback "data" key instead of "symbols"
# ---------------------------------------------------------------------------

def test_parse_with_data_key():
    raw = json.dumps({
        "data": [
            {
                "s": "OPRA:ABC260427C100.0",
                "f": [2.0, 1.5, "USD", 0.4, 20260427, 0.01, 0.25, "call", 100,
                      0.005, "ABC", 100, 1.75, -0.05, 0.03, 0.24, 0.26],
            }
        ],
        "time": "2026-04-18T10:00:00Z",
    })
    result = parse_options_chain(raw, symbol="ABC")
    assert "20260427" in result["calls"]
    assert result["calls"]["20260427"][0]["strike"] == 100


# ---------------------------------------------------------------------------
# 10. Field mapping accuracy — verify every index
# ---------------------------------------------------------------------------

def test_field_mapping_accuracy():
    """Verify that each positional field index maps to the correct output key."""
    fields = [
        10.0,    # f[0]  = ask
        9.0,     # f[1]  = bid
        "USD",   # f[2]  = currency
        0.55,    # f[3]  = delta
        20260601,# f[4]  = expiration
        0.033,   # f[5]  = gamma
        0.42,    # f[6]  = iv
        "call",  # f[7]  = option-type
        100,     # f[8]  = pricescale
        0.007,   # f[9]  = rho
        "ZZZ",   # f[10] = root
        300,     # f[11] = strike
        9.5,     # f[12] = theoPrice / mid
        -0.12,   # f[13] = theta
        0.08,    # f[14] = vega
        0.41,    # f[15] = bid_iv
        0.43,    # f[16] = ask_iv
    ]
    raw = json.dumps({"symbols": [{"s": "OPRA:ZZZ260601C300.0", "f": fields}]})
    result = parse_options_chain(raw, symbol="ZZZ")
    opt = result["calls"]["20260601"][0]

    assert opt["ask"] == 10.0,   "f[0] → ask"
    assert opt["bid"] == 9.0,    "f[1] → bid"
    assert opt["delta"] == 0.55, "f[3] → delta"
    assert opt["gamma"] == 0.033,"f[5] → gamma"
    assert opt["iv"] == 0.42,    "f[6] → iv"
    assert opt["rho"] == 0.007,  "f[9] → rho"
    assert opt["strike"] == 300, "f[11] → strike"
    assert opt["mid"] == 9.5,    "f[12] → mid"
    assert opt["theta"] == -0.12,"f[13] → theta"
    assert opt["vega"] == 0.08,  "f[14] → vega"
    assert opt["bid_iv"] == 0.41,"f[15] → bid_iv"
    assert opt["ask_iv"] == 0.43,"f[16] → ask_iv"
