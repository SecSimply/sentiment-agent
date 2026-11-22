from typing import TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .gather import Gatherer
from .prompts import ANALYST_SYSTEM, render_documents
from .schemas import AnalyzeRequest, AnalyzeResponse, DocumentMeta, SentimentResult


class State(TypedDict, total=False):
    request: AnalyzeRequest
    docs: list[dict]
    result: SentimentResult


def build_graph(llm: BaseChatModel, gatherer: Gatherer, settings: Settings):
    # Stage 2 model has NO tools bound: a poisoned document cannot trigger any action.
    analyst = llm.with_structured_output(SentimentResult, method="function_calling")

    async def gather(state: State) -> State:
        req = state["request"]
        docs: list[dict] = []
        if req.urls:
            docs += await gatherer.fetch_urls(req.urls)
        if req.query:
            docs += await gatherer.research(req.query)
        # de-duplicate by URL, preserve order
        seen, unique = set(), []
        for d in docs:
            if d.get("source_url") not in seen:
                seen.add(d.get("source_url"))
                unique.append(d)
        return {"docs": unique[: settings.max_docs]}

    async def analyze(state: State) -> State:
        docs = state["docs"]
        if not docs:
            raise ValueError("No documents could be retrieved")
        req = state["request"]
        prompt = render_documents(docs, req.subject or req.query, settings.max_chars_per_doc)
        result = await analyst.ainvoke([SystemMessage(ANALYST_SYSTEM), HumanMessage(prompt)])
        if any("possible_injection" in (d.get("sanitization") or {}).get("flags", []) for d in docs):
            result.injection_suspected = True  # sanitizer evidence overrides the model's opinion
        return {"result": result}

    g = StateGraph(State)
    g.add_node("gather", gather)
    g.add_node("analyze", analyze)
    g.add_edge(START, "gather")
    g.add_edge("gather", "analyze")
    g.add_edge("analyze", END)
    return g.compile()


def to_response(state: State) -> AnalyzeResponse:
    metas = [
        DocumentMeta(
            source_url=d.get("source_url", ""),
            domain=d.get("domain", ""),
            title=d.get("title"),
            flags=(d.get("sanitization") or {}).get("flags", []),
        )
        for d in state["docs"]
    ]
    return AnalyzeResponse(result=state["result"], documents=metas)
