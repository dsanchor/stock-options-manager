### 2026-09-06: Make batch reassignment reason optional
**By:** Copilot (via Copilot)
**What:** Batch account reassignment must allow an empty reason in both frontend and backend. When omitted, the server records a standard internal audit reason instead of rejecting the request. This change applies to batch reassignment; individual reassignment keeps its existing validation unless separately changed.
**Why:** The UI labels the batch reason as optional, but current validation blocks submission without it.
