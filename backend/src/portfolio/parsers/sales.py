"""Sales CSV parser.

Expected columns (6):
  Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta

Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

_REQUIRED_COLS = [
    "año",
    "empresa",
    "fecha venta",
    "acciones",
    "comisión",
    "total venta",
]


def _normalize_header(h: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", h)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


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
      - source_row: Dict[str, str]
      - warnings: List[Dict]

    Raises ValueError on parse failure.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    for i, expected in enumerate(_REQUIRED_COLS):
        if i >= len(normalized_headers):
            raise ValueError(
                f"Missing column at position {i+1}: expected '{expected}'"
            )
        actual = normalized_headers[i]
        normalized_expected = _normalize_header(expected)
        if actual != normalized_expected:
            raise ValueError(
                f"Column {i+1} mismatch: expected '{expected}', got '{actual}'"
            )

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        while len(row) < 6:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            sale_date = parse_spanish_date(row[2])
            quantity = parse_spanish_decimal(row[3]) or Decimal("0")
            commission = parse_spanish_decimal(row[4]) or Decimal("0")
            total_proceeds = parse_spanish_decimal(row[5]) or Decimal("0")
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        empresa_normalized = normalize_company_name(empresa_raw)

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "sale_date": sale_date,
            "quantity": quantity,
            "commission": commission,
            "total_proceeds": total_proceeds,
            "source_row": source_row,
            "warnings": [],
        })

    return results
