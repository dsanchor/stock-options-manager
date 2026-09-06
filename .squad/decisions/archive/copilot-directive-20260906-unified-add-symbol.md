### 2026-09-06: Unify Add symbol and Security creation
**By:** Copilot (via Copilot)
**What:** Use one user experience only: Add symbol is also the SecurityMaster creation/selection flow. Creating or adding a symbol must create/ensure its symbol_config with every agent, alert, notification, and automation disabled initially. Do not expose a separate Add security action.
**Why:** User explicitly wants Symbol and Security creation to be the same operation and experience.
