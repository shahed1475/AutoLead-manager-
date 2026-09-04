"""
email_campaigns — PopupGenix Email Campaign module.

n8n V1.1 is used (from Checkpoint 3C onward) for campaign preparation /
orchestration only. AutoLead's `email_sender.send_email()` remains the ONE
production email sender. This package NEVER sends email itself — it prepares
per-lead subject/body, enforces the fail-closed TEST_MODE safety gate, and
hands each vetted send to `email_sender.send_email()`.

Feature-flagged: `app_settings['email_campaigns_enabled']` (default false).
"""
from .gmail_oauth import GoogleOAuthNotConfigured
from .n8n_client import N8nNotConfigured, N8nUnavailable
from .safety import SafetyGateError, assert_send_allowed
from .senders import SenderUnavailable
from .service import EmailCampaignError, EmailCampaignService, get_email_campaign_service

__all__ = [
    "SafetyGateError",
    "assert_send_allowed",
    "EmailCampaignError",
    "EmailCampaignService",
    "get_email_campaign_service",
    "N8nNotConfigured",
    "N8nUnavailable",
    "SenderUnavailable",
    "GoogleOAuthNotConfigured",
]
