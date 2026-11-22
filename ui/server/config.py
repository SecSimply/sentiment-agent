from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_url: str = "http://localhost:8000"          # the agent service (private URL on Railway)
    ui_hmac_secret: SecretStr = SecretStr("")          # signs public lab-page URLs; required in production
    public_base_url: str | None = None                 # e.g. https://ui-production-xxxx.up.railway.app
    lab_db_path: str | None = None                     # override; otherwise <volume>/lab.db or ./lab.db
    allow_ephemeral_db: bool = False                   # escape hatch: run on Railway without a volume (data lost on redeploy)
    static_dir: str = "static"
    run_rate_per_min: int = 12                         # /api/run per API key

    max_pages: int = 200
    max_html_chars: int = 200_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
