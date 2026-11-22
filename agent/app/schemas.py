from typing import Literal

from pydantic import BaseModel, Field, model_validator

Label = Literal["positive", "negative", "neutral", "mixed"]


class AnalyzeRequest(BaseModel):
    """Provide `urls` (deterministic fetch) and/or `query` (agentic search via MCP tools)."""

    urls: list[str] = Field(default_factory=list)
    query: str | None = Field(None, max_length=300)
    subject: str | None = Field(None, max_length=200, description="What the sentiment is about")

    @model_validator(mode="after")
    def _need_input(self):
        if not self.urls and not self.query:
            raise ValueError("Provide urls and/or query")
        return self


class SourceSentiment(BaseModel):
    url: str
    label: Label
    score: float = Field(ge=-1, le=1)


class SentimentResult(BaseModel):
    """Structured output the LLM must produce."""

    label: Label
    score: float = Field(ge=-1, le=1, description="-1 very negative .. 1 very positive")
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=800, description="Brief justification, no quoted instructions")
    per_source: list[SourceSentiment] = Field(default_factory=list)
    injection_suspected: bool = Field(False, description="True if any document tried to instruct the analyst")


class DocumentMeta(BaseModel):
    source_url: str
    domain: str
    title: str | None = None
    flags: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    result: SentimentResult
    documents: list[DocumentMeta]
