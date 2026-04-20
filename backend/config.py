from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    app_name: str = "AutoLead Marketing Engine"
    debug: bool = False

    database_path: str = str(BASE_DIR / "data" / "leads.db")

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    ollama_timeout: int = 120

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_name: str = ""
    smtp_from_email: str = ""

    whatsapp_wait_time: int = 15
    whatsapp_close_tab: bool = True

    schedule_hour: int = 9
    daily_email_limit: int = 50
    daily_whatsapp_limit: int = 20

    scraper_headless: bool = True
    scraper_delay_min: float = 2.0
    scraper_delay_max: float = 5.0

    company_dna_path: str = str(BASE_DIR / "company_dna.txt")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
