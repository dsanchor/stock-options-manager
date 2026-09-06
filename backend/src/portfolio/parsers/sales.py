"""Sales CSV parser.

Expected columns (6):
  Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta

Two 7-column variants are supported — Tipo may appear in either position:

  A) Tipo between Fecha venta and Acciones (user sample layout):
     Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta

  B) Tipo appended after Total Venta (design-doc layout):
     Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta | Tipo

Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List

from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

# 6-column format (legacy): no Tipo column, all sales default to ACCIONES
_REQUIRED_COLS_6 = [
    "año",
    "empresa",
    "fecha venta",
    "acciones",
    "comisión",
    "total venta",
]

# 7-column variant A: Tipo between Fecha venta and Acciones
_REQUIRED_COLS_7A = [
    "año",
    "empresa",
    "fecha venta",
    "tipo",
    "acciones",
    "comisión",
    "total venta",
]

# 7-column variant B: Tipo appended after Total Venta
_REQUIRED_COLS_7B = [
    "año",
    "empresa",
    "fecha venta",
    "acciones",
    "comisión",
    "total venta",
    "tipo",
]

_VALID_SALES_TYPES = {"ACCIONES", "DERECHOS"}


def _normalize_header(h: str) -> str:
    nfkd = unicodedata.normalize("NFKD", h)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _normalize_sales_type(raw: str) -> str:
    """Normalize a Tipo cell to 'ACCIONES' or 'DERECHOS'.

    - Empty / whitespace → 'ACCIONES' (fail-safe default)
    - Case-, whitespace-, and accent-insensitive match
    - Non-empty, unrecognized value → raises ValueError
    """
    stripped = raw.strip()
    if not stripped:
        return "ACCIONES"
    nfkd = unicodedata.normalize("NFKD", stripped)
    normalized = "".join(c for c in nfkd if not unicodedata.combining(c)).upper()
    if normalized in _VALID_SALES_TYPES:
        return normalized
    raise ValueError(
        f"Invalid Tipo value '{raw}'; must be 'Acciones' or 'Derechos'"
    )


def parse_sales(content: bytes) -> List[Dict[str, Any]]:
    """Parse sales CSV content.

    Returns a list of row dicts, each containing:
      - row_index: int (0-based, excludes header)
      - year: Optional[int]
      - empresa_raw: str
      - empresa_normalized: str
      - sale_date: Optional[str] — ISO YYYY-MM-DD
      - quantity: Decimal  (shares sold)
      - commission: Decimal
      - total_proceeds: Decimal
      - sales_type: str  — 'ACCIONES' or 'DERECHOS'
      - sales_type_raw: str  — original Tipo cell value
      - source_row: Dict[str, str]
      - warnings: List[Dict]

    Raises ValueError on parse failure or invalid Tipo value.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    # Detect format variant by inspecting header positions.
    #   7A: Tipo at index 3 (between Fecha venta and Acciones)
    #   7B: Tipo at index 6 (appended after Total Venta)
    #    6: no Tipo column
    n = len(normalized_headers)
    if n >= 4 and normalized_headers[3] == "tipo":
        variant = "7A"
        expected_cols = _REQUIRED_COLS_7A
        min_cols = 7
    elif n >= 7 and normalized_headers[6] == "tipo":
        variant = "7B"
        expected_cols = _REQUIRED_COLS_7B
        min_cols = 7
    else:
        variant = "6"
        expected_cols = _REQUIRED_COLS_6
        min_cols = 6

    for i, expected in enumerate(expected_cols):
        if i >= len(normalized_headers):
            raise ValueError(
                f"Missing column at position {i+1}: expected '{expected}'"
            )
        actual = normalized_headers[i]
        if actual != _normalize_header(expected):
            raise ValueError(
                f"Column {i+1} mismatch: expected '{expected}', got '{actual}'"
            )

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        while len(row) < min_cols:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            sale_date = parse_spanish_date(row[2])

            if variant == "7A":
                sales_type_raw = row[3]
                try:
                    sales_type = _normalize_sales_type(sales_type_raw)
                except ValueError as exc:
                    raise ValueError(f"Row {row_index + 2}: {exc}") from exc
                quantity = parse_spanish_decimal(row[4]) or Decimal("0")
                commission = parse_spanish_decimal(row[5]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[6]) or Decimal("0")
            elif variant == "7B":
                sales_type_raw = row[6]
                try:
                    sales_type = _normalize_sales_type(sales_type_raw)
                except ValueError as exc:
                    raise ValueError(f"Row {row_index + 2}: {exc}") from exc
                quantity = parse_spanish_decimal(row[3]) or Decimal("0")
                commission = parse_spanish_decimal(row[4]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[5]) or Decimal("0")
            else:
                sales_type_raw = ""
                sales_type = "ACCIONES"
                quantity = parse_spanish_decimal(row[3]) or Decimal("0")
                commission = parse_spanish_decimal(row[4]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[5]) or Decimal("0")
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        empresa_normalized = normalize_company_name(empresa_raw)

        row_warnings: List[Dict[str, Any]] = []
        if sales_type == "DERECHOS" and quantity > Decimal("0"):
            row_warnings.append({
                "type": "DERECHOS_WITH_QUANTITY",
                "row_index": row_index,
                "message": (
                    "Rights sale with share quantity > 0. "
                    "Rights sales do not affect share count; please verify."
                ),
            })
        elif sales_type == "ACCIONES" and quantity == Decimal("0"):
            row_warnings.append({
                "type": "ACCIONES_ZERO_QUANTITY",
                "row_index": row_index,
                "message": (
                    "Share sale with zero quantity. "
                    "Verify this is not a rights transaction."
                ),
            })

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "sale_date": sale_date,
            "quantity": quantity,
            "commission": commission,
            "total_proceeds": total_proceeds,
            "sales_type": sales_type,
            "sales_type_raw": sales_type_raw,
            "source_row": source_row,
            "warnings": row_warnings,
        })

    return results
