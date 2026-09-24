"""WhatsApp Campaigns: pacing, opt-outs, automatic replies and their guardrails,
the n8n hooks' secret, and the single send path — with the WhatsApp engine,
the AI and sending all faked (nothing real is sent)."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db, whatsapp_sender
from backend.whatsapp import engine, service

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def wa(clean_db, monkeypatch, tmp_path):
    """Always-on hours, no pauses, a fake WhatsApp and a fake AI."""
    cfg = tmp_path / "wa"
    cfg.mkdir()
    (cfg / "secrets.env").write_text("WAHA_API_KEY=k\nHOM_WA_SECRET=s3cret\n")
    monkeypatch.setenv("HOM_WA_CONFIG_DIR", str(cfg))
    await service.save_settings({"wa_hours_start": 0, "wa_hours_end": 24, "wa_min_gap": 30, "wa_max_gap": 30,
                                 "wa_reply_delay_min": 0, "wa_reply_delay_max": 0})
    sent = []

    async def fake_send(phone, text, config):
        sent.append((phone, text))
        return True

    async def ready():
        return True

    async def fake_draft(lead_id, chat):
        return "Thanks for getting back to me! Would a short call on Thursday work?"
    monkeypatch.setattr(whatsapp_sender, "send_whatsapp", fake_send)
    monkeypatch.setattr(engine, "ready", ready)
    monkeypatch.setattr(service, "draft_reply", fake_draft)
    return sent


async def _lead(name, phone, status="PENDING", label="HOT", **extra):
    lid = await db.create_lead({"business_name": name, "phone": phone, "city": "Dubai", "niche": "dentist", **extra})
    await db.update_lead(lid, {"status": status, "score_label": label, "score": 80})
    return lid


def _event(phone_digits, body, mid="m1", **payload):
    return {"event": "message", "payload": {"from": f"{phone_digits}@c.us", "body": body, "fromMe": False,
                                            "id": mid, **payload}}


async def _ready_for_next():
    await db.upsert_setting("wa_next_send_at", "")


# ── Campaigns & pacing ──────────────────────────────────────────────────

async def test_campaign_sends_one_message_per_tick_with_a_pause(wa):
    a = await _lead("Pearl Dental", "+971 50 111 2233")
    b = await _lead("Smile Studio", "+971501112244")
    c = await service.create_campaign("Dubai dentists", "Hi {business_name} — quick question about bookings.", False, [a, b])
    assert c["total"] == 2 and c["status"] == "DRAFT"
    assert (await service.tick())["reason"] == "Nothing to send"          # not started yet
    await service.set_campaign_status(c["id"], "start")
    r = await service.tick()
    assert r["sent"] is True and wa == [("+971501112233", "Hi Pearl Dental — quick question about bookings.")]
    assert (await service.tick())["reason"].startswith("Next message in")   # the pause holds the next one
    await _ready_for_next()
    await service.tick()
    assert len(wa) == 2
    await _ready_for_next()
    await service.tick()                                                   # nothing left → campaign done
    assert (await service.get_campaign(c["id"]))["status"] == "DONE"
    lead = await db.portal_fetchrow("SELECT status FROM leads WHERE id = ?", a)
    assert lead["status"] == "SENT"


async def test_daily_limit_and_hours(wa):
    ids = [await _lead(f"Clinic {i}", f"+97150111000{i}") for i in range(3)]
    c = await service.create_campaign("Batch", "Hello {business_name}, a quick idea for you.", False, ids)
    await service.set_campaign_status(c["id"], "start")
    await service.save_settings({"wa_daily_limit": 1})
    await service.tick()
    await _ready_for_next()
    assert (await service.tick())["reason"].startswith("Daily limit reached")
    await service.save_settings({"wa_daily_limit": 30, "wa_hours_start": 0, "wa_hours_end": 1})
    from datetime import datetime
    if datetime.now().hour >= 1:
        assert (await service.tick())["reason"].startswith("Outside sending hours")
    assert len(wa) == 1


async def test_opted_out_lead_is_skipped_right_before_sending(wa):
    a = await _lead("Pearl Dental", "+971501112233")
    c = await service.create_campaign("C", "Hello {business_name}, a quick idea for you.", False, [a])
    await service.set_campaign_status(c["id"], "start")
    await db.update_lead(a, {"status": "DO_NOT_CONTACT"})          # opted out after the campaign was made
    r = await service.tick()
    assert r["sent"] is False and "opted out" in r["reason"] and wa == []


async def test_audience_rules(wa):
    await _lead("Has phone", "+971501112233")
    await _lead("Duplicate number", "0501112233")                  # same number, no country code? different digits → kept
    await _lead("No phone", None)
    await _lead("Opted out", "+971502223344", status="DO_NOT_CONTACT")
    await _lead("In talks", "+971503334455", status="MEETING")
    await _lead("Cold", "+971504445566", label="COLD")
    names = [l["business_name"] for l in await service.audience(labels=["HOT"])]
    assert "Has phone" in names and "No phone" not in names and "Opted out" not in names
    assert "In talks" not in names and "Cold" not in names
    drafts = await service.audience(need_ai_draft=True)
    assert drafts == []                                            # nobody has an approved WhatsApp draft yet


async def test_uses_each_leads_approved_ai_draft(wa):
    a = await _lead("Pearl Dental", "+971501112233", ai_whatsapp_msg="Hi Dr. Amira — noticed you don't offer online booking yet…")
    b = await _lead("No Draft Clinic", "+971501112244")
    c = await service.create_campaign("Drafts", None, True, [a, b])
    await service.set_campaign_status(c["id"], "start")
    await service.tick()
    await _ready_for_next()
    await service.tick()
    assert wa == [("+971501112233", "Hi Dr. Amira — noticed you don't offer online booking yet…")]
    counts = (await service.get_campaign(c["id"]))["counts"]
    assert counts == {"SENT": 1, "SKIPPED": 1}


def test_template_rendering():
    text = service.render("Hi {first_name}, love what {business_name} does in {city}. {unknown}",
                          {"business_name": "Pearl Dental", "city": "Dubai", "decision_maker": "Amira Hassan"})
    assert text == "Hi Amira, love what Pearl Dental does in Dubai. {unknown}"


# ── Incoming messages & automatic replies ───────────────────────────────

async def _contacted(wa, name="Pearl Dental", phone="+971501112233"):
    lid = await _lead(name, phone)
    c = await service.create_campaign("C", "Hello {business_name}, a quick idea for you.", False, [lid])
    await service.set_campaign_status(c["id"], "start")
    await service.tick()
    return lid


async def test_reply_is_detected_and_answered_automatically(wa):
    lid = await _contacted(wa)
    r = await service.handle_event(_event("971501112233", "Hi, yes tell me more"), reply_now=True)
    assert r["auto_reply"] == "sent"
    assert wa[-1] == ("+971501112233", "Thanks for getting back to me! Would a short call on Thursday work?")
    lead = await db.portal_fetchrow("SELECT status FROM leads WHERE id = ?", lid)
    assert lead["status"] == "REPLIED"
    convo = await service.conversation(lid)
    assert [m["direction"] for m in convo] == ["OUT", "IN", "OUT"] and convo[-1]["source"] == "auto_reply"
    kinds = [a["kind"] for a in await service.activity()]
    assert "auto_reply" in kinds and "received" in kinds


async def test_opt_out_is_never_answered(wa):
    lid = await _contacted(wa)
    r = await service.handle_event(_event("971501112233", "Please stop messaging me, unsubscribe"), reply_now=True)
    assert r.get("opt_out") is True and len(wa) == 1                     # only the campaign message
    lead = await db.portal_fetchrow("SELECT status FROM leads WHERE id = ?", lid)
    assert lead["status"] == "DO_NOT_CONTACT"


async def test_everyone_gets_an_answer_and_new_contacts_become_leads(wa):
    import time
    r = await service.handle_event(_event("971501234567", "Hi, do you build websites?", mid="n1",
                                          notifyName="Omar", timestamp=int(time.time())), reply_now=True)
    assert r["auto_reply"] == "sent" and wa[-1][0] == "+971501234567"
    lead = await db.portal_fetchrow("SELECT * FROM leads WHERE phone = ?", "+971501234567")
    assert lead["business_name"] == "WhatsApp · Omar" and lead["source"] == "WHATSAPP" and lead["status"] == "REPLIED"
    # a second message from them doesn't create another lead
    await service.handle_event(_event("971501234567", "Price?", mid="n2"), reply_now=True)
    assert (await db.portal_fetchrow("SELECT count(*) AS n FROM leads WHERE phone = ?", "+971501234567"))["n"] == 1


async def test_never_groups_own_messages_or_old_messages(wa):
    import time
    assert (await service.handle_event({"event": "message", "payload": {"from": "1203630@g.us", "body": "group hi"}}))["handled"] is False
    assert (await service.handle_event(_event("971501112233", "me", fromMe=True)))["handled"] is False
    old = await service.handle_event(_event("971505556677", "sent hours ago", mid="o1",
                                            timestamp=int(time.time()) - 3 * 3600), reply_now=True)
    assert old["auto_reply"] == "too old" and wa == []


async def test_leads_only_mode_ignores_strangers(wa):
    await service.save_settings({"wa_reply_scope": "leads"})
    await _lead("Never messaged", "+971509998877")
    assert (await service.handle_event(_event("971501234567", "hello?"), reply_now=True))["lead"] is None
    r = await service.handle_event(_event("971509998877", "who is this?", mid="m9"), reply_now=True)
    assert r["auto_reply"] == "not contacted" and wa == []
    with pytest.raises(service.WhatsAppError):
        await service.save_settings({"wa_reply_scope": "groups"})


async def test_per_chat_limit_duplicates_and_switch_off(wa):
    await service.save_settings({"wa_auto_reply_per_chat": 1})
    await _contacted(wa)
    assert (await service.handle_event(_event("971501112233", "tell me more", mid="a"), reply_now=True))["auto_reply"] == "sent"
    assert (await service.handle_event(_event("971501112233", "and pricing?", mid="b"), reply_now=True))["auto_reply"] == "limit"
    assert (await service.handle_event(_event("971501112233", "and pricing?", mid="b"), reply_now=True)).get("duplicate")
    await service.save_settings({"wa_auto_reply": False, "wa_auto_reply_per_chat": 5})
    assert (await service.handle_event(_event("971501112233", "hello again", mid="c"), reply_now=True))["auto_reply"] == "off"


async def test_reply_cancelled_if_lead_opts_out_meanwhile(wa):
    lid = await _contacted(wa)
    msg = service.parse_event(_event("971501112233", "ok", mid="x"))
    await service._record(lid, msg["chat"], "IN", "ok", "inbound", "x")
    await db.update_lead(lid, {"status": "DO_NOT_CONTACT"})
    assert await service._auto_reply(lid, msg, await service.get_settings(), send=whatsapp_sender.send_whatsapp, delay=0) == "blocked"


def test_first_reply_greets_later_replies_continue():
    lead = {"business_name": "WhatsApp · Fahad", "source": "WHATSAPP"}
    first = service.build_reply_prompt(lead, [{"direction": "IN", "body": "Hi"}], "DNA", "- Web: sites.")
    later = service.build_reply_prompt(lead, [{"direction": "IN", "body": "Hi"}, {"direction": "OUT", "body": "Hello!"},
                                              {"direction": "IN", "body": "I need an e-commerce website"}], "DNA", "- Web")
    assert "greet them briefly as Fahad" in first and "do NOT greet them again" not in first
    assert "do NOT greet them again" in later and 'THEIR LATEST MESSAGE: "I need an e-commerce website"' in later
    assert "Never state a price" in later


def test_price_and_timing_questions_get_a_direct_answer():
    assert any("PRICE" in f for f in service.reply_focus("How much will it cost?"))
    assert any("PRICE" in f for f in service.reply_focus("দাম কত?"))
    assert any("TIMING" in f for f in service.reply_focus("I want it in 1 month"))
    assert service.reply_focus("Hi there") == []


def test_cut_off_replies_end_at_the_last_full_sentence():
    assert service.clean_reply("We can help with that. Could you tell me what you sell and when you nee") == "We can help with that."
    assert service.clean_reply("Sure — happy to help!") == "Sure — happy to help!"


def test_bad_ai_replies_are_not_sent():
    assert service.clean_reply("Your reply: Sure — happy to help!") == "Sure — happy to help!"
    assert service.clean_reply("[insert price here]") is None
    assert service.clean_reply("x" * 800) is None
    assert service.clean_reply("") is None


# ── Hooks, settings, the single send path ───────────────────────────────

async def test_n8n_hooks_need_the_secret(wa):
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/whatsapp/hooks/tick")).status_code == 401
        assert (await c.post("/api/whatsapp/hooks/tick", headers={"X-HOM-Secret": "wrong"})).status_code == 401
        assert (await c.post("/api/whatsapp/hooks/tick", headers={"X-HOM-Secret": "s3cret"})).status_code == 200
        r = await c.post("/api/whatsapp/hooks/event", headers={"X-HOM-Secret": "s3cret"}, json={"event": "session.status", "payload": {"status": "WORKING"}})
        assert r.status_code == 200


async def test_settings_are_validated(wa):
    for bad in ({"wa_daily_limit": 0}, {"wa_min_gap": 5}, {"wa_hours_start": 20, "wa_hours_end": 8}):
        with pytest.raises(service.WhatsAppError):
            await service.save_settings(bad)
            await service.save_settings({"wa_hours_start": 0, "wa_hours_end": 24})


async def test_send_whatsapp_uses_the_linked_engine(clean_db, monkeypatch):
    calls = []

    async def ready():
        return True

    async def send_text(phone, text):
        calls.append((phone, text))
        return "wamid.1"
    monkeypatch.setattr(engine, "ready", ready)
    monkeypatch.setattr(engine, "send_text", send_text)
    assert await whatsapp_sender.send_whatsapp("+971 50 111 2233", "Hello", {}) is True
    assert calls == [("+971501112233", "Hello")]


async def test_private_whatsapp_ids_are_mapped_to_the_phone(wa, monkeypatch):
    lid = await _contacted(wa)

    async def lookup(x):
        return "971501112233" if x == "4455@lid" else None
    monkeypatch.setattr(engine, "lid_to_phone", lookup)
    r = await service.handle_event(_event("4455", "sounds good", mid="L1") | {"payload": {
        "from": "4455@lid", "body": "sounds good", "fromMe": False, "id": "L1"}}, reply_now=True)
    assert r["auto_reply"] == "sent" and wa[-1][0] == "+971501112233"
    unknown = await service.handle_event({"event": "message", "payload": {"from": "999@lid", "body": "hi", "id": "L2"}})
    assert unknown.get("private_number") is True


async def test_draft_reply_uses_the_ai_slot_only_once(wa, monkeypatch):
    """Regression: the reply code must not hold the one-at-a-time AI slot while
    the AI call waits for it (that deadlocked every automatic reply)."""
    import asyncio
    from backend import ai_brain
    monkeypatch.undo()                       # the real draft_reply, with a fake model call
    lid = await _lead("Pearl Dental", "+971501112233")

    async def fake_cfg():
        return {"provider": "ollama", "model": "m", "base_url": "http://x", "timeout": 5}

    async def fake_llm(prompt, cfg, temperature=None, num_predict=800):
        async with ai_brain.ollama_slot():   # what the real call does
            return "Happy to help — what would you like to know?"
    monkeypatch.setattr(ai_brain, "_ollama_cfg", fake_cfg)
    monkeypatch.setattr(ai_brain, "_call_llm_raw", fake_llm)
    text = await asyncio.wait_for(service.draft_reply(lid, "971501112233@c.us"), timeout=5)
    assert text == "Happy to help — what would you like to know?"


# ── Uploaded contact files ──────────────────────────────────────────────

def test_phone_numbers_are_normalized_with_the_default_country_code():
    from backend.whatsapp.contacts import normalize_phone as n
    assert n("01712-345678", "+880") == "+8801712345678"          # local, leading 0
    assert n("1712345678", "880") == "+8801712345678"             # local, no 0
    assert n("+971 50 111 2233", "+880") == "+971501112233"       # already international
    assert n("00971501112233", "+880") == "+971501112233"
    assert n("971501112233", "") == "+971501112233"
    assert n("8801712345678.0", "+880") == "+8801712345678"      # Excel number
    assert n("12345", "+880") is None and n("", "+880") is None and n("call me", "") is None


def test_csv_with_any_column_names_invalid_rows_and_duplicates():
    from backend.whatsapp.contacts import parse_file
    csv = ("Full Name,Mobile Number,Company,City\n"
           "Sara Khan,01712345678,Bright Dental,Dhaka\n"
           "Omar,+971501112233,,Dubai\n"
           "Bad Row,12,,\n"
           "Sara again,+8801712345678,,\n").encode()
    p = parse_file("list.csv", csv, "+880")
    assert [c["phone"] for c in p.contacts] == ["+8801712345678", "+971501112233"]
    assert p.contacts[0]["name"] == "Sara Khan" and p.contacts[0]["company"] == "Bright Dental"
    assert p.duplicates == 1 and p.invalid == [{"row": 4, "value": "12", "reason": "not a valid phone number"}]
    assert p.columns["phone"] == "Mobile Number"


def test_file_without_headers_and_bad_files():
    from backend.whatsapp.contacts import ContactFileError, parse_file
    p = parse_file("numbers.csv", b"01712345678\n01812345678\n", "+880")
    assert len(p.contacts) == 2
    for name, data in (("x.csv", b""), ("x.xls", b"abc"), ("x.csv", b"Name,Email\nA,a@b.com\n")):
        with pytest.raises(ContactFileError):
            parse_file(name, data, "+880")


def test_xlsx_upload():
    import io
    from openpyxl import Workbook
    from backend.whatsapp.contacts import parse_file
    wb = Workbook()
    ws = wb.active
    ws.append(["WhatsApp", "Name"])
    ws.append([8801712345678, "Sara"])
    buf = io.BytesIO()
    wb.save(buf)
    p = parse_file("contacts.xlsx", buf.getvalue(), "+880")
    assert p.contacts[0]["phone"] == "+8801712345678" and p.contacts[0]["name"] == "Sara"


async def test_import_protects_opt_outs_and_reuses_existing_leads(wa):
    from backend.whatsapp.contacts import check_and_import, parse_file
    existing = await _lead("Pearl Dental", "+971501112233")
    await _lead("Opted out", "+971502223344", status="DO_NOT_CONTACT")
    await _lead("In talks", "+971503334455", status="MEETING")
    csv = ("Phone,Name,Company\n+971501112233,Amira,\n+971502223344,,\n+971503334455,,\n"
           "01712345678,Sara Khan,Bright Dental\n").encode()
    parsed = parse_file("c.csv", csv, "+880")
    dry = await check_and_import(parsed, save=False)
    assert (dry["ready"], dry["opted_out"], dry["already_talking"], dry["lead_ids"]) == (2, 1, 1, [])
    assert await db.portal_fetchrow("SELECT id FROM leads WHERE phone = ?", "+8801712345678") is None   # nothing saved
    res = await check_and_import(parsed, save=True)
    new = await db.portal_fetchrow("SELECT * FROM leads WHERE phone = ?", "+8801712345678")
    assert res["new_leads"] == 1 and set(res["lead_ids"]) == {existing, new["id"]}
    assert new["business_name"] == "Bright Dental" and new["contact_name"] == "Sara Khan" and new["source"] == "WHATSAPP_IMPORT"
    assert (await db.portal_fetchrow("SELECT contact_name FROM leads WHERE id = ?", existing))["contact_name"] == "Amira"
    # {first_name} now works for uploaded contacts
    c = await service.create_campaign("Upload", "Hi {first_name}, quick idea for {business_name}.", False, res["lead_ids"])
    await service.set_campaign_status(c["id"], "start")
    await service.tick()
    assert wa[0][1] == "Hi Amira, quick idea for Pearl Dental."


async def test_upload_endpoint(wa):
    from backend.main import app
    csv = b"Phone,Name\n01712345678,Sara\n01812345678,Omar\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        dry = (await c.post("/api/whatsapp/contacts", files={"file": ("list.csv", csv, "text/csv")},
                            data={"country_code": "+880", "save": "false"})).json()
        assert dry["ready"] == 2 and dry["lead_ids"] == [] and dry["preview"][0]["phone"] == "+8801712345678"
        saved = (await c.post("/api/whatsapp/contacts", files={"file": ("list.csv", csv, "text/csv")},
                              data={"country_code": "+880", "save": "true"})).json()
        assert len(saved["lead_ids"]) == 2
        r = await c.post("/api/whatsapp/campaigns", json={"name": "From file", "template": "Hello {first_name}, a quick idea.",
                                                         "lead_ids": saved["lead_ids"]})
        assert r.status_code == 201 and r.json()["total"] == 2
        bad = await c.post("/api/whatsapp/contacts", files={"file": ("x.csv", b"Name\nA\n", "text/csv")}, data={})
        assert bad.status_code == 400 and "phone column" in bad.json()["detail"]


# ── Meta WhatsApp Cloud API ─────────────────────────────────────────────

import hashlib as _hashlib
import hmac as _hmac
import json as _json


async def _use_meta(monkeypatch=None):
    for k, v in {"wa_engine": "meta", "wa_meta_phone_number_id": "1234567890", "wa_meta_waba_id": "999",
                 "wa_meta_access_token": "EAAtoken", "wa_meta_app_secret": "appsecret"}.items():
        await db.upsert_setting(k, v)


def _meta_payload(phone="971501112233", body="Hi there", mid="wamid.A", kind="text"):
    msg = {"from": phone, "id": mid, "timestamp": "0", "type": kind}
    if kind == "text":
        msg["text"] = {"body": body}
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": phone, "profile": {"name": "Omar"}}], "messages": [msg]}}]}]}


def test_meta_webhook_payload_becomes_hom_events():
    from backend.whatsapp import meta
    ev = meta.to_events(_meta_payload())
    assert ev == [{"event": "message", "payload": {"from": "971501112233@c.us", "body": "Hi there", "fromMe": False,
                                                   "id": "wamid.A", "timestamp": "0", "notifyName": "Omar"}}]
    assert meta.to_events(_meta_payload(kind="image")) == []            # media isn't answered automatically
    body = b'{"x":1}'
    sig = "sha256=" + _hmac.new(b"appsecret", body, _hashlib.sha256).hexdigest()
    assert meta.signature_ok(body, sig, "appsecret") and not meta.signature_ok(body, sig, "other")
    assert not meta.signature_ok(body, None, "appsecret")
    assert meta.fill_template("Hi {{1}}, about {{2}}.", ["Sara", "Bright Dental"]) == "Hi Sara, about Bright Dental."


async def test_meta_webhook_verification_and_signed_messages(wa):
    from backend.main import app
    await _use_meta()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        token = (await c.get("/api/whatsapp/meta")).json()["verify_token"]
        ok = await c.get("/api/whatsapp/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"})
        assert ok.status_code == 200 and ok.text == "42"
        bad = await c.get("/api/whatsapp/meta/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
        assert bad.status_code == 403
        body = _json.dumps(_meta_payload(phone="971509990000", body="Do you build websites?", mid="wamid.B")).encode()
        assert (await c.post("/api/whatsapp/meta/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=forged"})).status_code == 401
        sig = "sha256=" + _hmac.new(b"appsecret", body, _hashlib.sha256).hexdigest()
        r = await c.post("/api/whatsapp/meta/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
        assert r.status_code == 200
    lead = await db.portal_fetchrow("SELECT * FROM leads WHERE phone = ?", "+971509990000")
    assert lead["business_name"] == "WhatsApp · Omar"
    msg = await db.portal_fetchrow("SELECT * FROM whatsapp_messages WHERE wa_message_id = ?", "wamid.B")
    assert msg["direction"] == "IN" and msg["body"] == "Do you build websites?"


async def test_meta_secrets_are_masked_and_kept(wa):
    from backend.main import app
    await _use_meta()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        got = (await c.get("/api/whatsapp/meta")).json()
        assert got["access_token"] == "••••set••••" and got["app_secret"] == "••••set••••" and got["phone_number_id"] == "1234567890"
        await c.put("/api/whatsapp/meta", json={"access_token": "••••set••••", "waba_id": "777"})
        assert (await c.put("/api/whatsapp/meta", json={"phone_number_id": "abc"})).status_code == 422
    from backend.whatsapp import meta
    cfg = await meta.config()
    assert cfg["access_token"] == "EAAtoken" and cfg["waba_id"] == "777"
    row = await db.portal_fetchrow("SELECT value FROM app_settings WHERE key = 'wa_meta_access_token'")
    assert "EAAtoken" not in row["value"]                              # encrypted at rest


async def test_send_whatsapp_uses_meta_when_chosen(clean_db, monkeypatch):
    from backend.whatsapp import meta
    await _use_meta()
    calls = []

    async def send_text(phone, text):
        calls.append(("text", phone, text))
        return "wamid.1"

    async def send_template(phone, name, language, params):
        calls.append(("template", phone, name, language, params))
        return "wamid.2"
    monkeypatch.setattr(meta, "send_text", send_text)
    monkeypatch.setattr(meta, "send_template", send_template)
    assert await whatsapp_sender.send_whatsapp("+971 50 111 2233", "Hello", {}) is True
    assert await whatsapp_sender.send_whatsapp("+971501112233", "x", {"template": {"name": "intro", "language": "en", "params": ["Sara"]}}) is True
    assert calls == [("text", "+971501112233", "Hello"), ("template", "+971501112233", "intro", "en", ["Sara"])]


async def test_meta_campaigns_need_an_approved_template(wa, monkeypatch):
    await _use_meta()
    a = await _lead("Pearl Dental", "+971501112233", contact_name="Amira")
    with pytest.raises(service.WhatsAppError, match="template"):
        await service.create_campaign("Free text", "Hello {business_name}, a quick idea.", False, [a])
    c = await service.create_campaign("Template", None, False, [a], meta_template={
        "name": "intro_offer", "language": "en_US", "body": "Hi {{1}}, a quick idea for {{2}}.", "vars": ["first_name", "business_name"]})
    await service.set_campaign_status(c["id"], "start")
    sent = []

    async def fake_send(phone, text, config):
        sent.append((phone, text, config))
        return True
    await service.tick(send=fake_send, is_ready=lambda: _true())
    assert sent == [("+971501112233", "Hi Amira, a quick idea for Pearl Dental.",
                     {"template": {"name": "intro_offer", "language": "en_US", "params": ["Amira", "Pearl Dental"]}})]


async def _true():
    return True


async def test_workspaces_can_only_use_meta(wa, monkeypatch):
    monkeypatch.setenv("HOM_EDITION", "client")
    assert await service.current_engine() == "meta"
    with pytest.raises(service.WhatsAppError, match="Meta"):
        await service.save_settings({"wa_engine": "web"})
