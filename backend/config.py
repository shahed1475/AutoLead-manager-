from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    app_name: str = "AutoLead Marketing Engine"
    debug:    bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    # SQLite — a single file, no external DB server. Ideal for a desktop app.
    database_path: str = str(BASE_DIR / "data" / "leads.db")

    # ── Ollama (local AI) ─────────────────────────────────────────────────────
    # llama3.1:8b — an 8B-parameter model, in the weight class the README's
    # documented setup instruction ("ollama pull llama3") intends. The prior
    # default, qwen2.5:1.5b, is too small to reliably follow multi-rule outreach-
    # copy instructions (produced bracket placeholders, raw HTML, and hallucinated
    # details in production — see ai_brain.py's content quality gate, which is a
    # second line of defense regardless of model size).
    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "llama3.1:8b"
    ollama_timeout:  int = 120
    # Concurrent Ollama generations allowed (1 = safe for small GPUs; see ai_brain.ollama_slot)
    ollama_max_parallel: int = 1

    # ── SMTP ─────────────────────────────────────────────────────────────────
    smtp_host:       str = "smtp.gmail.com"
    smtp_port:       int = 587
    smtp_username:   str = ""
    smtp_password:   str = ""
    smtp_from_name:  str = ""
    smtp_from_email: str = ""

    # ── IMAP reply detection ──────────────────────────────────────────────────
    imap_host:     str  = ""
    imap_port:     int  = 993
    imap_username: str  = ""
    imap_password: str  = ""
    imap_ssl:      bool = True

    # ── WhatsApp ─────────────────────────────────────────────────────────────
    whatsapp_wait_time:  int  = 15
    whatsapp_close_tab:  bool = True

    # ── Scheduler ────────────────────────────────────────────────────────────
    schedule_hour:        int  = 9
    daily_email_limit:    int  = 50
    daily_whatsapp_limit: int  = 20

    # ── Lead Search Automation ───────────────────────────────────────────────
    automation_enabled:          bool = False
    automation_daily_limit:      int  = 500
    automation_start_time:       str  = "07:00"
    automation_timezone:         str  = ""   # empty = this machine's zone (automation/config.py)
    automation_duration_hours:   int  = 4
    automation_per_item_target:  int  = 40
    automation_max_retries:      int  = 2
    followup_delay_days:  int  = 3
    auto_send_enabled:    bool = False

    # ── Email Campaigns (PopupGenix module) ───────────────────────────────────
    # Dark until the module is built + tested. DB override: app_settings
    # 'email_campaigns_enabled'. n8n connection settings live in app_settings
    # (n8n_base_url, n8n_form_webhook_id, n8n_api_key[enc], n8n_callback_secret[enc]).
    email_campaigns_enabled: bool = False

    # ── Sender Profiles / Gmail OAuth2 (Checkpoint 4) ─────────────────────────
    # Prefer app_settings (google_oauth_client_secret is auto-encrypted there).
    # These are only a fallback so a fresh dev box can set them via .env.local.
    google_oauth_client_id:     str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri:  str = "http://localhost:8001/api/email-senders/gmail/callback"
    frontend_base_url:          str = "http://localhost:5173"

    # ── Scraper ───────────────────────────────────────────────────────────────
    scraper_headless:   bool  = True
    scraper_delay_min:  float = 2.0
    scraper_delay_max:  float = 5.0

    # ── Company DNA ───────────────────────────────────────────────────────────
    company_dna_path: str = str(BASE_DIR / "company_dna.txt")

    # ── Lead enrichment ───────────────────────────────────────────────────────
    enrichment_enabled: bool = True
    enrichment_timeout: int  = 12

    # ── Parallel job queue ────────────────────────────────────────────────────
    queue_workers: int = 4

    # ── Contact Verification (Checkpoint 5C) ────────────────────────────────
    # Deterministic email + phone verification (syntax / role / disposable /
    # MX+A DNS lookup / phonenumbers). No SMTP probe, no paid API, no LLM.
    # DB override: app_settings 'contact_verification_enabled'. Default OFF.
    contact_verification_enabled:      bool  = False
    verification_dns_timeout_seconds:  float = 3.0     # per DNS query
    verification_dns_lifetime_seconds: float = 5.0     # total incl. retries
    verification_max_leads_per_run:    int   = 2000

    # ── Lead Discovery (Phase 1 — Discovery Planner) ─────────────────────────
    discovery_quick_max_sources:     int = 3
    discovery_quick_max_variants:    int = 2
    discovery_campaign_max_variants: int = 4
    # Post-discovery enrichment + scoring (Lead Search Upgrade, 2026-09-04):
    # after merge/dedup, run email_finder (website-but-no-email) then
    # website analysis + AI enrichment + lead scoring. Shared by manual Quick
    # Search and Lead Search Automation.
    discovery_enrichment_enabled:    bool = True
    # Research handoff (Lead Search Upgrade §17-20): after enrichment+scoring,
    # 'automatic' mode queues eligible leads for the existing Research Agent.
    research_handoff_mode:           str  = "manual"      # 'manual' | 'automatic'
    research_handoff_min_score:      int  = 60
    research_handoff_max_per_batch:  int  = 25

    # ── Browser Research Agent ───────────────────────────────────────────────
    research_agent_headless:                 bool  = False
    research_agent_max_actions_per_lead:      int   = 12
    research_agent_max_searches_per_lead:     int   = 4
    research_agent_max_pages_per_lead:        int   = 5
    research_agent_max_time_per_lead_seconds: int   = 180
    research_agent_max_total_leads:           int   = 20
    research_agent_max_consecutive_failures:  int   = 3
    research_agent_max_geographic_units:      int   = 5
    research_agent_page_timeout_ms:           int   = 20000
    research_agent_save_to_leads:             bool  = True
    research_agent_max_decision_makers:       int   = 5

    model_config = {
        "env_file":          str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra":             "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
