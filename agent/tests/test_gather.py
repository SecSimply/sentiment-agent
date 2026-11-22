import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException, tool

from app.config import Settings, get_settings
from app.gather import McpGatherer, SourceUnavailable

DOC = {"source_url": "https://a.test/x", "domain": "a.test", "title": "T", "text": "hi", "sanitization": {"flags": []}}


class LoopingModel(BaseChatModel):
    """Calls search_news forever, like a model that keeps retrying."""

    calls: int = 0

    @property
    def _llm_type(self):
        return "looping"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        LoopingModel.calls += 1
        msg = AIMessage("", tool_calls=[{"name": "search_news", "args": {"query": "q"}, "id": f"c{LoopingModel.calls}"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def gatherer(tool_fn, steps=8):
    LoopingModel.calls = 0
    s = Settings(max_agent_steps=steps)
    return McpGatherer(s, LoopingModel(), tools=[tool_fn])


async def test_broken_tool_fails_fast():
    @tool
    def search_news(query: str) -> str:
        """search"""
        raise ToolException("search_news is not configured.")

    with pytest.raises(SourceUnavailable):
        await gatherer(search_news, steps=25).research("q")
    assert LoopingModel.calls <= 2  # stopped on first error, did not run to the step limit


async def test_partial_docs_kept_at_step_limit():
    @tool
    def search_news(query: str) -> str:
        """search"""
        return json.dumps([DOC])

    docs = await gatherer(search_news, steps=6).research("q")
    assert docs and docs[0]["source_url"] == DOC["source_url"]  # no GraphRecursionError, docs returned


async def test_docs_then_errors_returns_docs():
    state = {"n": 0}

    @tool
    def search_news(query: str) -> str:
        """search"""
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps([DOC])
        raise ToolException("boom")

    docs = await gatherer(search_news, steps=25).research("q")
    assert len(docs) == 1
    assert LoopingModel.calls < 10


def test_api_returns_503_when_source_unavailable(monkeypatch):
    from app import main

    class Boom:
        async def ainvoke(self, _):
            raise SourceUnavailable("x")

    s = Settings(agent_api_key="k", deepseek_api_key="x")
    main.app.dependency_overrides[get_settings] = lambda: s
    monkeypatch.setattr(main, "get_settings", lambda: s)
    with TestClient(main.app) as c:
        main.app.state.graph = Boom()
        r = c.post("/analyze", json={"query": "q"}, headers={"X-API-Key": "k"})
    main.app.dependency_overrides.clear()
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]
