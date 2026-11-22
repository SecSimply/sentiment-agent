import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_deepseek import ChatDeepSeek

from .config import Settings, get_settings
from .events import Emitter, emit_check
from .gather import McpGatherer, SourceUnavailable
from .graph import build_graph, to_response
from .schemas import AnalyzeRequest, AnalyzeResponse, SanitizePreviewRequest

log = logging.getLogger("agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    llm = ChatDeepSeek(
        model=s.deepseek_model,
        api_key=s.deepseek_api_key,
        temperature=0,
        timeout=s.llm_timeout_s,
        max_retries=2,
    )
    app.state.graph = build_graph(llm, McpGatherer(s, llm), s)
    app.state.fetch_http = httpx.AsyncClient(
        base_url=s.fetch_base_url,
        headers={"Authorization": f"Bearer {s.mcp_shared_secret.get_secret_value()}"},
        timeout=15,
    )
    yield
    await app.state.fetch_http.aclose()


app = FastAPI(title="sentiment-agent", lifespan=lifespan)


def require_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
    s: Settings = Depends(get_settings),
):
    expected = s.agent_api_key.get_secret_value()
    given = x_api_key or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "unauthorized")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/auth/check", dependencies=[Depends(require_key)])
async def auth_check():
    return {"ok": True}


@app.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_key)])
async def analyze(req: AnalyzeRequest):
    try:
        state = await app.state.graph.ainvoke({"request": req})
    except SourceUnavailable as exc:
        raise HTTPException(503, "Document retrieval failed: a source tool is unavailable or not configured") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("analysis failed")
        raise HTTPException(502, "upstream failure") from exc
    return to_response(state)


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@app.post("/analyze/stream", dependencies=[Depends(require_key)])
async def analyze_stream(req: AnalyzeRequest):
    """Same pipeline as /analyze, but streams every call and defensive check as Server-Sent Events."""
    queue: asyncio.Queue = asyncio.Queue()
    emit = Emitter(queue)

    async def runner():
        try:
            await emit_check(emit, None, "request.auth", "pass", "API key accepted")
            state = await app.state.graph.ainvoke({"request": req}, config={"configurable": {"emit": emit}})
            resp = to_response(state)
            await emit("result", result=resp.result.model_dump(), documents=[d.model_dump() for d in resp.documents])
        except SourceUnavailable:
            await emit("error", code="source_unavailable",
                       message="Document retrieval failed: a source tool is unavailable or not configured")
        except ValueError as exc:
            await emit("error", code="no_documents", message=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stream analysis failed")
            await emit("error", code="upstream_failure", message="upstream failure")
        finally:
            await emit("done")
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev is None:
                    break
                yield _sse(ev)
        finally:
            task.cancel()  # client went away or stream finished

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/lab/sanitize-preview", dependencies=[Depends(require_key)])
async def sanitize_preview(body: SanitizePreviewRequest):
    """Run HTML through the real sanitizer (no network fetch, no LLM) and return text, flags and checks."""
    try:
        r = await app.state.fetch_http.post("/lab/sanitize", json={"html": body.html})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("sanitize preview failed: %s", type(exc).__name__)
        raise HTTPException(502, "sanitizer unavailable") from exc
    data = r.json()
    from .checks_catalog import describe

    for c in data.get("checks", []):
        c.update(describe(c.get("id", "")))
    return data
