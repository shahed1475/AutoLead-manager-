"""
service.py — EmailCampaignService: the native campaign state machine.

Responsibilities (Checkpoint 3B):
  * create / read campaigns (always DRAFT, never auto-RUNNING)
  * import a lead file (CSV / XLSX) -> normalized rows -> persistence
  * one optional per-campaign attachment (backend-only paths)
  * per-lead preparation on top of ai_brain.py (verbatim body + AI subject,
    or full AI subject+body)
  * the send loop, with the fail-closed TEST_MODE safety gate executed
    IMMEDIATELY before every email_sender.send_email() call
  * campaign- and lead-level idempotency (email_campaign_runs + per-lead SENT)
  * pause / resume and the DRAFT/READY/RUNNING/PAUSED/COMPLETED/FAILED lifecycle
  * statistics + activity/audit via the existing logging systems

This module NEVER sends email itself. Every send goes through the ONE existing
sender, `email_sender.send_email()`. n8n is not involved until Checkpoint 3C.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import ai_brain  # noqa: F401  (kept for parity / future direct use)
from .. import database as db
from .. import email_sender
from ..config import get_settings
from ..validators import is_valid_email
from . import ai, attachments, file_import, senders
from .safety import SafetyGateError, assert_send_allowed
from .senders import SenderUnavailable

logger = logging.getLogger(__name__)

# lifecycle: {from_status: {allowed to_status, ...}}
_TRANSITIONS: Dict[str, set] = {
    "DRAFT":     {"READY", "FAILED"},
    "READY":     {"DRAFT", "RUNNING", "FAILED"},
    "RUNNING":   {"PAUSED", "COMPLETED", "FAILED"},
    "PAUSED":    {"RUNNING", "FAILED", "DRAFT"},
    "COMPLETED": set(),
    "FAILED":    {"DRAFT", "READY"},
}

# lead statuses the send loop will act on (everything else is skipped / terminal)
_SENDABLE_LEAD_STATUSES = ("VALIDATED", "GENERATED")
# global-lead statuses that block a campaign send (mirrors autolead-outreach-safety)
_BLOCKING_GLOBAL_STATUSES = frozenset({"DO_NOT_CONTACT", "REPLIED", "SKIPPED"})

_TRUTHY = (True, 1, "1", "true", "True", "TRUE")


class EmailCampaignError(RuntimeError):
    """Raised for invalid campaign operations (bad transition, missing campaign, …)."""


def _truthy(v: Any) -> bool:
    return v in _TRUTHY


async def _feature_enabled() -> bool:
    if get_settings().email_campaigns_enabled:
        return True
    return _truthy(await db.get_setting("email_campaigns_enabled"))


async def is_feature_enabled() -> bool:
    """Public: is the Email Campaign module turned on? (Settings flag OR the
    app_settings 'email_campaigns_enabled' override — default False.)"""
    return await _feature_enabled()


def _test_subject(subject: str, lead_email: Optional[str]) -> str:
    return f"[TEST -> {lead_email or 'no-email'}] {subject}".strip()


def _test_body(body: str, lead_email: Optional[str]) -> str:
    banner = (
        f"[[ TEST MODE — generated for {lead_email or 'an unknown lead'}, redirected to the "
        f"campaign test recipient. No prospect was contacted. ]]"
    )
    return f"{banner}\n\n{body}"


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


class EmailCampaignService:
    """Stateless service object — all state lives in the DB. Safe to share."""

    # ── creation / read ────────────────────────────────────────────────────

    async def create_campaign(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = (data.get("name") or "").strip()
        if not name:
            raise EmailCampaignError("campaign name is required")

        recipient = (data.get("test_recipient") or "").strip() or "shahedalfahad20@gmail.com"
        if not is_valid_email(recipient):
            raise EmailCampaignError(f"test_recipient is not a valid email: {recipient!r}")

        sender_profile_id = data.get("sender_profile_id")
        if sender_profile_id is not None:
            if not await db.get_sender_profile(int(sender_profile_id)):
                raise EmailCampaignError(f"sender profile {sender_profile_id} not found")

        reply_to = (data.get("reply_to") or "").strip() or None
        if reply_to and not is_valid_email(reply_to):
            raise EmailCampaignError(f"reply_to is not a valid email: {reply_to!r}")

        payload = {
            "name": name,
            "description": (data.get("description") or "").strip() or None,
            # This build is TEST_MODE only. Production is a separate approved change.
            "test_mode": True,
            "test_recipient": recipient,
            "ai_enabled": bool(data.get("ai_enabled", True)),
            "from_name": (data.get("from_name") or "").strip() or None,
            "from_email": (data.get("from_email") or "").strip() or None,
            "sender_profile_id": int(sender_profile_id) if sender_profile_id is not None else None,
            "reply_to": reply_to,
        }
        if isinstance(data.get("config"), dict):
            payload["config"] = data["config"]

        cid = await db.create_email_campaign(payload)
        await db.log_email_campaign_activity(cid, "campaign_created", f"name={name!r}")
        return await self.get_campaign(cid)

    async def get_campaign(self, campaign_id: int) -> Dict[str, Any]:
        camp = await db.get_email_campaign(campaign_id)
        if not camp:
            raise EmailCampaignError(f"campaign {campaign_id} not found")
        return camp

    # fields that must NEVER reach the frontend / API responses
    _CAMPAIGN_HIDDEN = (
        "attachment_path", "attachment_filename", "attachment_size", "attachment_mime",
        "config_json",
    )
    # writable via PATCH — deliberately excludes test_mode / test_recipient / status /
    # every counter and every attachment field (those have dedicated endpoints)
    _CAMPAIGN_PATCHABLE = ("name", "description", "ai_enabled", "from_name", "from_email",
                           "sender_profile_id", "reply_to")

    @classmethod
    def _public_view(cls, camp: Dict[str, Any]) -> Dict[str, Any]:
        """Frontend-safe representation: attachment as metadata, no fs path, no secrets."""
        out = {k: v for k, v in dict(camp).items() if k not in cls._CAMPAIGN_HIDDEN}
        out["attachment"] = attachments.public_attachment_meta(camp)
        out["test_mode"] = _truthy(camp.get("test_mode"))
        out["ai_enabled"] = _truthy(camp.get("ai_enabled"))
        return out

    async def _public_view_full(self, camp: Dict[str, Any]) -> Dict[str, Any]:
        out = self._public_view(camp)
        out["sender"] = await senders.campaign_sender_view(camp)   # safe metadata or None
        return out

    async def get_campaign_public(self, campaign_id: int) -> Dict[str, Any]:
        return await self._public_view_full(await self.get_campaign(campaign_id))

    async def list_campaigns(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return await db.list_email_campaigns(limit=limit, offset=offset)

    async def list_campaigns_public(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return [await self._public_view_full(c)
                for c in await self.list_campaigns(limit=limit, offset=offset)]

    async def update_campaign(self, campaign_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH a campaign. Only DRAFT/READY campaigns are editable, and only the
        safe fields — never test_mode, test_recipient, status, or counters."""
        camp = await self.get_campaign(campaign_id)
        if camp["status"] not in ("DRAFT", "READY"):
            raise EmailCampaignError(
                f"cannot edit a campaign in status {camp['status']} (must be DRAFT or READY)"
            )
        patch: Dict[str, Any] = {}
        for k in self._CAMPAIGN_PATCHABLE:
            if k not in data:
                continue
            v = data[k]
            if k == "sender_profile_id":
                if v is None:
                    patch[k] = None
                else:
                    if not await db.get_sender_profile(int(v)):
                        raise EmailCampaignError(f"sender profile {v} not found")
                    patch[k] = int(v)
            elif k == "reply_to":
                rt = (str(v).strip() or None) if v is not None else None
                if rt and not is_valid_email(rt):
                    raise EmailCampaignError(f"reply_to is not a valid email: {rt!r}")
                patch[k] = rt
            elif v is not None:
                patch[k] = bool(v) if k == "ai_enabled" else str(v).strip() or None
        if not patch:
            raise EmailCampaignError("no editable fields supplied")
        await db.update_email_campaign(campaign_id, patch)
        await db.log_email_campaign_activity(
            campaign_id, "campaign_updated", ", ".join(sorted(patch))
        )
        return await self.get_campaign_public(campaign_id)

    # ── lead listing ──────────────────────────────────────────────────────

    # per-lead fields safe to return through the API (no raw_json, no fs paths)
    _LEAD_PUBLIC_FIELDS = (
        "lead_key", "email", "first_name", "last_name", "company",
        "body_source", "status", "status_detail", "ai_subject",
        "sent_at", "failure_reason", "created_at", "updated_at",
    )

    async def list_leads(self, campaign_id: int, *, status: Optional[str] = None,
                         offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        await self.get_campaign(campaign_id)
        rows = await db.get_email_campaign_leads(
            campaign_id, status=status, offset=offset, limit=limit
        )
        by_status = await db.count_email_campaign_leads_by_status(campaign_id)
        return {
            "leads": [{k: r.get(k) for k in self._LEAD_PUBLIC_FIELDS} for r in rows],
            "offset": offset,
            "limit": limit,
            "total": sum(by_status.values()),
            "by_status": by_status,
        }

    # ── lead import ────────────────────────────────────────────────────────

    async def import_leads(self, campaign_id: int, filename: str, data: bytes) -> Dict[str, Any]:
        camp = await self.get_campaign(campaign_id)
        if camp["status"] not in ("DRAFT", "READY"):
            raise EmailCampaignError(
                f"cannot import leads while campaign is {camp['status']} (must be DRAFT or READY)"
            )

        result = file_import.parse_lead_file(campaign_id, filename, data)

        rows = [
            {
                "lead_key": ld.lead_key,
                "email": ld.email,
                "first_name": ld.first_name,
                "last_name": ld.last_name,
                "company": ld.company,
                "raw": ld.raw,
                "body_source": ld.body_source,
                "provided_body": ld.provided_body,
                "status": ld.status,
                "status_detail": ld.status_detail,
            }
            for ld in result.leads
        ]
        inserted = await db.bulk_insert_email_campaign_leads(campaign_id, rows)
        await db.recount_email_campaign(campaign_id)

        summary = {
            "filename": filename,
            "total_rows": result.total_rows,
            "inserted": inserted,
            "skipped_existing": len(rows) - inserted,
            "valid": result.valid,
            "missing_email": result.missing_email,
            "invalid_email": result.invalid_email,
            "duplicates": result.duplicates,
            "warnings": result.warnings,
        }
        await db.log_email_campaign_activity(
            campaign_id, "leads_imported",
            f"file={filename!r} rows={result.total_rows} inserted={inserted} "
            f"valid={result.valid} missing={result.missing_email} "
            f"invalid={result.invalid_email} dup={result.duplicates}",
        )
        return summary

    # ── attachment ────────────────────────────────────────────────────────

    async def set_attachment(self, campaign_id: int, filename: str, data: bytes) -> Dict[str, Any]:
        await self.get_campaign(campaign_id)
        meta = attachments.save_campaign_attachment(campaign_id, filename, data)
        await db.update_email_campaign(campaign_id, meta)
        await db.log_email_campaign_activity(
            campaign_id, "attachment_set",
            f"filename={meta['attachment_filename']!r} size={meta['attachment_size']}",
        )
        return {
            "filename": meta["attachment_filename"],
            "size": meta["attachment_size"],
            "mime": meta["attachment_mime"],
            "present": True,
        }

    async def clear_attachment(self, campaign_id: int) -> None:
        camp = await self.get_campaign(campaign_id)
        path = camp.get("attachment_path")
        if path:
            try:
                import os

                if os.path.isfile(path):
                    os.remove(path)
            except OSError as exc:  # noqa: BLE001
                logger.warning("clear_attachment: could not remove %s: %s", path, exc)
        async with db.get_db() as conn:
            await conn.execute(
                "UPDATE email_campaigns SET attachment_filename=NULL, attachment_path=NULL, "
                "attachment_size=NULL, attachment_mime=NULL, updated_at=? WHERE id=?",
                db._now_naive_iso(), campaign_id,
            )
        await db.log_email_campaign_activity(campaign_id, "attachment_cleared", "")

    # ── preparation (AI / verbatim body) ──────────────────────────────────

    async def prepare_campaign(self, campaign_id: int) -> Dict[str, int]:
        """Run per-lead preparation for every VALIDATED lead. Idempotent — a lead
        already GENERATED is left alone."""
        camp = await self.get_campaign(campaign_id)
        generated = failed = 0
        for lead in await db.get_email_campaign_leads(campaign_id, status="VALIDATED", limit=100_000):
            updates, status = await ai.prepare_email(camp, lead)
            await db.update_email_campaign_lead(
                campaign_id, lead["lead_key"], {**updates, "status": status}
            )
            if status == "GENERATED":
                generated += 1
            else:
                failed += 1
                await db.log_email_campaign_activity(
                    campaign_id, "ai_generation_failed",
                    updates.get("status_detail", ""), level="WARNING",
                    lead_key=lead["lead_key"],
                )
        await db.recount_email_campaign(campaign_id)
        await db.log_email_campaign_activity(
            campaign_id, "campaign_prepared", f"generated={generated} failed={failed}"
        )
        return {"generated": generated, "failed": failed}

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def _transition(self, campaign_id: int, to_status: str, *, detail: str = "") -> None:
        camp = await self.get_campaign(campaign_id)
        cur = camp["status"]
        if to_status == cur:
            return
        if to_status not in _TRANSITIONS.get(cur, set()):
            raise EmailCampaignError(f"invalid transition {cur} -> {to_status}")
        patch: Dict[str, Any] = {"status": to_status}
        if to_status == "RUNNING" and not camp.get("started_at"):
            patch["started_at"] = db._now_naive_iso()
        if to_status in ("COMPLETED", "FAILED"):
            patch["completed_at"] = db._now_naive_iso()
        await db.update_email_campaign(campaign_id, patch)
        await db.log_email_campaign_activity(
            campaign_id, f"status_{to_status.lower()}", detail or f"{cur} -> {to_status}"
        )

    async def mark_ready(self, campaign_id: int) -> Dict[str, Any]:
        await db.recount_email_campaign(campaign_id)
        camp = await self.get_campaign(campaign_id)
        if (camp["valid_leads"] or 0) < 1:
            raise EmailCampaignError("campaign has no valid leads — import leads first")
        await self._transition(campaign_id, "READY", detail="marked ready for sending")
        return await self.get_campaign(campaign_id)

    async def revert_to_draft(self, campaign_id: int) -> Dict[str, Any]:
        await self._transition(campaign_id, "DRAFT", detail="reverted to draft")
        return await self.get_campaign(campaign_id)

    async def pause(self, campaign_id: int) -> Dict[str, Any]:
        await self._transition(campaign_id, "PAUSED", detail="paused by request")
        return await self.get_campaign(campaign_id)

    async def start(
        self,
        campaign_id: int,
        idempotency_key: str,
        *,
        background: bool = True,
    ) -> Dict[str, Any]:
        """Start (from READY) or resume (from PAUSED) sending.

        Idempotent on `idempotency_key`: a repeat call with the same key returns
        the same run and does not launch a second send loop.
        """
        if not await _feature_enabled():
            raise EmailCampaignError(
                "email campaigns are disabled — set app_settings 'email_campaigns_enabled'"
            )
        if not idempotency_key or not idempotency_key.strip():
            raise EmailCampaignError("idempotency_key is required to start a campaign")

        camp = await self.get_campaign(campaign_id)
        if camp["status"] not in ("READY", "PAUSED", "RUNNING"):
            raise EmailCampaignError(f"cannot start a campaign in status {camp['status']}")

        # Idempotent no-op: the campaign is already sending. Hand back the run for
        # this key (creating the record if the caller is retrying) without
        # launching a second send loop.
        if camp["status"] == "RUNNING":
            run = await db.create_email_campaign_run(
                campaign_id, idempotency_key, {"status": "SENDING"}
            )
            await db.log_email_campaign_activity(
                campaign_id, "start_ignored_already_running", f"key={idempotency_key}"
            )
            return run

        await db.recount_email_campaign(campaign_id)
        camp = await self.get_campaign(campaign_id)
        counts = await db.count_email_campaign_leads_by_status(campaign_id)
        sendable = sum(counts.get(s, 0) for s in _SENDABLE_LEAD_STATUSES)
        if sendable < 1:
            raise EmailCampaignError("no leads left to send")

        # Pre-flight: the selected sender must be usable BEFORE we go RUNNING.
        # A named-but-unavailable profile blocks here (409) — never a fallback.
        try:
            await senders.resolve_transport(camp)
        except SenderUnavailable as exc:
            raise EmailCampaignError(f"cannot start — selected sender is unavailable: {exc}")

        run = await db.create_email_campaign_run(
            campaign_id, idempotency_key, {"status": "PENDING", "batch_size": sendable}
        )
        await self._transition(campaign_id, "RUNNING", detail=f"run={run['id']} key={idempotency_key}")

        if background:
            from ..queue_worker import get_queue  # local import: optional dependency

            q = get_queue()
            if q is not None:
                await q.enqueue(
                    "email_campaign_send",
                    {"campaign_id": campaign_id, "run_id": run["id"]},
                    lambda p: self.run_batch(p["campaign_id"], p["run_id"]),
                )
                return run
        # no queue (tests / CLI) — run inline
        await self.run_batch(campaign_id, run["id"])
        return await db.get_email_campaign_run(run["id"])

    async def resume(self, campaign_id: int, idempotency_key: str, *, background: bool = True):
        camp = await self.get_campaign(campaign_id)
        if camp["status"] != "PAUSED":
            raise EmailCampaignError(f"can only resume a PAUSED campaign (it is {camp['status']})")
        return await self.start(campaign_id, idempotency_key, background=background)

    # ── the send loop ─────────────────────────────────────────────────────

    async def run_batch(self, campaign_id: int, run_id: int) -> Dict[str, Any]:
        """Process every sendable lead once. Honors pause. Fail-closed on the gate.
        Marks a lead SENT/SEND_FAILED/SEND_BLOCKED/DO_NOT_CONTACT only after the
        outcome is known. Safe to re-run — SENT leads are skipped."""
        camp = await db.get_email_campaign(campaign_id)
        if not camp:
            raise EmailCampaignError(f"campaign {campaign_id} not found")
        if camp["status"] != "RUNNING":
            await db.log_email_campaign_activity(
                campaign_id, "batch_skipped", f"campaign is {camp['status']}, not RUNNING"
            )
            return await db.get_email_campaign_run(run_id) or {}

        # ── resolve the sender profile — fail closed, NEVER fall back ──
        try:
            transport = await senders.resolve_transport(camp)
        except SenderUnavailable as exc:
            await db.update_email_campaign_run(
                run_id, {"status": "FAILED", "error": f"sender unavailable: {exc}"[:400]}
            )
            await db.update_email_campaign(campaign_id, {"status": "FAILED",
                                                        "completed_at": db._now_naive_iso()})
            await db.log_email_campaign_activity(
                campaign_id, "sender_unavailable", str(exc)[:400], level="ERROR"
            )
            return await db.get_email_campaign_run(run_id) or {}

        await db.log_email_campaign_activity(
            campaign_id, "sender_selected",
            f"provider={transport.provider} from={transport.email_address} "
            f"profile_id={camp.get('sender_profile_id') or 'global'}",
        )
        campaign_reply_to = (camp.get("reply_to") or "").strip() or None

        await db.update_email_campaign_run(run_id, {"status": "SENDING"})
        processed = sent = failed = blocked = 0

        try:
            att = attachments.read_campaign_attachment(camp)
            att_list = (
                [{"filename": att[0], "content": att[1], "mime": att[2]}] if att else None
            )

            leads = await db.get_email_campaign_leads(campaign_id, limit=100_000)
            for lead in leads:
                if lead["status"] not in _SENDABLE_LEAD_STATUSES:
                    continue

                # honor pause / external status change
                fresh_camp = await db.get_email_campaign(campaign_id)
                if not fresh_camp or fresh_camp["status"] != "RUNNING":
                    await db.log_email_campaign_activity(
                        campaign_id, "batch_paused",
                        f"stopped at lead {lead['lead_key']} (campaign "
                        f"{fresh_camp['status'] if fresh_camp else 'gone'})",
                    )
                    await db.update_email_campaign_run(
                        run_id,
                        {"status": "PAUSED", "processed_count": processed,
                         "sent_count": sent, "failed_count": failed},
                    )
                    return await db.get_email_campaign_run(run_id) or {}
                camp = fresh_camp

                # re-read the lead — idempotency: a concurrent run may have sent it
                lead = await db.get_email_campaign_lead(campaign_id, lead["lead_key"])
                if not lead or lead["status"] == "SENT":
                    continue

                processed += 1
                lead_key = lead["lead_key"]
                lead_email = lead.get("email")

                # ── DO_NOT_CONTACT / terminal re-check against the global lead ──
                gid = lead.get("lead_id")
                if gid:
                    g = await db.get_lead_by_id(gid)
                    if g and (g.get("status") or "").upper() in _BLOCKING_GLOBAL_STATUSES:
                        await db.update_email_campaign_lead(
                            campaign_id, lead_key,
                            {"status": "DO_NOT_CONTACT",
                             "failure_reason": f"global lead {gid} is {g['status']}"},
                        )
                        await db.log_email_campaign_activity(
                            campaign_id, "lead_blocked",
                            f"global lead {gid} status {g['status']}",
                            level="WARNING", lead_key=lead_key,
                        )
                        continue

                # ── prepare if needed ──
                if lead["status"] != "GENERATED" or not lead.get("ai_subject"):
                    updates, status = await ai.prepare_email(camp, lead)
                    await db.update_email_campaign_lead(
                        campaign_id, lead_key, {**updates, "status": status}
                    )
                    if status != "GENERATED":
                        failed += 1
                        await db.log_email_campaign_activity(
                            campaign_id, "ai_generation_failed",
                            updates.get("status_detail", ""), level="WARNING", lead_key=lead_key,
                        )
                        continue
                    lead = await db.get_email_campaign_lead(campaign_id, lead_key)

                subject = (lead.get("ai_subject") or "").strip() or "Following up"
                body = lead.get("ai_body") or lead.get("provided_body") or ""

                # ── compute the destination address ──
                test_mode = _truthy(camp.get("test_mode"))
                send_to = camp.get("test_recipient") if test_mode else lead_email

                # ── FAIL-CLOSED SAFETY GATE — immediately before the send ──
                try:
                    assert_send_allowed(camp, send_to or "", lead_email)
                except SafetyGateError as exc:
                    blocked += 1
                    await db.update_email_campaign_lead(
                        campaign_id, lead_key,
                        {"status": "SEND_BLOCKED", "failure_reason": str(exc)[:400]},
                    )
                    await db.log_email_campaign_activity(
                        campaign_id, "send_blocked", str(exc)[:400],
                        level="ERROR", lead_key=lead_key,
                    )
                    continue

                if test_mode:
                    subject = _test_subject(subject, lead_email)
                    body = _test_body(body, lead_email)

                idem = f"{campaign_id}:{lead_key}"
                result = await transport.send(
                    send_to, subject, body,
                    attachments=att_list, reply_to=campaign_reply_to,
                )

                if result.ok:
                    sent += 1
                    await db.update_email_campaign_lead(
                        campaign_id, lead_key,
                        {"status": "SENT", "sent_at": db._now_naive_iso(),
                         "idempotency_key": idem, "message_id": result.message_id,
                         "failure_reason": ""},
                    )
                    await db.log_email_campaign_activity(
                        campaign_id, "email_sent",
                        f"to={send_to} via {transport.provider} "
                        f"from={transport.email_address} (test_mode={test_mode})",
                        lead_key=lead_key,
                    )
                    if gid:
                        await db.log_campaign_action(gid, "EMAIL_CAMPAIGN", "SEND", True)
                else:
                    failed += 1
                    await db.update_email_campaign_lead(
                        campaign_id, lead_key,
                        {"status": "SEND_FAILED",
                         "failure_reason": (result.error or "send failed")[:400]},
                    )
                    await db.log_email_campaign_activity(
                        campaign_id, "email_failed",
                        f"to={send_to} via {transport.provider}: {(result.error or '')[:200]}",
                        level="ERROR", lead_key=lead_key,
                    )

                await db.update_email_campaign_run(
                    run_id,
                    {"processed_count": processed, "sent_count": sent, "failed_count": failed},
                )

        except Exception as exc:  # noqa: BLE001 — a batch crash must not wedge the campaign
            logger.error("run_batch failed for campaign %s: %s", campaign_id, exc, exc_info=True)
            await db.update_email_campaign_run(
                run_id, {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:400]}
            )
            await db.update_email_campaign(campaign_id, {"status": "FAILED",
                                                        "completed_at": db._now_naive_iso()})
            await db.log_email_campaign_activity(
                campaign_id, "batch_failed", f"{type(exc).__name__}: {exc}"[:400], level="ERROR"
            )
            return await db.get_email_campaign_run(run_id) or {}

        await db.recount_email_campaign(campaign_id)
        _rc = await db.count_email_campaign_leads_by_status(campaign_id)
        remaining = sum(_rc.get(s, 0) for s in _SENDABLE_LEAD_STATUSES)
        run_patch = {
            "status": "COMPLETED", "processed_count": processed,
            "sent_count": sent, "failed_count": failed, "completed_at": db._now_naive_iso(),
        }
        await db.update_email_campaign_run(run_id, run_patch)

        # only auto-complete the campaign if it wasn't paused/failed mid-flight
        cur = await db.get_email_campaign(campaign_id)
        if cur and cur["status"] == "RUNNING" and remaining == 0:
            await db.update_email_campaign(
                campaign_id, {"status": "COMPLETED", "completed_at": db._now_naive_iso()}
            )
            await db.log_email_campaign_activity(
                campaign_id, "campaign_completed",
                f"processed={processed} sent={sent} failed={failed} blocked={blocked}",
            )
        else:
            await db.log_email_campaign_activity(
                campaign_id, "batch_finished",
                f"processed={processed} sent={sent} failed={failed} blocked={blocked} "
                f"remaining={remaining}",
            )
        return await db.get_email_campaign_run(run_id) or {}

    # ── statistics / activity ─────────────────────────────────────────────

    async def get_stats(self, campaign_id: int) -> Dict[str, Any]:
        camp = await self.get_campaign(campaign_id)
        by_status = await db.count_email_campaign_leads_by_status(campaign_id)
        runs = await db.list_email_campaign_runs(campaign_id)
        return {
            "campaign_id": campaign_id,
            "status": camp["status"],
            "test_mode": _truthy(camp.get("test_mode")),
            "totals": {
                "total_leads": camp.get("total_leads", 0),
                "valid_leads": camp.get("valid_leads", 0),
                "pending": sum(by_status.get(s, 0) for s in _SENDABLE_LEAD_STATUSES),
                "sent": by_status.get("SENT", 0),
                "send_failed": by_status.get("SEND_FAILED", 0),
                "send_blocked": by_status.get("SEND_BLOCKED", 0),
                "ai_failed": by_status.get("AI_GENERATION_FAILED", 0),
                "do_not_contact": by_status.get("DO_NOT_CONTACT", 0),
                "replied": camp.get("replied_count", 0),
            },
            "by_lead_status": by_status,
            "runs": len(runs),
            "last_run": self._public_run(runs[0]) if runs else None,
        }

    @staticmethod
    def _public_run(run: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """A run summary safe for the API — no internal orchestration fields
        (n8n_trigger_ref, idempotency_key, raw row id)."""
        if not run:
            return None
        return {
            "status": run.get("status"),
            "batch_size": run.get("batch_size"),
            "processed_count": run.get("processed_count"),
            "sent_count": run.get("sent_count"),
            "failed_count": run.get("failed_count"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
        }

    async def get_activity(self, campaign_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        await self.get_campaign(campaign_id)
        return await db.get_email_campaign_activity(campaign_id, limit=limit)

    # ── optional n8n preparation callback ─────────────────────────────────

    async def apply_preparation_callback(
        self, campaign_id: int, run_id: int, leads: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Ingest per-lead subject/body PREPARED by the optional n8n workflow.

        This NEVER sends and NEVER marks a lead SENT — it only fills ai_subject /
        ai_body and moves VALIDATED -> GENERATED. The native send loop + safety
        gate stay authoritative. Idempotent: re-applying the same payload is a
        no-op for already-GENERATED leads.
        """
        camp = await self.get_campaign(campaign_id)
        run = await db.get_email_campaign_run(run_id)
        if not run or run["campaign_id"] != campaign_id:
            raise EmailCampaignError("run does not belong to this campaign")

        applied = skipped = 0
        for item in leads or []:
            lead_key = (item or {}).get("lead_key")
            if not lead_key:
                skipped += 1
                continue
            lead = await db.get_email_campaign_lead(campaign_id, lead_key)
            if not lead or lead["status"] not in ("VALIDATED", "GENERATED"):
                skipped += 1
                continue
            subject = (item.get("ai_subject") or "").strip()
            body = (item.get("ai_body") or "").strip()
            if not subject or not body:
                skipped += 1
                continue
            await db.update_email_campaign_lead(campaign_id, lead_key, {
                "ai_subject": subject, "ai_body": body,
                "body_source": lead.get("body_source") or "ai",
                "status": "GENERATED", "status_detail": "prepared by n8n",
            })
            applied += 1
        await db.recount_email_campaign(campaign_id)
        await db.log_email_campaign_activity(
            campaign_id, "n8n_preparation_applied",
            f"run={run_id} applied={applied} skipped={skipped}",
        )
        return {"applied": applied, "skipped": skipped}


# ── module singleton ─────────────────────────────────────────────────────

_service: Optional[EmailCampaignService] = None


def get_email_campaign_service() -> EmailCampaignService:
    global _service
    if _service is None:
        _service = EmailCampaignService()
    return _service
