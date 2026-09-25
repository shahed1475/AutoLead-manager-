"""Email deliverability: DNS checks (SPF/DMARC/DKIM), bounce detection, a
per-sender daily limit, and auto-pause on a high bounce rate. All of it only
blocks or pauses sends — nothing here sends."""
import pytest

from backend.email_campaigns import bounces, deliverability as dv

from test_campaign_send_from import (  # noqa: F401 — shared fixtures
    PROSPECTS, _smtp_profile, enabled, stub_ai, stub_smtp,
)

pytestmark = pytest.mark.asyncio


def _resolver(records):
    def resolve(name, rtype):
        return records.get((name, rtype), [])
    return resolve


# ── DNS ──────────────────────────────────────────────────────────────────

def test_domain_fully_set_up():
    r = dv.check_domain("popupgenix.com", resolve=_resolver({
        ("popupgenix.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"],
        ("_dmarc.popupgenix.com", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:d@popupgenix.com"],
        ("google._domainkey.popupgenix.com", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
        ("popupgenix.com", "MX"): ["10 mx.google.com."],
    }))
    assert [r[k]["status"] for k in ("spf", "dmarc", "dkim", "mx")] == ["ok", "ok", "ok", "ok"]
    assert "google" in r["dkim"]["detail"]


def test_domain_missing_and_weak_records():
    r = dv.check_domain("shop.example", resolve=_resolver({
        ("shop.example", "TXT"): ["v=spf1 +all"],
        ("_dmarc.shop.example", "TXT"): ["v=DMARC1; p=none"],
    }))
    assert r["spf"]["status"] == "weak" and r["dmarc"]["status"] == "weak"
    assert r["dkim"]["status"] == "missing" and r["mx"]["status"] == "missing"
    assert r["advice"]


def test_dns_failure_is_unknown_not_missing():
    def boom(name, rtype):
        raise dv.LookupFailed("timeout")
    r = dv.check_domain("popupgenix.com", resolve=boom)
    assert {r[k]["status"] for k in ("spf", "dmarc", "dkim", "mx")} == {"unknown"}


def test_gmail_address_gets_custom_domain_advice():
    r = dv.check_domain("gmail.com", resolve=_resolver({}))
    assert r["shared_provider"] is True
    assert {r[k]["status"] for k in ("spf", "dkim", "dmarc")} == {"ok"}   # the provider's records, not the user's
    assert any("own domain" in a for a in r["advice"])


# ── Bounce parsing ───────────────────────────────────────────────────────

GMAIL_DSN = (
    "** Address not found **\n\nYour message wasn't delivered to ceo@northwind-dental.com "
    "because the address couldn't be found.\n\nFinal-Recipient: rfc822; ceo@northwind-dental.com\n"
    "Status: 5.1.1\nFrom: hello@popupgenix.com"
)


def test_parse_gmail_bounce():
    got = bounces.parse_bounce("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)",
                               GMAIL_DSN, own=("hello@popupgenix.com",))
    assert got == ["ceo@northwind-dental.com"]


def test_normal_reply_is_not_a_bounce():
    assert bounces.parse_bounce("ceo@northwind-dental.com", "Re: Idea", "Sounds good, call me.") is None


def test_soft_bounce_is_ignored():
    body = "Final-Recipient: rfc822; a@b.com\nStatus: 4.2.2 mailbox full, will retry"
    assert bounces.parse_bounce("postmaster@b.com", "Delivery delayed", body) is None


# ── Send loop ────────────────────────────────────────────────────────────

async def _campaign(db, pid, emails):
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    cid = (await svc.create_campaign({"name": "C", "sender_profile_id": pid}))["id"]
    csv = "email,company\n" + "\n".join(f"{e},Co{i}" for i, e in enumerate(emails)) + "\n"
    await svc.import_leads(cid, "p.csv", csv.encode())
    await svc.mark_ready(cid)
    return cid, svc


async def test_bounced_address_is_never_sent_again(enabled, stub_ai, stub_smtp):
    await enabled.record_bounce(PROSPECTS[0], "5.1.1 address not found")
    pid = await _smtp_profile(enabled)
    cid, svc = await _campaign(enabled, pid, PROSPECTS)
    await svc.start(cid, "r1", background=False)
    assert len(stub_smtp.calls) == 2
    lead = await enabled.get_email_campaign_lead(cid, f"{cid}::{PROSPECTS[0].lower()}")
    assert lead["status"] == "SEND_BLOCKED" and "bounced" in lead["failure_reason"]


async def test_sender_daily_limit_pauses_campaign(enabled, stub_ai, stub_smtp):
    pid = await _smtp_profile(enabled)
    await enabled.update_sender_profile(pid, {"daily_limit": 2})
    cid, svc = await _campaign(enabled, pid, PROSPECTS)
    await svc.start(cid, "r1", background=False)
    assert len(stub_smtp.calls) == 2
    assert (await enabled.get_email_campaign(cid))["status"] == "PAUSED"
    assert "sender_daily_limit" in [a["event"] for a in await svc.get_activity(cid)]


async def test_no_daily_limit_by_default(enabled, stub_ai, stub_smtp):
    pid = await _smtp_profile(enabled)
    cid, svc = await _campaign(enabled, pid, PROSPECTS)
    await svc.start(cid, "r1", background=False)
    assert len(stub_smtp.calls) == 3
    assert (await enabled.get_sender_profile(pid))["daily_limit"] == 0


async def test_high_bounce_rate_pauses_running_campaign(enabled):
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    emails = [f"p{i}@co{i}.com" for i in range(20)]
    cid, _ = await _campaign(enabled, None, emails)
    await enabled.update_email_campaign(cid, {"status": "RUNNING"})
    for e in emails:
        await enabled.update_email_campaign_lead(cid, f"{cid}::{e}", {"status": "SENT"})
    paused = await bounces.handle_bounce(["p0@co0.com"], "5.1.1")
    assert paused == []                     # 1 of 20 = 5%: not above the threshold yet
    paused = await bounces.handle_bounce(["p1@co1.com"], "5.1.1")
    assert cid in paused
    assert (await svc.get_campaign(cid))["status"] == "PAUSED"
    assert (await enabled.get_email_campaign_lead(cid, f"{cid}::p1@co1.com"))["status"] == "BOUNCED"
    assert await enabled.is_email_bounced("P1@CO1.COM")


async def test_reply_detector_records_bounce_and_skips_reply(clean_db, monkeypatch):
    from backend import reply_detector
    msg = {"from_email": "mailer-daemon@googlemail.com", "from_header": "Mail Delivery Subsystem",
           "subject": "Delivery Status Notification (Failure)", "body_text": GMAIL_DSN,
           "received_at": "Mon, 1 Jan 2026 00:00:00 +0000"}

    async def fake_to_thread(func, *args, **kwargs):
        return [msg]
    monkeypatch.setattr(reply_detector.asyncio, "to_thread", fake_to_thread)
    saved = await reply_detector.check_for_replies(
        {"imap_host": "h", "imap_username": "hello@popupgenix.com", "imap_password": "p"})
    assert saved == []
    assert await clean_db.is_email_bounced("ceo@northwind-dental.com")
    assert not await clean_db.is_email_bounced("hello@popupgenix.com")


async def test_sender_router_daily_limit_and_dns(enabled, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.main import app
    pid = await _smtp_profile(enabled)
    monkeypatch.setattr(dv, "_dns_resolve", lambda name, rtype: [])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.patch(f"/api/email-senders/{pid}", json={"daily_limit": 40})
        assert r.status_code == 200 and r.json()["daily_limit"] == 40
        assert (await c.patch(f"/api/email-senders/{pid}", json={"daily_limit": -1})).status_code == 422
        d = (await c.get(f"/api/email-senders/{pid}/deliverability")).json()
        assert d["domain"] == "popupgenix.com" and d["spf"]["status"] == "missing"
