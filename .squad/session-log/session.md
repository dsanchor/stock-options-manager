
## 2026-07-09T22:30:49Z — DPS Markdown Overflow Fix (Scribe)

Committed fix to render DPS Insights structured markdown without horizontal scroll. Changes span prompt instruction clarification, marked() render options for line preservation, and CSS width/word-break constraints. Commit: `4697927a7012b83cb1bff4aa8f42c63ac07eadb9`.


## 2026-09-06T15:34Z — Danny: Portfolio Summary Cost-Basis Contract (Architecture)

**Context:** User requested that `current_invested_eur` subtract the acquisition cost of sold shares, not sale proceeds. Current formula `purchases − sale_proceeds` mixes incompatible concepts.

**Decision:** Proposed CMP (Coste Medio Ponderado / Moving Weighted Average) per security_id as cost-basis method. Not FIFO. Explicitly documented as non-fiscal.

**Key outputs:**
- New fields: `remaining_cost_basis_eur`, `cost_basis_sold_eur`, `total_purchase_outflow_eur`, `total_sale_proceeds_eur`, `rights_proceeds_eur`, `realized_result_eur`, `has_incomplete_cost_basis`
- `current_invested_eur` redefined as `remaining_cost_basis_eur` (breaking change, frontend updated in same PR)
- `avg_cost_basis_eur` now reflects CMP pool (changes with sells, not just buys)
- Backward-compat aliases: `total_purchases_eur`, `total_sales_eur`, `total_invested_eur` unchanged numerically
- DERECHOS sales: contribute to sale_proceeds and realized_result but never touch share pool or cost basis
- 15 acceptance test scenarios defined (S1–S15)

**File:** `.squad/decisions/inbox/danny-portfolio-summary-cost-basis.md` — PROPOSED, awaiting approval.

## 2026-09-06T15:38Z — Danny: Corrección → FIFO en lugar de CMP

**Cambio:** Usuario pidió FIFO para el precio medio. Contrato actualizado: método FIFO (First In, First Out) con cola de lotes `(remaining_qty, cost_per_share)` en lugar de media ponderada móvil. avg_cost_basis_eur = media ponderada de lotes FIFO restantes (no promedio histórico). Test S4 y S12 recalculados con FIFO. Añadido S16 (tres compras cruzando lotes).
