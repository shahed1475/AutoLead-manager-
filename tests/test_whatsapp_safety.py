"""WhatsApp number safety: an advisory risk meter, plus a warm-up cap that
only applies when the owner turns Safe mode on (off by default)."""
import pytest

from backend.whatsapp import safety, service

pytestmark = pytest.mark.asyncio


def test_warmup_ramp():
    assert [safety.warmup_limit(d) for d in (0, 7, 8, 14, 20, 28, 60)] == [15, 15, 30, 30, 50, 50, 100]
    assert safety.warmup_limit(None) == 15          # never sent before = brand-new number


def test_official_api_is_low_risk():
    r = safety.assess("meta", sent_today=150, daily_limit=200, age_days=2,
                      auto_reply=True, reply_scope="everyone", auto_replies_today=90)
    assert r["level"] == "low" and r["recommended_limit"] is None


def test_new_web_number_over_ramp_is_high():
    r = safety.assess("web", sent_today=10, daily_limit=100, age_days=3,
                      auto_reply=False, reply_scope="leads", auto_replies_today=0)
    assert r["level"] == "high" and r["recommended_limit"] == 15
    assert any("15" in reason for reason in r["reasons"])


def test_seasoned_web_number_within_ramp_is_low():
    r = safety.assess("web", sent_today=5, daily_limit=30, age_days=90,
                      auto_reply=True, reply_scope="leads", auto_replies_today=3)
    assert r["level"] == "low"


def test_auto_reply_to_everyone_raises_risk():
    base = dict(sent_today=5, daily_limit=30, age_days=90, auto_reply=True, auto_replies_today=150)
    assert safety.assess("web", reply_scope="everyone", **base)["level"] == "medium"
    assert safety.assess("web", reply_scope="leads", **{**base, "auto_replies_today": 3})["level"] == "low"


async def test_pacing_unchanged_when_safe_mode_off(clean_db, monkeypatch):
    monkeypatch.setattr(service, "current_engine", _engine("web"))
    s = {**service.DEFAULTS, "wa_daily_limit": 120}
    p = await service.pacing(s)
    assert p["daily_limit"] == 120 and p["safe_mode"] is False
    assert p["safety"]["level"] in ("low", "medium", "high")


async def test_safe_mode_caps_daily_limit_to_ramp(clean_db, monkeypatch):
    monkeypatch.setattr(service, "current_engine", _engine("web"))
    s = {**service.DEFAULTS, "wa_daily_limit": 120, "wa_safe_mode": True}
    p = await service.pacing(s)
    assert p["daily_limit"] == 15                   # no history → new-number ramp
    assert "wa_safe_mode" in service.DEFAULTS and service.DEFAULTS["wa_safe_mode"] is False


def _engine(name):
    async def f():
        return name
    return f


async def test_number_age_comes_from_first_outbound_message(clean_db, monkeypatch):
    monkeypatch.setattr(service, "current_engine", _engine("web"))
    async with clean_db.transaction() as tx:
        await tx.execute(
            "INSERT INTO whatsapp_messages (chat_id, direction, body, source, created_at) "
            "VALUES ('8801', 'OUT', 'hi', 'campaign', datetime('now', '-60 days'))")
    s = {**service.DEFAULTS, "wa_daily_limit": 120, "wa_safe_mode": True}
    p = await service.pacing(s)
    assert p["safety"]["recommended_limit"] == 100 and p["daily_limit"] == 100


async def test_established_number_is_not_rated_by_its_hom_history(clean_db, monkeypatch):
    """A number used for years before HOM isn't 'new' just because HOM
    started sending yesterday — the owner can say so."""
    monkeypatch.setattr(service, "current_engine", _engine("web"))
    s = {**service.DEFAULTS, "wa_daily_limit": 30}
    new = await service.pacing(s)
    assert new["safety"]["level"] == "high"
    assert any("already in use before HOM" in r for r in new["safety"]["reasons"])
    old = await service.pacing({**s, "wa_number_established": True})
    assert old["safety"]["level"] == "low" and old["safety"]["recommended_limit"] == 100
    assert service.DEFAULTS["wa_number_established"] is False
