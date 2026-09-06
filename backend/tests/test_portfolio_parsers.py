"""Tests for the three domain CSV parsers.

All tests are hermetic: no Cosmos connectivity, no network.
Synthetic fixtures only.
"""

import pytest
from decimal import Decimal

from src.portfolio.parsers.dividends import parse_dividends
from src.portfolio.parsers.purchases import parse_purchases
from src.portfolio.parsers.sales import parse_sales
from src.portfolio.parsers.common import (
    parse_spanish_decimal,
    parse_spanish_date,
    normalize_company_name,
    row_idempotency_hash,
)


# ---------------------------------------------------------------------------
# Shared CSV helpers
# ---------------------------------------------------------------------------

def _encode(text: str) -> bytes:
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Common utilities
# ---------------------------------------------------------------------------

class TestParseSpanishDecimal:
    def test_simple_decimal(self):
        assert parse_spanish_decimal("123,45") == Decimal("123.45")

    def test_thousands_separator(self):
        assert parse_spanish_decimal("1.234,56") == Decimal("1234.56")

    def test_large_amount(self):
        assert parse_spanish_decimal("1.234.567,89") == Decimal("1234567.89")

    def test_zero(self):
        assert parse_spanish_decimal("0") == Decimal("0")

    def test_zero_comma(self):
        assert parse_spanish_decimal("0,00") == Decimal("0")

    def test_empty(self):
        assert parse_spanish_decimal("") is None

    def test_none(self):
        assert parse_spanish_decimal(None) is None

    def test_na(self):
        assert parse_spanish_decimal("N/A") is None

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_spanish_decimal("abc")

    # F2 regression — dot-only strings are thousands separators (not English decimals)
    def test_dot_only_is_thousands(self):
        """'1.234' is Spanish 1234, not English 1.234."""
        assert parse_spanish_decimal("1.234") == Decimal("1234")

    def test_dot_only_large(self):
        """'10.500' → 10500 (ten thousand five hundred)."""
        assert parse_spanish_decimal("10.500") == Decimal("10500")

    def test_dot_only_round_thousand(self):
        """'1.000' → 1000 (one thousand, not one)."""
        assert parse_spanish_decimal("1.000") == Decimal("1000")

    def test_no_dot_no_comma(self):
        """Plain integer string is unchanged."""
        assert parse_spanish_decimal("100") == Decimal("100")

    def test_comma_decimal_preserved(self):
        """Comma-decimal with dot thousands is unchanged."""
        assert parse_spanish_decimal("1.234,56") == Decimal("1234.56")

    def test_comma_only_decimal(self):
        """Comma-only decimal string is parsed correctly."""
        assert parse_spanish_decimal("0,50") == Decimal("0.50")


class TestParseSpanishDate:
    def test_valid(self):
        assert parse_spanish_date("15/06/2024") == "2024-06-15"

    def test_single_digits(self):
        assert parse_spanish_date("01/01/2024") == "2024-01-01"

    def test_empty(self):
        assert parse_spanish_date("") is None

    def test_none(self):
        assert parse_spanish_date(None) is None

    # ISO pass-through — formerly raised ValueError (pre-fix regression anchor)
    def test_iso_passthrough(self):
        """Already-normalised ISO YYYY-MM-DD is accepted unchanged."""
        assert parse_spanish_date("2024-06-15") == "2024-06-15"

    def test_iso_passthrough_old_date(self):
        """Reproduces the production bug: '2016-07-18' must not raise."""
        assert parse_spanish_date("2016-07-18") == "2016-07-18"

    def test_spanish_normalises_to_iso(self):
        """Spanish 18/07/2016 normalises to ISO 2016-07-18 exactly once."""
        assert parse_spanish_date("18/07/2016") == "2016-07-18"

    def test_iso_idempotent(self):
        """Calling parse_spanish_date twice on the same value is safe."""
        first = parse_spanish_date("18/07/2016")
        second = parse_spanish_date(first)
        assert second == "2016-07-18"

    def test_iso_calendar_invalid_raises(self):
        """ISO with out-of-range day raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse date"):
            parse_spanish_date("2024-02-30")

    def test_spanish_calendar_invalid_raises(self):
        """Spanish with month 13 raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse date"):
            parse_spanish_date("01/13/2024")

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_spanish_date("not-a-date")

    def test_us_date_high_month_rejected(self):
        """US MM/DD/YYYY with day > 12 read as Spanish produces month > 12 → rejected."""
        with pytest.raises(ValueError):
            parse_spanish_date("07/18/2016")  # day=07, month=18 → invalid


class TestNormalizeCompanyName:
    def test_lowercase(self):
        assert normalize_company_name("Apple Inc.") == "apple inc."

    def test_accents_stripped(self):
        assert normalize_company_name("Telefónica") == "telefonica"

    def test_whitespace_collapsed(self):
        assert normalize_company_name("  Apple  Inc  ") == "apple inc"

    def test_empty(self):
        assert normalize_company_name("") == ""


class TestRowIdempotencyHash:
    def test_deterministic(self):
        h1 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        h2 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        assert h1 == h2

    def test_different_inputs(self):
        h1 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        h2 = row_idempotency_hash("XMAD:TEF", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        assert h1 != h2


# ---------------------------------------------------------------------------
# Dividends parser
# ---------------------------------------------------------------------------

DIVIDENDS_CSV_TAB = """\
Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\tImporte en Derechos\tRetención Origen\tRetención Destino
2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75
2024\tTelefónica\t20/06/2024\t50,00\t38,00\t0,00\t7,50\t4,50
"""

DIVIDENDS_CSV_SEMICOLON = """\
Año;Empresa;Fecha de cobro;Importe Bruto;Importe Neto;Importe en Derechos;Retención Origen;Retención Destino
2024;Apple Inc.;15/06/2024;100,00;73,31;0,00;12,94;13,75
"""

DIVIDENDS_CSV_COMMA = """\
Año,Empresa,Fecha de cobro,Importe Bruto,Importe Neto,Importe en Derechos,Retención Origen,Retención Destino
2024,Apple Inc.,15/06/2024,"100,00","73,31","0,00","12,94","13,75"
"""

DIVIDENDS_CSV_DERECHOS = """\
Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\tImporte en Derechos\tRetención Origen\tRetención Destino
2024\tSantander\t10/06/2024\t80,00\t60,00\t45,30\t12,00\t8,00
"""


class TestDividendsParser:
    def test_tab_delimiter(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert len(rows) == 2

    def test_semicolon_delimiter(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_SEMICOLON))
        assert len(rows) == 1

    def test_correct_amounts(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        first = rows[0]
        assert first["gross"] == Decimal("100.00")
        assert first["net"] == Decimal("73.31")
        assert first["wht_source"] == Decimal("12.94")
        assert first["wht_destination"] == Decimal("13.75")

    def test_correct_date(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["payment_date"] == "2024-06-15"

    def test_empresa_normalized(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[1]["empresa_normalized"] == "telefonica"

    def test_rights_amount_warning(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_DERECHOS))
        assert len(rows) == 1
        row = rows[0]
        assert row["derechos"] == Decimal("45.30")
        assert len(row["warnings"]) == 1
        assert row["warnings"][0]["type"] == "RIGHTS_AMOUNT"
        assert row["warnings"][0]["amount"] == "45.30"

    def test_no_rights_warning_when_zero(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        for row in rows:
            assert not any(w["type"] == "RIGHTS_AMOUNT" for w in row["warnings"])

    def test_row_index_zero_based(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["row_index"] == 0
        assert rows[1]["row_index"] == 1

    def test_source_row_preserved(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert "source_row" in rows[0]
        assert isinstance(rows[0]["source_row"], dict)

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_dividends(b"")

    def test_header_only_raises(self):
        header = _encode(
            "Año\tEmpresa\tFecha de cobro\tImporte Bruto\t"
            "Importe Neto\tImporte en Derechos\tRetención Origen\tRetención Destino\n"
        )
        with pytest.raises(ValueError):
            parse_dividends(header)

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_dividends(_encode("Col1\tCol2\tCol3\n1\t2\t3\n"))

    def test_year_parsed(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["year"] == 2024

    def test_spanish_thousands_in_amounts(self):
        csv = (
            "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
            "Importe en Derechos\tRetención Origen\tRetención Destino\n"
            "2024\tCo\t15/06/2024\t1.234,56\t900,00\t0,00\t100,00\t234,56\n"
        )
        rows = parse_dividends(_encode(csv))
        assert rows[0]["gross"] == Decimal("1234.56")


# ---------------------------------------------------------------------------
# Purchases parser
# ---------------------------------------------------------------------------

PURCHASES_CSV = """\
Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión
2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50
2024\tTelefónica\t15/03/2024\t3,80\t100\t380,00\t4,00
"""

PURCHASES_CSV_ZERO_COST = """\
Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión
2024\tTelefónica\t20/06/2024\t0\t50\t0,00\t0,00
"""


class TestPurchasesParser:
    def test_basic_parse(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert len(rows) == 2

    def test_amounts(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["price_per_share"] == Decimal("182.50")
        assert rows[0]["quantity"] == Decimal("10")
        assert rows[0]["total_cost"] == Decimal("1825.00")
        assert rows[0]["commission"] == Decimal("7.50")

    def test_date(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["purchase_date"] == "2024-01-10"

    def test_cost_basis_complete(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["cost_basis_status"] == "COMPLETE"
        assert not any(w["type"] == "ZERO_COST_ACQUISITION" for w in rows[0]["warnings"])

    def test_zero_cost_acquisition(self):
        rows = parse_purchases(_encode(PURCHASES_CSV_ZERO_COST))
        assert len(rows) == 1
        row = rows[0]
        assert row["cost_basis_status"] == "INCOMPLETE"
        assert row["price_per_share"] == Decimal("0")
        assert row["quantity"] == Decimal("50")
        assert len(row["warnings"]) == 1
        assert row["warnings"][0]["type"] == "ZERO_COST_ACQUISITION"

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_purchases(b"")

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_purchases(_encode("Col1\tCol2\n1\t2\n"))


# ---------------------------------------------------------------------------
# Sales parser
# ---------------------------------------------------------------------------

SALES_CSV = """\
Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta
2024\tApple Inc.\t20/06/2024\t5\t7,50\t1.050,00
2024\tTelefónica\t25/06/2024\t50\t4,00\t200,00
"""


class TestSalesParser:
    def test_basic_parse(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert len(rows) == 2

    def test_amounts(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["quantity"] == Decimal("5")
        assert rows[0]["commission"] == Decimal("7.50")
        assert rows[0]["total_proceeds"] == Decimal("1050.00")

    def test_date(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["sale_date"] == "2024-06-20"

    def test_empresa_normalized(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["empresa_normalized"] == "apple inc."

    def test_no_warnings_by_default(self):
        rows = parse_sales(_encode(SALES_CSV))
        for row in rows:
            assert row["warnings"] == []

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_sales(b"")

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_sales(_encode("Col1\tCol2\n1\t2\n"))


# ---------------------------------------------------------------------------
# Regression: ISO date pass-through across all three parsers
# ---------------------------------------------------------------------------
# Reproduces the production bug: user files that already contain ISO-formatted
# dates (e.g. after re-export or Excel normalisation) must not raise.

class TestIsoDatesRegressionPurchases:
    """Purchases: ISO YYYY-MM-DD in 'Fecha compra' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
        "2016\tSome Corp\t2016-07-18\t10,00\t100\t1.000,00\t5,00\n"
    )

    def test_iso_date_accepted(self):
        """'2016-07-18' must not raise 'Cannot parse date'."""
        rows = parse_purchases(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["purchase_date"] == "2016-07-18"

    def test_spanish_date_still_normalises(self):
        csv = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2016\tSome Corp\t18/07/2016\t10,00\t100\t1.000,00\t5,00\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["purchase_date"] == "2016-07-18"

    def test_iso_and_spanish_mixed(self):
        """A file with both formats in different rows parses cleanly."""
        csv = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2016\tCorp A\t18/07/2016\t10,00\t100\t1.000,00\t5,00\n"
            "2017\tCorp B\t2017-03-15\t20,00\t50\t1.000,00\t5,00\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["purchase_date"] == "2016-07-18"
        assert rows[1]["purchase_date"] == "2017-03-15"


class TestIsoDatesRegressionDividends:
    """Dividends: ISO YYYY-MM-DD in 'Fecha de cobro' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
        "Importe en Derechos\tRetención Origen\tRetención Destino\n"
        "2016\tSome Corp\t2016-07-18\t50,00\t38,00\t0,00\t7,50\t4,50\n"
    )

    def test_iso_date_accepted(self):
        rows = parse_dividends(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["payment_date"] == "2016-07-18"


class TestIsoDatesRegressionSales:
    """Sales: ISO YYYY-MM-DD in 'Fecha venta' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\n"
        "2016\tSome Corp\t2016-07-18\t50\t5,00\t600,00\n"
    )

    def test_iso_date_accepted(self):
        rows = parse_sales(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["sale_date"] == "2016-07-18"
