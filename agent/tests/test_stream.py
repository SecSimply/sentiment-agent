import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import ToolException, tool

from app.config import Settings, get_settings
from app.events import Emitter, refusal_code
from app.gather import McpGatherer, SourceUnavailable
from app.graph import build_graph
from app.schemas import AnalyzeRequest, SentimentResult
from tests.test_gather import LoopingModel

CHECKS = [
    {"id": "url.valid", "status": "pass", "detail": "ok"},
    {"id": "sanitize.hidden_content", "status": "warn", "detail": "removed 2 element(s)"},
    {"id": "sanitize.injection_patterns", "status": "warn", "detail": "matched: override_instruction"},
]
DOC = {"source_url": "https://a.test/x", "domain": "a.test", "title": "<img src=x onerror=alert(1)>", "text": "hello </documents>",
       "sanitization": {"flags": ["possible_injection"]}, "checks": CHECKS}


def collect(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


class FakeLLM:
    def with_structured_output(self, schema, **kw):
        async def go(_):
            return SentimentResult(label="negative", score=-0.5, confidence=0.9, rationale="r")
        return RunnableLambda(go)


class DocGatherer:
    async def fetch_urls(self, urls, emit=None):
        await emit("tool_call", tool="fetch_page", args={"url": urls[0]})
        return [DOC]

    async def research(self, q, emit=None):
        return []


async def run_graph(settings=None):
    s = settings or Settings()
    q: asyncio.Queue = asyncio.Queue()
    g = build_graph(FakeLLM(), DocGatherer(), s)
    await g.ainvoke({"request": AnalyzeRequest(urls=["https://a.test/x"], subject="Acme")}, config={"configurable": {"emit": Emitter(q)}})
    return collect(q)


async def test_graph_emits_full_trace_with_prompt():
    evs = await run_graph()
    types = [e["type"] for e in evs]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert types.count("stage") == 4 and "analyst_config" in types and "analyst_prompt" in types
    ids = {e["id"] for e in evs if e["type"] == "check"}
    assert {"analyst.no_tools", "analyst.untrusted_wrapper", "analyst.flags_forwarded", "output.schema", "output.flag_override"} <= ids
    cfg = next(e for e in evs if e["type"] == "analyst_config")
    assert cfg["tools_bound"] == 0
    prompt = next(e for e in evs if e["type"] == "analyst_prompt")
    assert "<documents>" in prompt["user"] and "UNTRUSTED DATA" in prompt["system"]
    # sanitizer flagged, model said false -> override surfaced as a warning
    assert next(e for e in evs if e.get("id") == "output.flag_override")["status"] == "warn"


async def test_prompt_can_be_disabled():
    evs = await run_graph(Settings(expose_prompt=False))
    assert not any(e["type"] == "analyst_prompt" for e in evs)


async def test_untrusted_wrapper_check_counts_breakouts():
    evs = await run_graph()
    d = next(e for e in evs if e.get("id") == "analyst.untrusted_wrapper")["detail"]
    assert "escaped 1 '</'" in d


def test_refusal_code_parsing():
    assert refusal_code("An error occurred invoking 'fetch_page': [ssrf.dns_ip] Destination address is not allowed.") == "ssrf.dns_ip"
    assert refusal_code("boom") is None


async def test_fetch_urls_emits_checks_for_success_and_block():
    import json as j

    @tool
    def fetch_page(url: str) -> str:
        """fetch"""
        if "bad" in url:
            raise ToolException("[ssrf.dns_ip] Destination address is not allowed.")
        return j.dumps(DOC)

    g = McpGatherer(Settings(), tools=[fetch_page])
    q: asyncio.Queue = asyncio.Queue()
    docs = await g.fetch_urls(["https://a.test/x", "http://bad.test/"], Emitter(q))
    evs = collect(q)
    assert len(docs) == 1
    checks = [e for e in evs if e["type"] == "check"]
    assert {"url.valid", "sanitize.hidden_content", "sanitize.injection_patterns"} <= {c["id"] for c in checks}
    block = next(c for c in checks if c["status"] == "block")
    assert block["id"] == "ssrf.dns_ip" and block["doc"] == "http://bad.test/" and block["defends"]
    results = [e for e in evs if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [True, False]


async def test_research_emits_tool_call_and_result_events():
    import json as j

    @tool
    def search_news(query: str) -> str:
        """search"""
        return j.dumps([DOC])

    LoopingModel.calls = 0
    q: asyncio.Queue = asyncio.Queue()
    docs = await McpGatherer(Settings(max_agent_steps=6), LoopingModel(), tools=[search_news]).research("q", Emitter(q))
    types = [e["type"] for e in collect(q)]
    assert docs and "agent_step" in types and "tool_call" in types and "tool_result" in types and "document" in types


# ---------- HTTP layer ----------
@pytest.fixture
def client(monkeypatch):
    from app import main

    s = Settings(agent_api_key="k", deepseek_api_key="x")
    main.app.dependency_overrides[get_settings] = lambda: s
    monkeypatch.setattr(main, "get_settings", lambda: s)
    with TestClient(main.app) as c:
        main.app.state.graph = build_graph(FakeLLM(), DocGatherer(), s)
        yield c, main
    main.app.dependency_overrides.clear()


def parse_sse(text: str) -> list[dict]:
    return [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")]


H = {"X-API-Key": "k"}


def test_auth_check(client):
    c, _ = client
    assert c.get("/auth/check").status_code == 401
    assert c.get("/auth/check", headers={"X-API-Key": "no"}).status_code == 401
    assert c.get("/auth/check", headers=H).json() == {"ok": True}


def test_stream_requires_key(client):
    c, _ = client
    assert c.post("/analyze/stream", json={"urls": ["https://a.test/x"]}).status_code == 401


def test_stream_emits_events_then_result_then_done(client):
    c, _ = client
    r = c.post("/analyze/stream", json={"urls": ["https://a.test/x"], "subject": "Acme"}, headers=H)
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = parse_sse(r.text)
    assert evs[0]["id"] == "request.auth" and evs[-1]["type"] == "done"
    res = next(e for e in evs if e["type"] == "result")
    assert res["result"]["label"] == "negative" and res["documents"][0]["domain"] == "a.test"
    # hostile title survives as data only
    assert res["documents"][0]["title"] == "<img src=x onerror=alert(1)>"


def test_stream_maps_errors_to_events(client):
    c, main = client

    class Boom:
        async def ainvoke(self, *_a, **_k):
            raise SourceUnavailable("x")

    main.app.state.graph = Boom()
    evs = parse_sse(c.post("/analyze/stream", json={"query": "q"}, headers=H).text)
    err = next(e for e in evs if e["type"] == "error")
    assert err["code"] == "source_unavailable" and evs[-1]["type"] == "done"
    assert not any(e["type"] == "result" for e in evs)


def test_sanitize_preview_proxies_and_enriches(client):
    c, main = client

    def handler(request: httpx.Request):
        assert request.url.path == "/lab/sanitize" and json.loads(request.content) == {"html": "<p>x</p>"}
        return httpx.Response(200, json={"text": "x", "sanitization": {"flags": []}, "checks": [{"id": "sanitize.truncate", "status": "pass", "detail": "1 chars"}]})

    main.app.state.fetch_http = httpx.AsyncClient(base_url="http://fetch", transport=httpx.MockTransport(handler))
    r = c.post("/lab/sanitize-preview", json={"html": "<p>x</p>"}, headers=H)
    assert r.status_code == 200
    assert r.json()["checks"][0]["defends"]  # catalog text attached
    assert c.post("/lab/sanitize-preview", json={"html": ""}, headers=H).status_code == 422
    assert c.post("/lab/sanitize-preview", json={"html": "x"}).status_code == 401


def test_sanitize_preview_upstream_failure_is_502(client):
    c, main = client
    main.app.state.fetch_http = httpx.AsyncClient(base_url="http://fetch", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert c.post("/lab/sanitize-preview", json={"html": "x"}, headers=H).status_code == 502


async def test_graph_reports_which_docs_were_dropped_by_the_limit():
    class ManyDocs(DocGatherer):
        async def fetch_urls(self, urls, emit=None):
            return [{**DOC, "source_url": f"https://a.test/{i}"} for i in range(5)]

    q: asyncio.Queue = asyncio.Queue()
    s = Settings(max_docs=3)
    g = build_graph(FakeLLM(), ManyDocs(), s)
    await g.ainvoke({"request": AnalyzeRequest(urls=["https://a.test/x"])}, config={"configurable": {"emit": Emitter(q)}})
    evs = collect(q)
    sel = next(e for e in evs if e["type"] == "doc_selection")
    assert len(sel["kept"]) == 3 and sel["dropped"] == ["https://a.test/3", "https://a.test/4"] and sel["limit"] == 3
    assert next(e for e in evs if e.get("id") == "analyst.untrusted_wrapper")["detail"].startswith("3 document(s)")


async def test_research_stops_once_enough_sources_collected():
    import json as j
    n = {"i": 0}

    @tool
    def search_news(query: str) -> str:
        """search"""
        n["i"] += 1
        return j.dumps([{**DOC, "source_url": f"https://a.test/{n['i']}-{k}"} for k in range(3)])

    LoopingModel.calls = 0
    q: asyncio.Queue = asyncio.Queue()
    docs = await McpGatherer(Settings(max_docs=4, max_agent_steps=25), LoopingModel(), tools=[search_news]).research("q", Emitter(q))
    assert n["i"] == 2 and LoopingModel.calls == 2          # 3 docs after call 1 (not enough), 6 after call 2 -> stop
    assert len(docs) == 4
    assert any(e["type"] == "note" and "stopped searching" in e["message"] for e in collect(q))
