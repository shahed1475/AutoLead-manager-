"""Social media automation: connecting Meta Pages/Instagram, AI compose,
drafts → schedule → publish, partial failures, the public image address and
the n8n hook — with Meta's API faked (nothing is posted)."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.social import browser, inbox, linkedin, meta, service, xapi

pytestmark = pytest.mark.asyncio
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
JPG = b"\xff\xd8\xff" + b"0" * 100
REAL_PUBLIC_BASE = service.public_base


@pytest.fixture
async def social(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "MEDIA_DIR", tmp_path / "media")
    calls = []

    async def fake_call(method, path, token, **kw):
        calls.append((method, path, token, kw.get("data"), bool(kw.get("files"))))
        if path == "me/accounts":
            return {"data": [{"id": "111", "name": "PopupGenix", "access_token": "PAGE-TOKEN",
                              "instagram_business_account": {"id": "222", "username": "popupgenix"}}]}
        if path.endswith("/photos"):
            return {"id": "p1", "post_id": "111_555"}
        if path.endswith("/feed"):
            return {"id": "111_556"}
        if path.endswith("/media"):
            return {"id": "creation-1"}
        if path.endswith("/media_publish"):
            return {"id": "ig-media-1"}
        if path == "ig-media-1":
            return {"permalink": "https://instagram.com/p/abc"}
        return {"name": "PopupGenix", "username": "popupgenix"}
    monkeypatch.setattr(meta, "_call", fake_call)
    monkeypatch.setattr(service, "public_base", lambda: "https://dash.example.com")
    monkeypatch.setattr(inbox, "start_background_sync", lambda: False)     # inbox tests drive it directly
    return calls


async def _connect():
    await service.connect_meta("USER-TOKEN")
    return {a["platform"]: a["id"] for a in await service.accounts()}


async def test_connect_finds_page_and_instagram_and_encrypts_tokens(social):
    found = await service.discover_meta("USER-TOKEN")
    assert found == [{"platform": "facebook", "external_id": "111", "name": "PopupGenix"},
                     {"platform": "instagram", "external_id": "222", "name": "@popupgenix"}]
    accts = await _connect()
    assert set(accts) == {"facebook", "instagram"}
    row = await db.portal_fetchrow("SELECT token_enc FROM social_accounts WHERE platform = 'facebook'")
    assert "PAGE-TOKEN" not in row["token_enc"]
    assert "token_enc" not in (await service.accounts())[0]


async def test_images_are_checked(social):
    assert service.save_media(PNG, "image/png").endswith(".png")
    for data, ctype in ((b"GIF89a", "image/gif"), (b"not an image", "image/jpeg"), (b"", "image/png")):
        with pytest.raises(service.SocialError):
            service.save_media(data, ctype)
    assert service.media_path("../../etc/passwd") is None and service.media_path("x.jpg") is None


async def test_post_rules(social):
    accts = await _connect()
    with pytest.raises(service.SocialError, match="Instagram posts need an image"):
        await service.save_post(None, "Hello", {}, [accts["instagram"]], None)
    with pytest.raises(service.SocialError, match="at least one account"):
        await service.save_post(None, "Hello", {}, [], None)
    p = await service.save_post(None, "Hello", {"facebook": "FB version"}, [accts["facebook"]], None)
    assert p["status"] == "DRAFT" and p["captions"] == {"facebook": "FB version"}


async def test_schedule_then_tick_publishes_to_both(social):
    accts = await _connect()
    img = service.save_media(JPG, "image/jpeg")
    p = await service.save_post(None, "Base", {"facebook": "FB text", "instagram": "IG text #ai"},
                                [accts["facebook"], accts["instagram"]], img)
    assert (await service.tick())["reason"] == "Nothing due"                 # drafts never publish
    await service.schedule(p["id"], "2000-01-01T00:00:00Z")                   # due
    r = await service.tick()
    post = await service.get_post(p["id"])
    assert r["published"] and post["status"] == "PUBLISHED"
    fb = next(c for c in social if c[1] == "111/photos")
    assert fb[2] == "PAGE-TOKEN" and fb[3] == {"caption": "FB text"} and fb[4] is True     # image uploaded
    ig = next(c for c in social if c[1] == "222/media")
    assert ig[3] == {"image_url": f"https://dash.example.com/api/social/media/{img}", "caption": "IG text #ai"}
    urls = {t["platform"]: t["url"] for t in post["targets"]}
    assert urls == {"facebook": "https://www.facebook.com/111_555", "instagram": "https://instagram.com/p/abc"}


async def test_one_platform_failing_gives_partial(social, monkeypatch):
    accts = await _connect()
    monkeypatch.setattr(service, "public_base", lambda: None)               # dashboard link offline
    img = service.save_media(JPG, "image/jpeg")
    p = await service.save_post(None, "Hi", {}, [accts["facebook"], accts["instagram"]], img)
    await service.schedule(p["id"], None)
    await service.tick()
    post = await service.get_post(p["id"])
    assert post["status"] == "PARTIAL"
    ig = next(t for t in post["targets"] if t["platform"] == "instagram")
    assert ig["status"] == "FAILED" and "dashboard link" in ig["error"]
    kinds = [a["kind"] for a in await service.activity()]
    assert "published" in kinds and "failed" in kinds


async def test_ai_compose_uses_the_company_profile(social, monkeypatch):
    from backend import ai_brain
    prompts = []

    async def fake_cfg():
        return {"provider": "ollama", "model": "m", "base_url": "x", "timeout": 5}

    async def fake_llm(prompt, cfg, temperature=None, num_predict=800):
        prompts.append(prompt)
        return "Post: Launch your store this month! #ecommerce"
    monkeypatch.setattr(ai_brain, "_ollama_cfg", fake_cfg)
    monkeypatch.setattr(ai_brain, "_call_llm_raw", fake_llm)
    out = await service.compose("We build e-commerce sites", ["facebook", "instagram"])
    assert out == {"facebook": "Launch your store this month! #ecommerce", "instagram": "Launch your store this month! #ecommerce"}
    assert "never invent prices" in prompts[0] and "Facebook" in prompts[0] and "Instagram" in prompts[1]
    with pytest.raises(service.SocialError):
        await service.compose("  ", ["facebook"])


async def test_public_image_and_hook_secret(social, monkeypatch, tmp_path):
    from backend.main import app
    cfg = tmp_path / "wa"
    cfg.mkdir()
    (cfg / "secrets.env").write_text("HOM_WA_SECRET=s3cret\n")
    monkeypatch.setenv("HOM_WA_CONFIG_DIR", str(cfg))
    img = service.save_media(PNG, "image/png")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/social/media/{img}")
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
        assert (await c.get("/api/social/media/0000.png")).status_code == 404
        assert (await c.post("/api/social/hooks/tick")).status_code == 401
        assert (await c.post("/api/social/hooks/tick", headers={"X-HOM-Secret": "s3cret"})).json()["reason"] == "Nothing due"


async def test_owner_routes_need_the_password(social):
    from backend import auth
    from backend.main import app
    await auth.set_password("owner-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/social/posts")).status_code == 401
        assert (await c.get("/api/social/accounts")).status_code == 401


# ── Client workspaces: own API keys only ─────────────────────────────────────

async def test_client_workspace_uses_api_only_and_its_own_image_route(social, monkeypatch, tmp_path):
    from fastapi import HTTPException
    from backend.routers import social as social_router
    monkeypatch.setenv("HOM_EDITION", "client")
    monkeypatch.setenv("HOM_WORKSPACE_PORT", "7003")
    monkeypatch.setattr(service, "public_base", REAL_PUBLIC_BASE)      # the fixture fakes it
    monkeypatch.setattr(service, "CLIENT_LINK_FILE", tmp_path / "client-link.txt")

    with pytest.raises(service.SocialError, match="API"):
        await service.connect_browser("linkedin", "Me")
    with pytest.raises(HTTPException) as exc:
        social_router._owner_only_browser()
    assert exc.value.status_code == 403

    img = service.save_media(JPG, "image/jpeg")
    assert service.media_url(img) is None                        # client link not known yet
    (tmp_path / "client-link.txt").write_text("https://client.example.com/\n")
    assert service.media_url(img) == f"https://client.example.com/social-media/7003/{img}"

    # A browser account (e.g. from a copied database) is never used to publish.
    await db.portal_execute("INSERT INTO social_accounts (platform, transport, external_id, name, status)"
                            " VALUES ('x', 'browser', 'browser-1', 'Old', 'connected')")
    a = await db.portal_fetchrow("SELECT id FROM social_accounts WHERE external_id = 'browser-1'")
    with pytest.raises(browser.BrowserError, match="API"):
        await service.publish_target({"text": "hi", "captions": {}, "media_name": None}, {"account_id": a["id"]})


# ── Phase 2: inbox (comments + DMs) ─────────────────────────────────────────

@pytest.fixture
async def inboxed(social, monkeypatch):
    """Meta inbox faked: `feed` holds what Meta currently returns."""
    feed = {"fb_comments": [], "fb_dms": [], "ig_comments": [], "ig_dms": []}
    sent = []

    async def fb_comments(page, token):
        return list(feed["fb_comments"])

    async def ig_comments(ig, token):
        return list(feed["ig_comments"])

    async def convs(page, token, platform):
        return list(feed["fb_dms" if platform == "messenger" else "ig_dms"])

    async def reply_comment(platform, cid, token, text):
        sent.append(("comment", platform, cid, text))
        return f"r-{cid}"

    async def send_dm(page, token, rid, text):
        sent.append(("dm", page, rid, text))
        return f"m-{len(sent)}"

    async def fake_draft(message_id):
        m = await inbox._message(message_id)
        return f"Thanks {m['author_name']}!"
    monkeypatch.setattr(meta, "facebook_comments", fb_comments)
    monkeypatch.setattr(meta, "instagram_comments", ig_comments)
    monkeypatch.setattr(meta, "conversations", convs)
    monkeypatch.setattr(meta, "reply_comment", reply_comment)
    monkeypatch.setattr(meta, "send_dm", send_dm)
    monkeypatch.setattr(inbox, "draft", fake_draft)
    await _connect()
    return feed, sent


def _now_meta(minutes_ago=1):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _c(cid, author, text, thread="111_1", when=None, aid=None):
    return {"kind": "comment", "external_id": cid, "thread_id": thread, "author_id": aid or f"u-{author}",
            "author_name": author, "text": text, "created_at": when or _now_meta()}


def _dm(mid, author, text, thread="t1", when=None, aid=None):
    return {**_c(mid, author, text, thread, when, aid), "kind": "dm"}


async def test_first_sync_only_marks_the_backlog_seen(inboxed):
    feed, sent = inboxed
    feed["fb_comments"] = [_c("c1", "Ana", "How much is a website?")]
    feed["fb_dms"] = [_dm("m1", "Bo", "hi")]
    r = await inbox.sync_all(force=True)
    assert r["new"] == 0 and sent == []
    assert {m["status"] for m in await db.portal_fetch("SELECT status FROM social_messages")} == {"SEEN"}


async def test_new_comment_and_dm_get_answered_and_become_leads(inboxed):
    feed, sent = inboxed
    await inbox.sync_all(force=True)                                  # first sync: backlog
    feed["fb_comments"] = [_c("c2", "Ana", "How much is a website?"), _c("c3", "PopupGenix", "Thanks all", aid="111")]
    feed["ig_dms"] = [_dm("m2", "@bo", "I need an online store", thread="t9")]
    feed["ig_comments"] = [_c("c4", "@old", "nice", when="2020-01-01T00:00:00+0000", thread="ig1")]
    r = await inbox.sync_all(force=True)
    assert r["new"] == 2
    assert ("comment", "facebook", "c2", "Thanks Ana!") in sent
    assert ("dm", "111", "u-@bo", "Thanks @bo!") in sent              # IG DMs go through the Page
    assert not any(s[2] in ("c3", "c4") for s in sent)                # own comment / old comment: never answered
    own = await db.portal_fetchrow("SELECT direction, status FROM social_messages WHERE external_id = 'c3'")
    assert own == {"direction": "OUT", "status": "SENT"}
    leads = {l["business_name"]: l["source"] for l in await db.portal_fetch("SELECT business_name, source FROM leads")}
    assert leads == {"Facebook · Ana": "FACEBOOK", "Instagram · @bo": "INSTAGRAM"}
    assert (await inbox.summary())["auto_today"] == 2
    await inbox.sync_all(force=True)                                  # nothing is answered twice
    assert len(sent) == 2


async def test_opt_out_stops_replies_and_marks_do_not_contact(inboxed):
    feed, sent = inboxed
    await inbox.sync_all(force=True)
    feed["fb_dms"] = [_dm("m1", "Cy", "Do you do SEO?")]
    await inbox.sync_all(force=True)
    assert len(sent) == 1
    feed["fb_dms"] = [_dm("m1", "Cy", "Do you do SEO?"), _dm("m3", "Cy", "stop messaging me", when=_now_meta(0))]
    await inbox.sync_all(force=True)
    assert len(sent) == 1
    lead = await db.portal_fetchrow("SELECT status FROM leads WHERE business_name = 'Facebook · Cy'")
    assert lead["status"] == "DO_NOT_CONTACT"
    feed["fb_dms"].append(_dm("m4", "Cy", "hello again?", when=_now_meta(0)))
    await inbox.sync_all(force=True)
    assert len(sent) == 1                                             # never again, not even by hand
    m4 = await db.portal_fetchrow("SELECT id, status FROM social_messages WHERE external_id = 'm4'")
    assert m4["status"] == "SKIPPED"
    with pytest.raises(service.SocialError, match="asked not to be contacted"):
        await inbox.send_reply(m4["id"], "Hi!")


async def test_auto_reply_switch_cap_and_manual_reply(inboxed):
    feed, sent = inboxed
    await inbox.sync_all(force=True)
    await inbox.save_settings({"social_auto_reply": False})
    feed["fb_comments"] = [_c("c5", "Di", "great post")]
    await inbox.sync_all(force=True)
    assert sent == []
    c5 = await db.portal_fetchrow("SELECT id, status, lead_id FROM social_messages WHERE external_id = 'c5'")
    assert c5["status"] == "SEEN" and c5["lead_id"] is None           # praise isn't a lead
    out = await inbox.send_reply(c5["id"], "Thank you!")               # you answer by hand
    assert out["status"] == "REPLIED" and sent == [("comment", "facebook", "c5", "Thank you!")]
    thread = await inbox.thread(c5["id"])
    assert [t["direction"] for t in thread] == ["IN", "OUT"]
    await inbox.save_settings({"social_auto_reply": True, "social_reply_per_person": 1})
    feed["fb_dms"] = [_dm("m5", "Ed", "price?", thread="t5")]
    await inbox.sync_all(force=True)
    feed["fb_dms"].append(_dm("m6", "Ed", "and timing?", thread="t5", when=_now_meta(0)))
    await inbox.sync_all(force=True)
    assert [s[3] for s in sent].count("Thanks Ed!") == 1              # daily cap per person


async def test_missing_permission_is_reported_not_fatal(inboxed, monkeypatch):
    async def denied(page, token, platform):
        raise meta.MetaError("(#200) Requires pages_messaging permission")
    monkeypatch.setattr(meta, "conversations", denied)
    await inbox.sync_all(force=True)
    accts = {a["platform"]: a for a in await service.accounts()}
    assert "pages_messaging" in accts["facebook"]["inbox_error"] and accts["facebook"]["inbox"] is True


# ── Phase 3: LinkedIn + X (API) and browser accounts ────────────────────────

async def test_linkedin_api_connect_and_publish(social, monkeypatch):
    calls = []

    async def ident(token, org_id=None):
        return {"external_id": "org-42", "author": "urn:li:organization:42", "name": "PopupGenix"}

    async def pub(author, token, text, image=None):
        calls.append((author, token, text, bool(image)))
        return {"id": "urn:li:share:1", "url": "https://www.linkedin.com/feed/update/urn:li:share:1/"}
    monkeypatch.setattr(linkedin, "identify", ident)
    monkeypatch.setattr(linkedin, "publish", pub)
    accts = await service.connect_linkedin("LI-TOKEN", "42")
    li = next(a for a in accts if a["platform"] == "linkedin")
    assert li["target"] == "company" and li["transport"] == "api"
    row = await db.portal_fetchrow("SELECT token_enc FROM social_accounts WHERE id = ?", li["id"])
    assert "LI-TOKEN" not in row["token_enc"]
    p = await service.save_post(None, "Base", {"linkedin": "LI text #growth"}, [li["id"]], None)
    post = await service.publish_post(p["id"])
    assert post["status"] == "PUBLISHED" and calls == [("urn:li:organization:42", "LI-TOKEN", "LI text #growth", False)]


async def test_x_api_renews_an_expired_token_once(social, monkeypatch):
    async def me(token):
        return {"external_id": "9", "name": "@hom"}
    tokens = []

    async def pub(token, text, image=None, filename="image.jpg"):
        tokens.append(token)
        if token == "OLD":
            raise xapi.Expired("expired")
        return {"id": "5", "url": "https://x.com/i/web/status/5"}

    async def refresh(creds):
        return {**creds, "access_token": "NEW", "refresh_token": "R2"}
    monkeypatch.setattr(xapi, "me", me)
    monkeypatch.setattr(xapi, "publish", pub)
    monkeypatch.setattr(xapi, "refresh", refresh)
    accts = await service.connect_x("OLD", "R1", "client")
    x = next(a for a in accts if a["platform"] == "x")
    with pytest.raises(service.SocialError, match="X allows 280"):
        await service.save_post(None, "a" * 281, {}, [x["id"]], None)
    p = await service.save_post(None, "Short post", {}, [x["id"]], None)
    post = await service.publish_post(p["id"])
    assert post["status"] == "PUBLISHED" and tokens == ["OLD", "NEW"]
    row = await db.portal_fetchrow("SELECT token_enc FROM social_accounts WHERE id = ?", x["id"])
    assert service.x_creds(row)["refresh_token"] == "R2"               # renewed token saved (encrypted)


async def test_browser_account_needs_sign_in_and_pauses_on_checks(social, monkeypatch):
    a = await service.connect_browser("linkedin", "My LinkedIn", None)
    assert a["status"] == "needs_login" and a["transport"] == "browser"
    with pytest.raises(service.SocialError):
        await service.connect_browser("facebook", "x", None)
    p = await service.save_post(None, "Hello LinkedIn", {}, [a["id"]], None)
    post = await service.publish_post(p["id"])
    assert post["status"] == "FAILED" and "Sign in" in post["targets"][0]["error"]

    async def logged_in(aid, platform):
        return True

    async def blocked(account, text, image):
        raise browser.NeedsLogin("The site is asking for a security check.")
    monkeypatch.setattr(browser, "logged_in", logged_in)
    monkeypatch.setattr(browser, "publish", blocked)
    assert (await service.browser_done(a["id"]))["status"] == "connected"
    await service.schedule(p["id"], None)
    post = await service.publish_post(p["id"])
    assert post["status"] == "FAILED" and "security check" in post["targets"][0]["error"]
    acct = next(x for x in await service.accounts() if x["id"] == a["id"])
    assert acct["status"] == "needs_login"                             # paused until you finish it yourself


async def test_browser_live_window_only_opens_the_platform(social):
    with pytest.raises(browser.BrowserError):
        await browser.act(999, "x", {"type": "click", "x": 1, "y": 1})   # no window open
    s = browser._Session(None, None, type("P", (), {"url": "", "is_closed": lambda self: False})())
    browser._sessions[998] = s
    try:
        for bad in ("https://evil.example.com/", "http://x.com/home", "https://x.com.evil.io/", "file:///etc/passwd"):
            with pytest.raises(browser.BrowserError):
                await browser.act(998, "x", {"type": "goto", "url": bad})
        with pytest.raises(browser.BrowserError):
            await browser.act(998, "x", {"type": "key", "key": "Control+A"})
        with pytest.raises(browser.BrowserError):
            await browser.act(998, "x", {"type": "click", "x": 5000, "y": 1})
    finally:
        browser._sessions.pop(998, None)


def test_linkedin_little_text_keeps_hashtags():
    assert linkedin.little_text("Hi (all) #growth @me") == "Hi \\(all\\) {hashtag|\\#|growth} \\@me"
