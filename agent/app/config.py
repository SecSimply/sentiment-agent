from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (the only outbound internet dependency of this service)
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0

    # Fetch MCP microservice (private network on Railway)
    mcp_url: str = "http://localhost:5099/mcp"
    mcp_shared_secret: SecretStr = SecretStr("")

    # Inbound auth for this API
    agent_api_key: SecretStr = SecretStr("")

    # Show the exact analyst prompt in the trace stream (useful for debugging injection; exposes the system prompt to key holders)
    expose_prompt: bool = True

    # Cost / abuse guards (single instance, in memory)
    rate_per_min: int = Field(20, ge=1)
    daily_run_cap: int = Field(500, ge=1)
    max_concurrent_runs: int = Field(3, ge=1)
    auth_fail_limit_per_min: int = Field(10, ge=1)

    # Limits
    max_urls: int = Field(5, ge=1, le=20)
    max_agent_steps: int = Field(8, ge=2, le=30)  # recursion limit for agentic gather
    max_docs: int = Field(10, ge=1, le=50)
    max_chars_per_doc: int = 8000  # extra clamp before prompting

    @property
    def fetch_base_url(self) -> str:
        """Base URL of the fetch service (for its plain-HTTP lab endpoint), derived from the MCP URL."""
        return self.mcp_url.rsplit("/mcp", 1)[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()
