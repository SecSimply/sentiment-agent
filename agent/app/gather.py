"""Document gathering via the fetch MCP service. This module is the ONLY path internet content takes."""
import json
import logging
import time
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.errors import GraphRecursionError

from .config import Settings
from .events import Emit, emit_check, emit_doc, noop_emit, refusal_code
from .prompts import GATHER_SYSTEM

log = logging.getLogger(__name__)
ALLOWED_TOOLS = {"fetch_page", "search_news"}


class SourceUnavailable(RuntimeError):
    """The document-retrieval tools failed and nothing usable was collected."""


class Gatherer(Protocol):
    async def fetch_urls(self, urls: list[str], emit: Emit = noop_emit) -> list[dict]: ...
    async def research(self, query: str, emit: Emit = noop_emit) -> list[dict]: ...


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


def _text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


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

    async def _report_failure(self, emit: Emit, doc: str | None, message: str) -> None:
        code = refusal_code(message)
        if code:
            await emit_check(emit, doc, code, "block", message.split("]", 1)[-1].strip()[:200] or "refused")
        else:
            await emit_check(emit, doc, "fetch.failed", "block", "the fetch tool reported an error")

    async def fetch_urls(self, urls: list[str], emit: Emit = noop_emit) -> list[dict]:
        tool = next((t for t in await self._get_tools() if t.name == "fetch_page"), None)
        if tool is None:
            raise RuntimeError("fetch_page tool not available from MCP server")
        docs: list[dict] = []
        for url in urls[: self._s.max_urls]:
            await emit("tool_call", tool="fetch_page", args={"url": url})
            t0 = time.monotonic()
            try:
                res = await tool.ainvoke({"name": "fetch_page", "args": {"url": url}, "id": "call", "type": "tool_call"})
                is_err = isinstance(res, ToolMessage) and res.status == "error"
                content = res.content if isinstance(res, ToolMessage) else res
            except Exception as exc:  # one bad URL shouldn't sink the batch
                log.warning("fetch failed for %s: %s", url, type(exc).__name__)
                is_err, content = True, str(exc)
            ms = int((time.monotonic() - t0) * 1000)
            if is_err:
                await emit("tool_result", tool="fetch_page", ok=False, ms=ms, n_docs=0)
                await self._report_failure(emit, url, _text(content))
                continue
            got = parse_tool_output(content)
            await emit("tool_result", tool="fetch_page", ok=True, ms=ms, n_docs=len(got))
            for d in got:
                await emit_doc(emit, d)
            docs.extend(got)
        return docs[: self._s.max_docs]

    async def research(self, query: str, emit: Emit = noop_emit) -> list[dict]:
        """Agentic gather. The agent's own text is discarded; only structured tool results are kept.

        Fails fast instead of looping on a broken tool, and keeps partial results if the step limit hits.
        """
        if self._llm is None:
            raise RuntimeError("LLM required for agentic gather")
        agent = create_agent(self._llm, await self._get_tools(), system_prompt=GATHER_SYSTEM)
        last: dict | None = None
        seen = 0
        started: dict[str, tuple[float, str]] = {}
        steps = 0
        try:
            async for state in agent.astream(
                {"messages": [HumanMessage(f"Collect sources for sentiment analysis of: {query}")]},
                config={"recursion_limit": self._s.max_agent_steps},
                stream_mode="values",
            ):
                last = state
                msgs = state["messages"]
                for m in msgs[seen:]:
                    if isinstance(m, AIMessage) and m.tool_calls:
                        steps += 1
                        await emit("agent_step", step=steps, limit=self._s.max_agent_steps)
                        for tc in m.tool_calls:
                            started[tc["id"]] = (time.monotonic(), tc["name"])
                            await emit("tool_call", tool=tc["name"], args=tc.get("args", {}))
                    elif isinstance(m, ToolMessage):
                        t0, name = started.get(m.tool_call_id, (time.monotonic(), m.name or "tool"))
                        ms = int((time.monotonic() - t0) * 1000)
                        if m.status == "error":
                            await emit("tool_result", tool=name, ok=False, ms=ms, n_docs=0)
                            await self._report_failure(emit, None, _text(m.content))
                        else:
                            got = parse_tool_output(m.content)
                            await emit("tool_result", tool=name, ok=True, ms=ms, n_docs=len(got))
                            for d in got:
                                await emit_doc(emit, d)
                seen = len(msgs)

                docs, errors = _scan(msgs)
                # Stop on the first error if nothing was collected, or once errors repeat.
                if errors and (not docs or len(errors) >= 2):
                    break
                # Stop once there are enough distinct sources: more searching only costs time and tokens.
                if len({d.get("source_url") for d in docs}) >= self._s.max_docs:
                    await emit("note", message=f"collected {self._s.max_docs} sources; stopped searching")
                    break
        except GraphRecursionError:
            log.warning("agentic gather hit the step limit; using documents collected so far")
            await emit("note", message="step limit reached; analysing the documents collected so far")

        docs, errors = _scan(last["messages"]) if last else ([], [])
        if not docs and errors:
            log.error("gather tools failed: %s", errors[-1])
            raise SourceUnavailable(errors[-1])
        return docs[: self._s.max_docs]
