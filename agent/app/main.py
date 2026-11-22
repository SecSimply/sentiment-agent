import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from langchain_deepseek import ChatDeepSeek

from .config import Settings, get_settings
from .gather import McpGatherer
from .graph import build_graph, to_response
from .schemas import AnalyzeRequest, AnalyzeResponse

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
    yield


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


@app.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_key)])
async def analyze(req: AnalyzeRequest):
    try:
        state = await app.state.graph.ainvoke({"request": req})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("analysis failed")
        raise HTTPException(502, "upstream failure") from exc
    return to_response(state)
