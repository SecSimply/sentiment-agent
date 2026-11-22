"""Document gathering via the fetch MCP service. This module is the ONLY path internet content takes."""
import json
import logging
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.errors import GraphRecursionError

from .config import Settings
from .prompts import GATHER_SYSTEM

log = logging.getLogger(__name__)
ALLOWED_TOOLS = {"fetch_page", "search_news"}


class SourceUnavailable(RuntimeError):
    """The document-retrieval tools failed and nothing usable was collected."""


class Gatherer(Protocol):
    async def fetch_urls(self, urls: list[str]) -> list[dict]: ...
    async def research(self, query: str) -> list[dict]: ...


def parse_tool_output(content: Any) -> list[dict]:
    """MCP tool output may be a JSON string, content blocks, or already-parsed data."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return []
    if isinstance(content, dict):
        if "source_url" in content:
            return [content]
        return []
    if isinstance(content, list):
        out: list[dict] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                out.extend(parse_tool_output(item.get("text", "")))
            elif isinstance(item, dict) and "source_url" in item:
                out.append(item)
        return out
    return []


def _scan(messages) -> tuple[list[dict], list[str]]:
    """Split ToolMessages into parsed documents and error texts."""
    docs: list[dict] = []
    errors: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            if m.status == "error":
                errors.append(str(m.content)[:300])
            else:
                docs.extend(parse_tool_output(m.content))
    return docs, errors


class McpGatherer:
    def __init__(self, settings: Settings, llm: BaseChatModel | None = None, tools: list | None = None):
        self._s = settings
        self._llm = llm
        self._client = MultiServerMCPClient(
            {
                "fetch": {
                    "transport": "streamable_http",
                    "url": settings.mcp_url,  # fixed by config; the model can never choose the server
                    "headers": {"Authorization": f"Bearer {settings.mcp_shared_secret.get_secret_value()}"},
                }
            }
        )
        self._tools = self._prepare(tools) if tools is not None else None  # loaded lazily from MCP when None

    @staticmethod
    def _prepare(tools: list) -> list:
        # Turn tool failures into error-status ToolMessages so the gather loop can react instead of crashing.
        for t in tools:
            t.handle_tool_error = True
        return tools

    async def _get_tools(self):
        if self._tools is None:
            tools = await self._client.get_tools()
            self._tools = self._prepare([t for t in tools if t.name in ALLOWED_TOOLS])  # allowlist, ignore extras
        return self._tools

    async def fetch_urls(self, urls: list[str]) -> list[dict]:
        tool = next((t for t in await self._get_tools() if t.name == "fetch_page"), None)
        if tool is None:
            raise RuntimeError("fetch_page tool not available from MCP server")
        docs: list[dict] = []
        for url in urls[: self._s.max_urls]:
            try:
                docs.extend(parse_tool_output(await tool.ainvoke({"url": url})))
            except Exception as exc:  # one bad URL shouldn't sink the batch
                log.warning("fetch failed for %s: %s", url, type(exc).__name__)
        return docs[: self._s.max_docs]

    async def research(self, query: str) -> list[dict]:
        """Agentic gather. The agent's own text is discarded; only structured tool results are kept.

        Fails fast instead of looping on a broken tool, and keeps partial results if the step limit hits.
        """
        if self._llm is None:
            raise RuntimeError("LLM required for agentic gather")
        agent = create_agent(self._llm, await self._get_tools(), system_prompt=GATHER_SYSTEM)
        last: dict | None = None
        try:
            async for state in agent.astream(
                {"messages": [HumanMessage(f"Collect sources for sentiment analysis of: {query}")]},
                config={"recursion_limit": self._s.max_agent_steps},
                stream_mode="values",
            ):
                last = state
                docs, errors = _scan(state["messages"])
                # Stop on the first error if nothing was collected, or once errors repeat.
                if errors and (not docs or len(errors) >= 2):
                    break
        except GraphRecursionError:
            log.warning("agentic gather hit the step limit; using documents collected so far")

        docs, errors = _scan(last["messages"]) if last else ([], [])
        if not docs and errors:
            log.error("gather tools failed: %s", errors[-1])
            raise SourceUnavailable(errors[-1])
        return docs[: self._s.max_docs]
