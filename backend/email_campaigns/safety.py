"""
safety.py — the fail-closed TEST_MODE safety gate for Email Campaigns.

`assert_send_allowed(campaign, send_to, lead_email)` runs IMMEDIATELY before
every `email_sender.send_email()` call in the campaign send path. It raises
`SafetyGateError` (which the caller records as SEND_BLOCKED) unless the send is
provably safe. It NEVER returns a "corrected" address and NEVER falls back to
the lead's real email — missing / inconsistent / malformed configuration all
BLOCK the send.

While `test_mode` is on (the default, and the only mode this checkpoint
supports) the gate requires ALL of:
  * campaign.test_mode is truthy
  * campaign.test_recipient is a well-formed address
  * send_to == campaign.test_recipient   (exact, case-insensitive, single addr)
  * send_to != lead_email                (proves the TEST_MODE redirect happened)

Production mode (`test_mode` off) is intentionally rejected here — enabling it
is a separate, explicitly-approved change (see PRODUCTION_READINESS.md).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..validators import is_valid_email


class SafetyGateError(RuntimeError):
    """Raised when a campaign send fails the fail-closed safety gate."""


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _truthy(v: Any) -> bool:
    return v in (True, 1, "1", "true", "True", "TRUE")


def assert_send_allowed(campaign: Dict[str, Any], send_to: str, lead_email: Optional[str]) -> None:
    """Fail closed. Raises SafetyGateError unless this exact send is safe."""
    if not campaign:
        raise SafetyGateError("no campaign context")

    test_mode      = campaign.get("test_mode")
    test_recipient = campaign.get("test_recipient")

    # test_mode must be present and unambiguously set.
    if test_mode is None:
        raise SafetyGateError("campaign.test_mode is missing — fail closed")

    if not _truthy(test_mode):
        # Production sending is not enabled in this build. Never fall through.
        raise SafetyGateError(
            "production sending is not enabled — test_mode must be true "
            "(enabling production is a separate approved change)"
        )

    # --- TEST_MODE assertions ------------------------------------------------
    if not test_recipient or not str(test_recipient).strip():
        raise SafetyGateError("campaign.test_recipient is missing while test_mode is on")

    tr = str(test_recipient).strip()
    if "," in tr or " " in tr:
        raise SafetyGateError("campaign.test_recipient must be exactly one address")
    if not is_valid_email(tr):
        raise SafetyGateError(f"campaign.test_recipient is malformed: {tr!r}")

    st = (send_to or "").strip()
    if not st:
        raise SafetyGateError("send_to is empty")
    if "," in st or " " in st:
        raise SafetyGateError("send_to must be exactly one address")
    if not is_valid_email(st):
        raise SafetyGateError(f"send_to is malformed: {st!r}")

    if _norm(st) != _norm(tr):
        raise SafetyGateError(
            f"TEST_MODE: send_to ({st}) is not the configured test recipient ({tr}) — blocked"
        )

    if lead_email and _norm(st) == _norm(lead_email):
        # The redirect to the test recipient must have changed the address.
        # If they're equal, either the redirect didn't happen or the lead's
        # own email is the test address (not allowed for a real campaign).
        raise SafetyGateError(
            "TEST_MODE: send_to equals the lead's real email — the redirect did not apply, blocked"
        )
