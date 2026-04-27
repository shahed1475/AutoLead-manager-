from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    app_name: str = "AutoLead Marketing Engine"
    debug:    bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    # Set DATABASE_URL env var to point to PostgreSQL.
    # Local default assumes docker-compose postgres service.
    database_url: str = "postgresql://autolead:autolead_secret@localhost:5432/autolead"

    # Legacy SQLite path kept for migration utility only
    database_path: str = str(BASE_DIR / "data" / "leads.db")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Connection pool ───────────────────────────────────────────────────────
    db_pool_min: int = 5
    db_pool_max: int = 20

    # ── Ollama (local AI) ─────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "qwen2.5:1.5b"
    ollama_timeout:  int = 120

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
    followup_delay_days:  int  = 3
    auto_send_enabled:    bool = False

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

    model_config = {
        "env_file":          str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra":             "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
