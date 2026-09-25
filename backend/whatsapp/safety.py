"""
whatsapp/safety.py — how risky the current WhatsApp sending pattern is for the
number. Advisory: nothing here blocks a send unless the owner turns on Safe
mode (wa_safe_mode), which caps campaign messages per day at the warm-up ramp.

The WhatsApp Web engine isn't Meta's official API, so a new number that sends
a lot at once is the classic ban pattern; the official Cloud API has its own
limits enforced by Meta and isn't rated here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Campaign messages per day a Web-engine number can take, by age in days
# (counted from its first message sent through HOM).
_RAMP = ((7, 15), (14, 30), (28, 50))
SEASONED_LIMIT = 100
ESTABLISHED_AGE_DAYS = 365    # used when the owner says the number predates HOM
BUSY_AUTO_REPLIES = 50        # automatic answers per day that start to look like a bot


def warmup_limit(age_days: Optional[int]) -> int:
    if age_days is None:
        return _RAMP[0][1]
    for max_age, limit in _RAMP:
        if age_days <= max_age:
            return limit
    return SEASONED_LIMIT


def assess(engine: str, sent_today: int, daily_limit: int, age_days: Optional[int],
           auto_reply: bool, reply_scope: str, auto_replies_today: int) -> Dict[str, Any]:
    if engine == "meta":
        return {"level": "low", "recommended_limit": None,
                "reasons": ["Official WhatsApp API: sending limits are managed by Meta."]}
    ramp = warmup_limit(age_days)
    if age_days is None:
        age_txt = "a new number"
    elif age_days >= ESTABLISHED_AGE_DAYS:
        age_txt = "an established number"
    else:
        age_txt = f"a number first used through HOM {age_days} day(s) ago"
    reasons: List[str] = []
    level = "low"
    if daily_limit > ramp:
        level = "high"
        reasons.append(f"Daily limit {daily_limit} is above the safe {ramp} a day for {age_txt}.")
    if sent_today > ramp:
        level = "high"
        reasons.append(f"{sent_today} campaign messages sent today, above the safe {ramp}.")
    if auto_reply and reply_scope == "everyone" and auto_replies_today > BUSY_AUTO_REPLIES:
        level = "high" if level == "high" else "medium"
        reasons.append(f"{auto_replies_today} automatic replies today to everyone who writes.")
    if level == "high" and (age_days is None or age_days < ESTABLISHED_AGE_DAYS):
        reasons.append("If this number was already in use before HOM, mark it as established in Settings.")
    if not reasons:
        reasons.append(f"Sending stays within the safe {ramp} a day for {age_txt}.")
    return {"level": level, "recommended_limit": ramp, "reasons": reasons}
