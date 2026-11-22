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

    # Limits
    max_urls: int = Field(5, ge=1, le=20)
    max_agent_steps: int = Field(8, ge=2, le=30)  # recursion limit for agentic gather
    max_docs: int = Field(10, ge=1, le=50)
    max_chars_per_doc: int = 8000  # extra clamp before prompting


@lru_cache
def get_settings() -> Settings:
    return Settings()
