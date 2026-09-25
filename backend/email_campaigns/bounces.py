"""
email_campaigns/bounces.py — spot "undeliverable" notices in the reply inbox,
remember the dead address so it's never mailed again, and pause any running
campaign whose bounce rate climbs past the point inboxes start to distrust
the sender. Only ever blocks or pauses — never sends.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from .. import database as db

logger = logging.getLogger(__name__)

BOUNCE_RATE_LIMIT = 0.05      # pause above 5% …
MIN_SENT_FOR_RATE = 20        # … once at least this many have gone out

_BOUNCE_SENDER = re.compile(r"^(mailer-daemon|postmaster|mail-daemon|bounce[s]?)@", re.I)
_BOUNCE_SUBJECT = re.compile(
    r"delivery status notification \(failure\)|undeliver(able|ed)|delivery (has )?failed|"
    r"returned mail|mail delivery (failed|subsystem)|failure notice|address not found", re.I)
_SOFT = re.compile(r"\b4\.\d\.\d\b|delay(ed)?|will retry|temporar", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FINAL_RCPT = re.compile(r"(?:final|original)-recipient:\s*rfc822;\s*<?([^\s>]+)", re.I)


def parse_bounce(from_email: str, subject: str, body: str, own: Iterable[str] = ()) -> Optional[List[str]]:
    """The addresses a permanent-failure notice is about, or None if this
    isn't one (a normal reply, or a temporary delay that will be retried)."""
    if not (_BOUNCE_SENDER.search(from_email or "") or _BOUNCE_SUBJECT.search(subject or "")):
        return None
    text = body or ""
    if _SOFT.search(subject or "") or (_SOFT.search(text) and not re.search(r"\b5\.\d\.\d\b", text)):
        return None
    mine = {o.lower() for o in own} | {(from_email or "").lower()}
    found = [m.lower() for m in _FINAL_RCPT.findall(text)] or [m.lower() for m in _EMAIL.findall(text)]
    out: List[str] = []
    for addr in found:
        addr = addr.strip(".;,")
        if addr in mine or _BOUNCE_SENDER.search(addr) or addr in out:
            continue
        out.append(addr)
    return out or None


async def handle_bounce(emails: List[str], reason: str) -> List[int]:
    """Record the bounced addresses and pause campaigns now over the bounce
    limit. Returns the ids of campaigns paused by this call."""
    from .service import get_email_campaign_service
    touched: set = set()
    for e in emails:
        await db.record_bounce(e, reason)
        touched.update(await db.mark_campaign_leads_bounced(e, reason))
    paused: List[int] = []
    svc = get_email_campaign_service()
    for cid in sorted(touched):
        camp = await db.get_email_campaign(cid)
        if not camp or camp["status"] != "RUNNING":
            continue
        sent, bounced = await db.email_campaign_bounce_counts(cid)
        if sent >= MIN_SENT_FOR_RATE and bounced / sent > BOUNCE_RATE_LIMIT:
            await svc.pause(cid)
            await db.log_email_campaign_activity(
                cid, "bounce_rate_paused",
                f"{bounced} of {sent} emails bounced ({bounced / sent:.0%}); paused to protect the sender",
                level="WARNING")
            paused.append(cid)
    return paused
