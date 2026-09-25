# Real Revenue Implementation Plan

> Executed natively (superpowers:executing-plans + test-driven-development). Condensed format.

**Problem (verified):** Dashboard "Est. revenue" = replied leads × `avg_deal_value`, and that setting has no UI and defaults to **$500** — so 1 reply showed "$500" although nothing was earned or recorded. `backend/database.py::get_dashboard_stats`.

**Goal:** revenue on the dashboard is only money the owner recorded on won deals; pipeline shows deal values; plus the insights that real values make possible.

## Global Constraints
- No invented money: every figure is a sum of values the owner entered. Zero stays zero ("No won deals yet").
- Additive DB (`leads.deal_value REAL`, `leads.won_at TIMESTAMP`); `estimated_revenue` key kept for old clients but now equals revenue won.
- Stage moves still go through `set_lead_stage` (the one choke point) and keep the DO_NOT_CONTACT refusal.
- One currency per workspace (`revenue_currency`, default USD; BDT, AED, EUR, GBP, INR, SAR, CAD, AUD offered).

## Tasks
1. **Deal fields + API** — `PUT /api/leads/{id}/deal {deal_value|null}`; `POST /{id}/stage` accepts optional `deal_value`; moving to WON stamps `won_at`, moving out clears it. Tests: value saved/cleared, negative rejected, WON stamps and un-WON clears, DO_NOT_CONTACT still refused.
2. **Honest stats** — `get_dashboard_stats`: `revenue_won`, `deals_won`, `open_pipeline_value` (valued INTERESTED/MEETING/PROPOSAL deals), `open_deals`, `avg_won_deal`, `win_rate` (won ÷ decided), `currency`; `estimated_revenue = revenue_won`. Results report gains `revenue_won` per period (by `won_at`). `GET /api/stats/revenue`: revenue by lead source and the latest wins. Tests: 1 reply + no deals → 0; sums, averages, win rate, by-source.
3. **UI** — Dashboard "Revenue won" metric (deals + open pipeline in the detail line, links to Pipeline); Pipeline cards show value, columns show totals, dropping on Won asks for the value (modal, prefilled with the typical value), cards have "Add value"; Results gets "Revenue won"; Dashboard "Where revenue comes from" panel (by source); Settings "Revenue" card (currency + typical deal value).

## Review Focus
1. Existing DB with no values → every money figure 0, no errors.
2. Deal moved WON → PROPOSAL → WON: counted once, `won_at` updated.
3. Lost deals excluded from open pipeline; count toward win rate only.
4. Value 0 is a real value (free pilot) — counts as a won deal with 0 revenue.
5. Currency change re-labels figures; amounts aren't converted (stated in the UI).
