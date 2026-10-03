# Known Defects

Source: [CURRENT_STATE.md](CURRENT_STATE.md) audit, 2026-10-03.

Defects that change strategy, risk or backtest **outputs** stay in place during Phase 1. The golden-master suite captures them, and each one is fixed in Phase 2 as a separate commit that updates the golden fixtures with justification. Safety and security defects that do not change outputs are fixed in Phase 1.

| ID | Defect | Status |
|---|---|---|
| D-001 | Paper broker never debits cash on entry but credits notional + P&L on exit, so cash and NAV inflate and position sizes drift upward | Open — Phase 2 |
| D-002 | Calibration outcomes are recorded against a constant 0.65, not the forecast; the Brier score is meaningless | Open — Phase 2 |
| D-003 | Calibration map is hard-coded, not empirical | Open — Phase 2 (v1 keeps it for parity; Kelly is disabled for uncalibrated strategies) |
| D-004 | Kelly payoff `b = 0.75` assumes ₹54.26 friction; the code computes ₹21.34 | Open — Phase 2 |
| D-005 | Docs claimed flat ₹20 + ₹20 brokerage; the code uses `min(₹20, 0.03%)` | **Docs corrected** (Phase 1). Which model is right for the operator's broker is still to be decided |
| D-006 | Max drawdown (6%) not enforced; daily loss ignores unrealized P&L and never resets | Open — Phase 2. The 6% limit is configured |
| D-007 | Kill-switch lockfile was cwd-relative; a write failure was swallowed (fail-open) | **Fixed** (Phase 1, task 6) |
| D-008 | Circular synthetic backtest presented as a passing validation gate | **Relabelled** "NOT EVIDENCE" (Phase 1); replaced in Phase 3 |
| D-009 | Positions keyed by symbol only; no strategy ownership; long-only | Open — Phase 3/7 |
| D-010 | Velocity z-score records every tweet at the same timestamp; "verified ratio" is synthesised from the duplication rate | Open — Phase 2. Real timestamps come from the social recorder |
| D-011 | Rapid bot toggling could start duplicate worker threads | **Fixed** (Phase 1, task 7) |
| D-012 | All state is in memory; a restart loses positions and history | Open — Phase 6/7 (SQLite) |
| S-001 | Unauthenticated mutating API on 0.0.0.0 | **Fixed** (Phase 1, task 7) |
| S-002 | Wildcard CORS allowed any website to call the API | **Fixed** (Phase 1, task 7) |
| S-003 | Stored XSS through symbol and log strings | **Fixed** (Phase 1, task 7) |
| S-004 | Kill switch failed open | **Fixed** (Phase 1, task 6) |
| S-005 | Tailwind loaded from a runtime CDN | **Fixed**: vendored (Phase 1, task 7) |
| S-006 | No secrets policy or `.env.example` | **Fixed** (Phase 1, task 10) |
| S-007 | Social-text isolation is informal | Open — formalised with the agent runtime (Phase 9); raw text is quarantined by the recorder |
