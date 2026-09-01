# Lead Search → Email Campaign Handoff (Phase 5B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user select leads on the Lead Search page and push them into a new Email Campaign in one action, reusing the existing `EmailCampaignService` and campaign-lead persistence.

**Architecture:** A new `EmailCampaignService.add_leads_from_db(campaign_id, lead_ids)` maps `leads` rows to campaign-lead rows via a pure helper and persists them with the existing `db.bulk_insert_email_campaign_leads` (`INSERT OR IGNORE` on `(campaign_id, lead_key)`). A new `POST /api/email-campaigns/from-search` endpoint creates a DRAFT campaign (`EmailCampaignService.create_campaign`, unchanged) then calls that method. The frontend adds row selection + an action bar + a small modal to `LeadSearch.jsx`. Nothing touches a send path, a transport, the TEST_MODE gate, or n8n.

**Tech Stack:** Python 3 / FastAPI / aiosqlite; pytest + pytest-asyncio + httpx `ASGITransport`; React 18 + Vite + `@tanstack/react-query` + `react-hot-toast` + `lucide-react`.

## Global Constraints

- SQLite only, single file. **No schema change, no migration, no new dependency in this phase.**
- No new message-sending path. The handoff must never call `email_sender.send_email`, `email_campaigns.senders.resolve_transport`, any transport, `assert_send_allowed`, or `email_campaigns.n8n_client`. Verified by a dedicated test.
- The created campaign lands `DRAFT` with `test_mode` truthy. Prepare / mark-ready / start stay user-driven on `/email-campaigns`. TEST_MODE and `safety.py` are untouched.
- The source `leads` row is never modified by the handoff.
- Request models for the email-campaigns router live **in `backend/routers/email_campaigns.py`** (that is where `CampaignCreate`, `CampaignPatch`, `StartBody` already are — not `backend/models.py`).
- `lead_ids` is hard-capped at 2000 per call. Endpoint is rate-limited `20/minute` (matches the `import_leads` precedent).
- Backend verification: `venv/Scripts/python.exe -m pytest -q` stays green (currently 677 passing + Phase 5B additions). Frontend: `cd frontend && npm run build` clean. No frontend test framework exists — the bar is a clean build plus a manual browser check.
- Reuse existing frontend primitives (`input` / `btn-primary` / `btn-secondary` classes, `react-hot-toast`, `lucide-react`, React Query). No redesign, no new npm package.
- Commit after every task with a `feat:` / `test:` message. Branch is `feat/email-campaigns` (already the working branch — do not create a new branch).

---

## File Structure

**Backend**

| File | Change | Responsibility |
|---|---|---|
| `backend/email_campaigns/service.py` | modify | add module-level `_lead_row_to_campaign_row(campaign_id, lead)` helper + `EmailCampaignService.add_leads_from_db(campaign_id, lead_ids)` |
| `backend/routers/email_campaigns.py` | modify | add `CampaignFromSearchRequest` model + `POST /from-search` route |
| `tests/test_campaign_from_search.py` | create | all Phase 5B backend tests |

**Frontend**

| File | Change | Responsibility |
|---|---|---|
| `frontend/src/api/client.js` | modify | `emailCampaignsApi.createFromSearch(payload)` |
| `frontend/src/components/lead-search/SendToCampaignModal.jsx` | create | the handoff modal (name + Send From + confirm + submit) |
| `frontend/src/pages/LeadSearch.jsx` | modify | selection state, checkbox column in `ResultsTable`, action bar, modal wiring |

---

## Task 1: `_lead_row_to_campaign_row` mapping helper

**Files:**
- Modify: `backend/email_campaigns/service.py` (add a module-level function near the other module-level helpers, after `_test_body`, before `class EmailCampaignService`)
- Test: `tests/test_campaign_from_search.py` (create)

**Interfaces:**
- Consumes: `backend.validators.is_valid_email` (already imported in `service.py` as `from ..validators import is_valid_email`)
- Produces: `_lead_row_to_campaign_row(campaign_id: int, lead: dict) -> dict` returning keys exactly:
  `lead_key, lead_id, email, first_name, last_name, company, raw_json, body_source, provided_body, status, status_detail`
  — the key set `db.bulk_insert_email_campaign_leads` reads. `raw_json` is a JSON **string** (so the bulk insert's `raw_json` pass-through branch is used, not its `raw`-dict branch which `json.dumps` without `default=str`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_campaign_from_search.py`:

```python
"""
test_campaign_from_search.py — Phase 5B: Lead Search -> Email Campaign handoff.

Selected global leads are pushed into a NEW campaign via
POST /api/email-campaigns/from-search. The campaign lands DRAFT; nothing is
prepared or sent; the source `leads` rows are untouched.
"""
import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


# ── Task 1: the mapping helper ────────────────────────────────────────────

async def test_valid_email_lead_maps_to_validated():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(7, {
        "id": 42, "business_name": "Acme Dental",
        "email": "Owner@Acme.com", "phone": "+15551234567",
        "website": "https://acme.com",
    })
    assert row["status"] == "VALIDATED"
    assert row["status_detail"] == ""
    assert row["lead_key"] == "7::owner@acme.com"
    assert row["lead_id"] == 42
    assert row["email"] == "owner@acme.com"
    assert row["company"] == "Acme Dental"
    assert row["first_name"] == "" and row["last_name"] == ""
    assert row["body_source"] == "ai" and row["provided_body"] is None


async def test_missing_email_lead_maps_to_missing_email():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(3, {
        "id": 9, "business_name": "No Email Co", "email": None, "phone": "5551110000",
    })
    assert row["status"] == "MISSING_EMAIL"
    assert row["lead_key"] == "3::lead::9"
    assert row["email"] is None


async def test_malformed_email_lead_maps_to_invalid_email():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(3, {
        "id": 9, "business_name": "Bad Co", "email": "not-an-email",
    })
    assert row["status"] == "INVALID_EMAIL"
    assert row["lead_key"] == "3::lead::9"


async def test_raw_json_preserves_original_fields_and_is_serialisable():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    lead = {
        "id": 1, "business_name": "Acme", "email": "a@acme.com",
        "phone": "555", "website": "https://acme.com", "source": "GOOGLE_MAPS",
        "score": 55, "created_at": datetime(2026, 9, 1, 12, 0, 0),
    }
    row = _lead_row_to_campaign_row(2, lead)
    parsed = json.loads(row["raw_json"])   # must not raise — default=str handles datetime
    assert parsed["business_name"] == "Acme"
    assert parsed["source"] == "GOOGLE_MAPS"
    assert parsed["score"] == 55
    assert parsed["_handoff"]["lead_id"] == 1
    assert parsed["_handoff"]["source"] == "lead_search"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q`
Expected: FAIL — `ImportError: cannot import name '_lead_row_to_campaign_row'`

- [ ] **Step 3: Add the helper**

In `backend/email_campaigns/service.py`, add these imports at the top of the module if not already present (`json` and `datetime` are not currently imported there):

```python
import json
from datetime import datetime, timezone
```

Then, immediately before `class EmailCampaignService:`, add:

```python
def _lead_row_to_campaign_row(campaign_id: int, lead: Dict[str, Any]) -> Dict[str, Any]:
    """Map one global `leads` row to a normalized `email_campaign_leads` row for
    the Lead Search handoff. Pure — no DB, no side effects. `raw_json` is a
    pre-serialised string so bulk_insert_email_campaign_leads passes it through
    verbatim (its dict branch would json.dumps without default=str)."""
    lead_id = lead["id"]
    email = (lead.get("email") or "").strip().lower() or None

    if email and is_valid_email(email):
        status, detail = "VALIDATED", ""
        lead_key = f"{campaign_id}::{email}"
    elif email:
        status, detail = "INVALID_EMAIL", "email format is invalid"
        lead_key = f"{campaign_id}::lead::{lead_id}"
    else:
        status, detail = "MISSING_EMAIL", "no email on the discovered lead"
        lead_key = f"{campaign_id}::lead::{lead_id}"

    raw = dict(lead)
    raw["_handoff"] = {
        "source": "lead_search",
        "lead_id": lead_id,
        "added_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }

    return {
        "lead_key": lead_key,
        "lead_id": lead_id,
        "email": email,
        "first_name": "",
        "last_name": "",
        "company": lead.get("business_name"),
        "raw_json": json.dumps(raw, default=str),
        "body_source": "ai",
        "provided_body": None,
        "status": status,
        "status_detail": detail,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/email_campaigns/service.py tests/test_campaign_from_search.py
git commit -m "feat: add _lead_row_to_campaign_row mapping helper for lead-search handoff"
```

---

## Task 2: `EmailCampaignService.add_leads_from_db`

**Files:**
- Modify: `backend/email_campaigns/service.py` — add a method to `EmailCampaignService` (place it right after `import_leads`, before `set_attachment`)
- Test: `tests/test_campaign_from_search.py` (append)

**Interfaces:**
- Consumes: `_lead_row_to_campaign_row` (Task 1); `db.get_leads_by_ids(lead_ids: list[int]) -> dict[int, dict]`; `db.bulk_insert_email_campaign_leads(campaign_id, rows) -> int`; `db.recount_email_campaign(campaign_id)`; `db.log_email_campaign_activity(campaign_id, event, detail)`; `self.get_campaign(campaign_id)` (raises `EmailCampaignError` "campaign … not found").
- Produces: `EmailCampaignService.add_leads_from_db(campaign_id: int, lead_ids: list[int]) -> dict` with keys
  `total_requested, added, skipped_existing, missing_email, invalid_email, not_found` (`not_found` is `list[int]`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_campaign_from_search.py`:

```python
# ── Task 2: add_leads_from_db ─────────────────────────────────────────────

def _svc():
    from backend.email_campaigns.service import get_email_campaign_service
    return get_email_campaign_service()


async def _make_lead(db, **over):
    data = {"business_name": "Biz", "email": None, "phone": None, "website": None}
    data.update(over)
    return await db.create_lead(data)


async def test_single_valid_lead_added(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme Dental", email="owner@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid])

    assert summary == {
        "total_requested": 1, "added": 1, "skipped_existing": 0,
        "missing_email": 0, "invalid_email": 0, "not_found": [],
    }
    rows = await db.get_email_campaign_leads(camp["id"])
    assert len(rows) == 1
    assert rows[0]["lead_id"] == lid
    assert rows[0]["status"] == "VALIDATED"
    assert rows[0]["company"] == "Acme Dental"
    fresh = await db.get_email_campaign(camp["id"])
    assert fresh["status"] == "DRAFT"


async def test_mixed_batch_statuses_and_counts(enabled):
    # NOTE: leads created via db.create_lead pass through _normalize_lead_fields,
    # which nulls a malformed email — so a DB-sourced lead is only ever
    # VALIDATED or MISSING_EMAIL. The INVALID_EMAIL branch of the helper is
    # covered by the pure unit test in the Task 1 block.
    db = enabled
    svc = _svc()
    good = await _make_lead(db, email="a@good.com")
    none = await _make_lead(db, email=None, phone="5550001111")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [good, none])

    assert summary["total_requested"] == 2
    assert summary["added"] == 2
    assert summary["missing_email"] == 1
    assert summary["invalid_email"] == 0
    by_status = {r["status"] for r in await db.get_email_campaign_leads(camp["id"])}
    assert by_status == {"VALIDATED", "MISSING_EMAIL"}


async def test_raw_json_and_lead_link_persisted(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme", email="a@acme.com",
                           website="https://acme.com", phone="+15551230000")
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])

    row = (await db.get_email_campaign_leads(camp["id"]))[0]
    assert row["lead_id"] == lid
    raw = json.loads(row["raw_json"])
    assert raw["business_name"] == "Acme"
    assert raw["website"] == "https://acme.com"
    assert raw["_handoff"]["lead_id"] == lid


async def test_source_lead_unchanged(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme", email="a@acme.com")
    before = await db.get_lead_by_id(lid)
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    after = await db.get_lead_by_id(lid)
    assert dict(after) == dict(before)


async def test_re_adding_same_lead_is_skipped_not_duplicated(enabled):
    # `skipped_existing` comes from bulk_insert_email_campaign_leads' INSERT OR
    # IGNORE on (campaign_id, lead_key). The from-search endpoint always creates
    # a fresh campaign so this is only reachable on a repeat add_leads_from_db
    # call for the same campaign (a future "add to existing" caller / a retry).
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="A", email="dupe@x.com")
    camp = await svc.create_campaign({"name": "H"})
    first = await svc.add_leads_from_db(camp["id"], [lid])
    second = await svc.add_leads_from_db(camp["id"], [lid])
    assert first["added"] == 1
    assert second["added"] == 0
    assert second["skipped_existing"] == 1
    assert len(await db.get_email_campaign_leads(camp["id"])) == 1


async def test_nonexistent_id_reported_not_fatal(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid, 999999])
    assert summary["added"] == 1
    assert summary["not_found"] == [999999]


async def test_duplicate_ids_deduped_in_total(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid, lid, lid])
    assert summary["total_requested"] == 1
    assert summary["added"] == 1


async def test_activity_row_written(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    events = [a["event"] for a in await db.get_email_campaign_activity(camp["id"])]
    assert "leads_added_from_search" in events


async def test_cannot_add_when_campaign_not_draft_or_ready(enabled):
    from backend.email_campaigns.service import EmailCampaignError
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    await db.update_email_campaign(camp["id"], {"status": "RUNNING"})
    with pytest.raises(EmailCampaignError):
        await svc.add_leads_from_db(camp["id"], [lid])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q -k "single_valid or mixed_batch or raw_json_and or source_lead or re_adding or nonexistent or duplicate_ids or activity_row or cannot_add"`
Expected: FAIL — `AttributeError: 'EmailCampaignService' object has no attribute 'add_leads_from_db'`

- [ ] **Step 3: Add the method**

In `backend/email_campaigns/service.py`, inside `class EmailCampaignService`, immediately after the `import_leads` method:

```python
    async def add_leads_from_db(self, campaign_id: int, lead_ids: List[int]) -> Dict[str, Any]:
        """Add existing global leads (Lead Search selection) to a DRAFT/READY
        campaign. Reuses the file-import persistence path. Never prepares,
        never sends, never touches the source `leads` rows."""
        camp = await self.get_campaign(campaign_id)
        if camp["status"] not in ("DRAFT", "READY"):
            raise EmailCampaignError(
                f"cannot add leads while campaign is {camp['status']} "
                f"(must be DRAFT or READY)"
            )

        ordered_ids = list(dict.fromkeys(int(x) for x in lead_ids))
        leads_map = await db.get_leads_by_ids(ordered_ids)

        not_found = [lid for lid in ordered_ids if lid not in leads_map]
        rows = [
            _lead_row_to_campaign_row(campaign_id, leads_map[lid])
            for lid in ordered_ids if lid in leads_map
        ]
        missing_email = sum(1 for r in rows if r["status"] == "MISSING_EMAIL")
        invalid_email = sum(1 for r in rows if r["status"] == "INVALID_EMAIL")

        inserted = await db.bulk_insert_email_campaign_leads(campaign_id, rows)
        await db.recount_email_campaign(campaign_id)

        summary = {
            "total_requested": len(ordered_ids),
            "added": inserted,
            "skipped_existing": len(rows) - inserted,
            "missing_email": missing_email,
            "invalid_email": invalid_email,
            "not_found": not_found,
        }
        await db.log_email_campaign_activity(
            campaign_id, "leads_added_from_search",
            f"requested={summary['total_requested']} added={inserted} "
            f"skipped_existing={summary['skipped_existing']} "
            f"missing_email={missing_email} invalid_email={invalid_email} "
            f"not_found={len(not_found)}",
        )
        return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/email_campaigns/service.py tests/test_campaign_from_search.py
git commit -m "feat: EmailCampaignService.add_leads_from_db — Lead Search handoff persistence"
```

---

## Task 3: `POST /api/email-campaigns/from-search` endpoint

**Files:**
- Modify: `backend/routers/email_campaigns.py` — add `CampaignFromSearchRequest` (in the `# ── request models ──` block, after `CampaignPatch`) + the route (in the `# ── CRUD ──` block, after `create_campaign`)
- Test: `tests/test_campaign_from_search.py` (append)

**Interfaces:**
- Consumes: `_svc.create_campaign(data: dict) -> dict` (returns the campaign dict incl. `id` and `name`); `_svc.add_leads_from_db(campaign_id, lead_ids) -> dict` (Task 2); `_handle(EmailCampaignError) -> HTTPException`; `limiter` (already imported).
- Produces: `POST /api/email-campaigns/from-search` → `201 {campaign_id, name, total_requested, added, skipped_existing, missing_email, invalid_email, not_found}`; `422` (Pydantic bounds on `name` / `lead_ids`); `404` (`create_campaign` "sender profile … not found" → `_handle`); `409` (`create_campaign` "… is not a valid email" for `reply_to` → `_handle`); `503` (feature flag off).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_campaign_from_search.py`:

```python
# ── Task 3: the endpoint ─────────────────────────────────────────────────

async def test_endpoint_creates_campaign_and_adds_leads(enabled):
    db = enabled
    a = await _make_lead(db, business_name="Acme", email="a@acme.com")
    b = await _make_lead(db, business_name="Beta", email=None, phone="5551112222")
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "Dental — CA", "lead_ids": [a, b]})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Dental — CA"
    assert body["added"] == 2
    assert body["missing_email"] == 1
    cid = body["campaign_id"]

    async with _client() as c:
        camp = (await c.get(f"/api/email-campaigns/{cid}")).json()
    assert camp["status"] == "DRAFT"
    assert camp["test_mode"] is True


async def test_empty_lead_ids_422(enabled):
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "X", "lead_ids": []})
    assert r.status_code == 422


async def test_too_many_lead_ids_422(enabled):
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "X", "lead_ids": list(range(1, 2002))})
    assert r.status_code == 422


async def test_empty_name_422(enabled):
    db = enabled
    lid = await _make_lead(db, email="a@acme.com")
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "", "lead_ids": [lid]})
    assert r.status_code == 422


async def test_sender_profile_persisted(enabled):
    from backend.secrets_crypto import encrypt
    db = enabled
    pid = await db.create_sender_profile({
        "name": "PG SMTP", "provider": "smtp", "transport": "smtp",
        "email_address": "hello@popupgenix.com", "status": "connected",
        "smtp_host": "mail.popupgenix.com", "smtp_port": 465, "smtp_security": "ssl",
        "smtp_username": "hello@popupgenix.com", "smtp_password_enc": encrypt("pw"),
    })
    lid = await _make_lead(db, email="a@acme.com")
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "X", "sender_profile_id": pid, "lead_ids": [lid]})
        assert r.status_code == 201
        cid = r.json()["campaign_id"]
        camp = (await c.get(f"/api/email-campaigns/{cid}")).json()
    assert camp["sender_profile_id"] == pid


async def test_bad_sender_profile_id_404(enabled):
    db = enabled
    lid = await _make_lead(db, email="a@acme.com")
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "X", "sender_profile_id": 999999, "lead_ids": [lid]})
    assert r.status_code == 404


async def test_feature_flag_off_503(clean_db):
    lid = await clean_db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "X", "lead_ids": [lid]})
    assert r.status_code == 503


async def test_handoff_never_touches_a_send_path(enabled, monkeypatch):
    from backend import email_sender
    from backend.email_campaigns import senders as senders_mod
    from backend.email_campaigns import n8n_client

    def _boom(*a, **k):
        raise AssertionError("a send/transport/n8n path was called during handoff")

    monkeypatch.setattr(email_sender, "send_email", _boom)
    monkeypatch.setattr(senders_mod, "resolve_transport", _boom)
    monkeypatch.setattr(n8n_client, "describe", _boom, raising=False)

    db = enabled
    lid = await _make_lead(db, business_name="Acme", email="a@acme.com")
    async with _client() as c:
        r = await c.post("/api/email-campaigns/from-search",
                         json={"name": "H", "lead_ids": [lid]})
    assert r.status_code == 201
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q -k "endpoint or _422 or sender_profile or _404 or flag_off or never_touches"`
Expected: FAIL — `404 Not Found` (route does not exist) on the POST calls

- [ ] **Step 3: Add the model and route**

In `backend/routers/email_campaigns.py`, after the `CampaignPatch` class:

```python
class CampaignFromSearchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sender_profile_id: Optional[int] = None
    reply_to: Optional[str] = Field(default=None, max_length=254)
    lead_ids: List[int] = Field(min_length=1, max_length=2000)
```

After the `create_campaign` route:

```python
@router.post("/from-search", dependencies=_gated, status_code=201)
@limiter.limit("20/minute")
async def create_campaign_from_search(request: Request, payload: CampaignFromSearchRequest):
    """Create a new DRAFT campaign from a Lead Search selection and add the
    selected global leads to it. Never prepares or sends — the campaign is
    driven normally from /email-campaigns afterwards."""
    try:
        camp = await _svc.create_campaign(
            payload.model_dump(exclude_none=True, exclude={"lead_ids"})
        )
        summary = await _svc.add_leads_from_db(camp["id"], payload.lead_ids)
        return {"campaign_id": camp["id"], "name": camp["name"], **summary}
    except EmailCampaignError as exc:
        raise _handle(exc)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full backend suite (regression gate)**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS — previous count + the new `test_campaign_from_search.py` tests, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/email_campaigns.py tests/test_campaign_from_search.py
git commit -m "feat: POST /api/email-campaigns/from-search — Lead Search campaign handoff endpoint"
```

---

## Task 4: Frontend API client + `SendToCampaignModal`

**Files:**
- Modify: `frontend/src/api/client.js` — add one method to `emailCampaignsApi`
- Create: `frontend/src/components/lead-search/SendToCampaignModal.jsx`

**Interfaces:**
- Consumes: `emailCampaignsApi.createFromSearch(payload) -> Promise<{campaign_id, name, added, missing_email, invalid_email, skipped_existing, not_found}>`; `emailSendersApi.list() -> Promise<{senders: [...]}>`.
- Produces: default export `SendToCampaignModal` with props `{ leadIds: number[], leads: object[], defaultName: string, onClose: () => void, onDone: () => void }`. Calls `onDone()` after a successful create (parent clears selection + closes). Calls `onClose()` on cancel / backdrop.

- [ ] **Step 1: Add the API client method**

In `frontend/src/api/client.js`, inside the `emailCampaignsApi` object, add after `n8nStatus`:

```js
  createFromSearch: (payload) =>
    api.post('/email-campaigns/from-search', payload).then((r) => r.data),
```

- [ ] **Step 2: Create the modal component**

Create `frontend/src/components/lead-search/SendToCampaignModal.jsx`:

```jsx
import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2, Send, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { emailCampaignsApi, emailSendersApi } from '../../api/client'

const LARGE_OP = 50

function isDisabledError(err) {
  return /not enabled|disabled|503/i.test(err?.message || '')
}

export default function SendToCampaignModal({ leadIds, leads, defaultName, onClose, onDone }) {
  const [name, setName] = useState(defaultName)
  const [senderId, setSenderId] = useState('')
  const [bigConfirm, setBigConfirm] = useState(false)

  const sendersQuery = useQuery({
    queryKey: ['email-senders'], queryFn: emailSendersApi.list, retry: false,
  })
  const senders = sendersQuery.data?.senders || []

  const noEmail = leads.filter((l) => !l.email).length

  const createMut = useMutation({
    mutationFn: () => emailCampaignsApi.createFromSearch({
      name: name.trim(),
      sender_profile_id: senderId ? Number(senderId) : undefined,
      lead_ids: leadIds,
    }),
    onSuccess: (data) => {
      toast.success((t) => (
        <span>
          Added {data.added} lead{data.added === 1 ? '' : 's'} to “{data.name}”.{' '}
          <Link to="/email-campaigns" className="text-brand-400 underline"
                onClick={() => toast.dismiss(t.id)}>Open →</Link>
        </span>
      ), { duration: 8000 })
      if (data.missing_email + data.invalid_email > 0) {
        toast(`${data.missing_email + data.invalid_email} lead(s) had no usable email — added but not emailable.`)
      }
      onDone()
    },
    onError: (err) => {
      toast.error(isDisabledError(err)
        ? 'Enable Email Campaigns first (Email Campaigns page → Enable).'
        : (err.message || 'Could not create campaign'))
    },
  })

  const tooBig = leadIds.length > LARGE_OP
  const canSubmit = name.trim().length > 0 && (!tooBig || bigConfirm) && !createMut.isPending

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
         onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-100">Send to Email Campaign</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300"><X size={16} /></button>
        </div>

        <p className="text-sm text-slate-400">
          {leadIds.length} lead{leadIds.length === 1 ? '' : 's'} selected
          {noEmail > 0 && <> · <span className="text-amber-400">{noEmail} with no email</span> (added but not emailed)</>}.
          A new campaign is created in <strong>TEST&nbsp;MODE</strong>; nothing is sent.
        </p>

        <label className="block">
          <span className="text-xs font-medium text-slate-400">Campaign name</span>
          <input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Dental clinics — California" />
        </label>

        <label className="block">
          <span className="text-xs font-medium text-slate-400">Send From</span>
          <select className="input mt-1" value={senderId} onChange={(e) => setSenderId(e.target.value)}>
            <option value="">Default / global SMTP</option>
            {senders.map((s) => (
              <option key={s.id} value={s.id} disabled={s.status !== 'connected'}>
                {s.name} — {s.email_address}{s.status !== 'connected' ? ` (${s.status})` : ''}
              </option>
            ))}
          </select>
        </label>

        {tooBig && (
          <label className="flex items-center gap-2 text-sm text-amber-300">
            <input type="checkbox" checked={bigConfirm} onChange={(e) => setBigConfirm(e.target.checked)} />
            Yes, add {leadIds.length} leads
          </label>
        )}

        <div className="flex gap-2 justify-end">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!canSubmit} onClick={() => createMut.mutate()}>
            {createMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            Create campaign
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: PASS — build completes, no import/syntax errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.js frontend/src/components/lead-search/SendToCampaignModal.jsx
git commit -m "feat: SendToCampaignModal + emailCampaignsApi.createFromSearch"
```

---

## Task 5: Selection + action bar in `LeadSearch.jsx`

**Files:**
- Modify: `frontend/src/pages/LeadSearch.jsx`

**Interfaces:**
- Consumes: `SendToCampaignModal` (Task 4).
- Produces: user-facing selection + handoff on `/lead-search`. No new exports.

- [ ] **Step 1: Add imports**

The current icon import line in `frontend/src/pages/LeadSearch.jsx` is:

```jsx
import { Search, Loader2, MapPin, Mail, Phone, Globe, XCircle } from 'lucide-react'
```

Change it to add the three new icons:

```jsx
import { Search, Loader2, MapPin, Mail, Phone, Globe, XCircle, ShieldCheck, FileText, Send as SendIcon } from 'lucide-react'
```

And add one component import below the existing imports:

```jsx
import SendToCampaignModal from '../components/lead-search/SendToCampaignModal'
```

- [ ] **Step 2: Replace `ResultsTable` with a selectable version**

Replace the whole `function ResultsTable({ leads }) { ... }` block with:

```jsx
function ResultsTable({ leads, selected, onToggle, onToggleAll }) {
  const allSelected = leads.length > 0 && leads.every((l) => selected.has(l.id))
  const someSelected = leads.some((l) => selected.has(l.id)) && !allSelected

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-[10px] font-bold uppercase tracking-widest text-slate-500">
              <th className="px-3 py-2.5 w-9">
                <input
                  type="checkbox"
                  aria-label="Select all"
                  checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected }}
                  onChange={onToggleAll}
                />
              </th>
              <th className="px-4 py-2.5">Business</th>
              <th className="px-4 py-2.5">Contact</th>
              <th className="px-4 py-2.5">Website</th>
              <th className="px-4 py-2.5">Score</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => (
              <tr key={lead.id} className="border-b border-slate-800/60 last:border-0">
                <td className="px-3 py-3">
                  <input
                    type="checkbox"
                    aria-label={`Select ${lead.business_name}`}
                    checked={selected.has(lead.id)}
                    onChange={() => onToggle(lead.id)}
                  />
                </td>
                <td className="px-4 py-3">
                  <p className="font-medium text-slate-200">{lead.business_name}</p>
                  <p className="text-xs text-slate-500">{lead.niche}{lead.city ? ` · ${lead.city}` : ''}</p>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 space-y-1">
                  {lead.email && (
                    <div className="flex items-center gap-1.5"><Mail size={11} />{lead.email}</div>
                  )}
                  {lead.phone && (
                    <div className="flex items-center gap-1.5"><Phone size={11} />{lead.phone}</div>
                  )}
                  {!lead.email && !lead.phone && <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {lead.website ? (
                    <a
                      href={lead.website} target="_blank" rel="noreferrer"
                      className="flex items-center gap-1.5 text-brand-400 hover:text-brand-300 truncate max-w-[220px]"
                    >
                      <Globe size={11} className="shrink-0" />
                      <span className="truncate">{lead.website}</span>
                    </a>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3">
                  {lead.score > 0 ? (
                    <ScoreBadge score={lead.score} label={lead.score_label} />
                  ) : (
                    <span className="text-[10px] text-slate-600 italic">Not scored yet</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Add an action bar component**

Add this component in `LeadSearch.jsx` (above `export default function LeadSearch`):

```jsx
function SelectionActionBar({ count, onClear, onSend }) {
  if (count === 0) return null
  return (
    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-brand-500/40 bg-slate-900/95 px-4 py-2.5 shadow-lg">
      <span className="text-xs font-semibold text-brand-300">{count} selected</span>
      <button className="btn-secondary text-xs" onClick={onClear}>Clear</button>
      <div className="flex-1" />
      <button className="btn-secondary text-xs opacity-50 cursor-not-allowed"
              disabled title="Coming in a later step">
        <ShieldCheck size={12} /> Verify Selected
      </button>
      <button className="btn-secondary text-xs opacity-50 cursor-not-allowed"
              disabled title="Coming in a later step">
        <FileText size={12} /> Generate Reports
      </button>
      <button className="btn-primary text-xs" onClick={onSend}>
        <SendIcon size={12} /> Send to Email Campaign
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Wire selection state into the page**

The component currently declares `const leads = resultsQuery.data?.leads || []` near the bottom, just before `return (`. **Move that single line up** to immediately after the `resultsQuery` `useQuery({...})` call, so the handlers below can close over it.

Then, immediately after that moved `const leads = ...` line, add:

```jsx
  const [selected, setSelected] = useState(() => new Set())
  const [showSendModal, setShowSendModal] = useState(false)

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  const toggleAll = () =>
    setSelected((prev) => {
      const ids = leads.map((l) => l.id)
      const allOn = ids.length > 0 && ids.every((id) => prev.has(id))
      return allOn ? new Set() : new Set(ids)
    })
  const clearSelection = () => setSelected(new Set())
```

In `handleSubmit`, immediately after the existing `setRunId(null)` line, add:

```jsx
    clearSelection()
```

- [ ] **Step 5: Render the action bar, selectable table, and modal**

Replace the results render block:

```jsx
      {leads.length > 0 && (
        <>
          <p className="text-xs text-slate-500 flex items-center gap-1.5">
            <MapPin size={11} /> Found {leads.length} lead{leads.length === 1 ? '' : 's'}
            {run && !TERMINAL_STATUSES.has(run.status) ? ' so far…' : ''}
          </p>
          <ResultsTable leads={leads} />
        </>
      )}
```

with:

```jsx
      {leads.length > 0 && (
        <>
          <SelectionActionBar
            count={selected.size}
            onClear={clearSelection}
            onSend={() => setShowSendModal(true)}
          />
          <p className="text-xs text-slate-500 flex items-center gap-1.5">
            <MapPin size={11} /> Found {leads.length} lead{leads.length === 1 ? '' : 's'}
            {run && !TERMINAL_STATUSES.has(run.status) ? ' so far…' : ''}
          </p>
          <ResultsTable
            leads={leads}
            selected={selected}
            onToggle={toggle}
            onToggleAll={toggleAll}
          />
        </>
      )}

      {showSendModal && (
        <SendToCampaignModal
          leadIds={[...selected]}
          leads={leads.filter((l) => selected.has(l.id))}
          defaultName={
            [query.trim(), location.trim()].filter(Boolean).join(' — ').slice(0, 120)
            || 'Lead Search campaign'
          }
          onClose={() => setShowSendModal(false)}
          onDone={() => { setShowSendModal(false); clearSelection() }}
        />
      )}
```

- [ ] **Step 6: Build**

Run: `cd frontend && npm run build`
Expected: PASS — clean build.

- [ ] **Step 7: Manual verification**

Start both servers:
```
venv/Scripts/python.exe run_server_8001.py
cd frontend && npm run dev
```
Then in the browser at `http://127.0.0.1:5173/lead-search`:
1. Run a Quick Search (e.g. "Dental clinics" / "Alabama", 10 leads).
2. Tick a few rows → action bar shows "N selected"; Verify / Generate Reports are greyed with a tooltip.
3. Header checkbox selects/clears all; partial selection shows the indeterminate state.
4. Click **Send to Email Campaign** → modal opens, name pre-filled "Dental clinics — Alabama", Send From lists "Default / global SMTP".
5. Create → success toast with "Open →"; selection clears.
6. Go to `/email-campaigns` → the new campaign is there, status **DRAFT**, lead count matches, no email sent.
7. Run a new search → the previous selection is cleared.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/LeadSearch.jsx
git commit -m "feat: lead selection + Send to Email Campaign action bar on Lead Search"
```

---

## Final verification (whole phase)

- [ ] `venv/Scripts/python.exe -m pytest -q` — green, 0 failures, count = prior baseline + `test_campaign_from_search.py`.
- [ ] `cd frontend && npm run build` — clean.
- [ ] Manual browser flow (Task 5 Step 7) passes end-to-end.
- [ ] `git log --oneline` shows one commit per task, all on `feat/email-campaigns`.
- [ ] Confirm no diff touched: `backend/email_sender.py`, `backend/email_campaigns/safety.py`, `backend/email_campaigns/senders.py`, `backend/scheduler.py`, any n8n file, `backend/database.py` schema, `backend/config.py`.

---

## Self-review notes (addressed)

- **Spec coverage:** §5 API contract → Task 3; §6 data mapping → Task 1; §7 frontend → Tasks 4–5; §8 edge cases → Task 2 + Task 3 tests (empty/oversized ids, not_found, duplicate email, bad sender, flag off); §9 security (rate limit, 2000 cap, `_gated`) → Task 3; §10 tests → Tasks 1–3 test blocks incl. the no-send guard.
- **Correction vs spec §12:** the request model lives in `routers/email_campaigns.py`, not `backend/models.py` — that file is where this router's other request models already are.
- **Correction vs spec §8:** a bad `sender_profile_id` surfaces as **404** (not 400) because `create_campaign` raises `"sender profile … not found"` and `_handle` maps "not found" → 404. Test asserts 404.
- **`skipped_existing`:** comes only from `bulk_insert_email_campaign_leads`'s `INSERT OR IGNORE` on `(campaign_id, lead_key)`. Because `leads` has a case-insensitive unique index on email and `create_lead` normalises malformed emails to `NULL`, two distinct global leads can't share an email — so for the `from-search` endpoint (fresh campaign every call) `skipped_existing` is effectively always 0. It is exercised by a repeat `add_leads_from_db` call in the Task 2 tests and kept in the contract for a future "add to an existing campaign" caller. Rows are not pre-de-duplicated. `total_requested` counts distinct `lead_ids`.
- **`INVALID_EMAIL` reachability:** the helper branch exists and is unit-tested (Task 1), but a lead fetched from the DB never carries a malformed email (`_normalize_lead_fields` nulls it), so the handoff produces only `VALIDATED` / `MISSING_EMAIL` in practice.
- **Type consistency:** `add_leads_from_db` return keys are identical across Task 2 definition, Task 2 tests, and Task 3 route spread (`**summary`). `_lead_row_to_campaign_row` output keys match `bulk_insert_email_campaign_leads`'s reader.
