"""Dividends CSV parser.

Expected columns (first 8):
  Año | Empresa | Fecha de cobro | Importe Bruto | Importe Neto |
  Importe en Derechos | Retención Origen | Retención Destino

Bilingual: Spanish or English headers are both accepted (Amendment G).
Additional columns beyond col 8 are preserved as `extra_cols`.

Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

# Positional alias map for dividend CSV headers (Amendment G §G.4.4).
_DIVIDENDS_HEADER_ALIASES: Dict[int, Set[str]] = {
    0: {"ano", "year"},
    1: {"empresa", "company"},
    2: {"fecha de cobro", "fecha cobro", "payment date", "date"},
    3: {"importe bruto", "gross amount", "gross"},
    4: {"importe neto", "net amount", "net"},
    5: {"importe en derechos", "rights amount", "scrip amount"},
    6: {"retencion origen", "source withholding", "withholding source", "wht source"},
    7: {"retencion destino", "destination withholding", "withholding destination", "wht destination", "wht dest"},
}


def _normalize_header(h: str) -> str:
    """NFKD → strip combining marks → lowercase → collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", h)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return " ".join(stripped.split())


def parse_dividends(content: bytes) -> List[Dict[str, Any]]:
    """Parse dividends CSV content.

    Returns a list of row dicts, each containing:
      - row_index: int (0-based, excludes header)
      - year: Optional[int]
      - empresa_raw: str
      - empresa_normalized: str
      - payment_date: Optional[str] — ISO YYYY-MM-DD
      - gross: Decimal
      - net: Decimal
      - derechos: Decimal
      - wht_source: Decimal
      - wht_destination: Decimal
      - extra_cols: List[str]
      - source_row: Dict[str, str] — raw cell values by header
      - warnings: List[Dict] — RIGHTS_AMOUNT if derechos > 0

    Raises ValueError on parse failure.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    for pos, aliases in _DIVIDENDS_HEADER_ALIASES.items():
        if pos >= len(normalized_headers):
            raise ValueError(
                f"Missing column at position {pos + 1}: expected one of {sorted(aliases)}"
            )
        actual = normalized_headers[pos]
        if actual not in aliases:
            raise ValueError(
                f"Column {pos + 1}: unrecognized header {header_row[pos]!r}. "
                f"Expected one of: {', '.join(sorted(aliases))}"
            )

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        # Pad to at least 8 cells
        while len(row) < 8:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            payment_date = parse_spanish_date(row[2])
            gross = parse_spanish_decimal(row[3]) or Decimal("0")
            net = parse_spanish_decimal(row[4]) or Decimal("0")
            derechos = parse_spanish_decimal(row[5]) or Decimal("0")
            wht_source = parse_spanish_decimal(row[6]) or Decimal("0")
            wht_destination = parse_spanish_decimal(row[7]) or Decimal("0")
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        extra_cols = row[8:] if len(row) > 8 else []
        empresa_normalized = normalize_company_name(empresa_raw)

        warnings: List[Dict[str, Any]] = []
        if derechos > Decimal("0"):
            warnings.append({
                "type": "RIGHTS_AMOUNT",
                "row_index": row_index,
                "company": empresa_raw,
                "amount": str(derechos),
                "message": (
                    f"Row {row_index}: Importe en Derechos = {derechos} — "
                    "rights/scrip amount present; source fact preserved. "
                    "Cost basis incomplete pending Phase 2 corporate-action reconciliation."
                ),
            })

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "payment_date": payment_date,
            "gross": gross,
            "net": net,
            "derechos": derechos,
            "wht_source": wht_source,
            "wht_destination": wht_destination,
            "extra_cols": extra_cols,
            "source_row": source_row,
            "warnings": warnings,
        })

    return results
