from typing import TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .events import emit_check, get_emit
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

    async def gather(state: State, config: RunnableConfig) -> State:
        emit = get_emit(config)
        req = state["request"]
        await emit("stage", stage="gather", status="start")
        docs: list[dict] = []
        if req.urls:
            docs += await gatherer.fetch_urls(req.urls, emit)
        if req.query:
            docs += await gatherer.research(req.query, emit)
        # de-duplicate by URL, preserve order
        seen, unique = set(), []
        for d in docs:
            if d.get("source_url") not in seen:
                seen.add(d.get("source_url"))
                unique.append(d)
        kept = unique[: settings.max_docs]
        # Be explicit about what the analyst will (not) read, so the trace never overstates it.
        await emit("doc_selection", kept=[d.get("source_url") for d in kept],
                   dropped=[d.get("source_url") for d in unique[settings.max_docs:]], limit=settings.max_docs)
        await emit("stage", stage="gather", status="end", n_docs=len(kept))
        return {"docs": kept}

    async def analyze(state: State, config: RunnableConfig) -> State:
        emit = get_emit(config)
        docs = state["docs"]
        if not docs:
            raise ValueError("No documents could be retrieved")
        req = state["request"]
        await emit("stage", stage="analyze", status="start")

        prompt = render_documents(docs, req.subject or req.query, settings.max_chars_per_doc)
        flagged = [d for d in docs if "possible_injection" in (d.get("sanitization") or {}).get("flags", [])]
        breakouts = sum((d.get("text") or "").count("</") for d in docs)

        await emit("analyst_config", model=settings.deepseek_model, tools_bound=0,
                   structured_output="SentimentResult", temperature=0)
        await emit_check(emit, None, "analyst.no_tools", "pass", "0 tools bound to the model that reads the documents")
        await emit_check(emit, None, "analyst.untrusted_wrapper", "pass",
                         f"{len(docs)} document(s) wrapped as JSON in <documents>; escaped {breakouts} '</' sequence(s)")
        await emit_check(emit, None, "analyst.flags_forwarded", "warn" if flagged else "pass",
                         f"{len(flagged)} of {len(docs)} document(s) carried a possible_injection flag to the model")
        if settings.expose_prompt:
            await emit("analyst_prompt", system=ANALYST_SYSTEM, user=prompt)

        result = await analyst.ainvoke([SystemMessage(ANALYST_SYSTEM), HumanMessage(prompt)])
        await emit_check(emit, None, "output.schema", "pass", "response parsed and validated against SentimentResult")

        if flagged:
            overridden = not result.injection_suspected
            result.injection_suspected = True  # sanitizer evidence overrides the model's opinion
            await emit_check(emit, None, "output.flag_override", "warn" if overridden else "pass",
                             "injection_suspected forced to true by sanitizer evidence" if overridden
                             else "model also reported the injection; no override needed")
        else:
            await emit_check(emit, None, "output.flag_override", "pass", "no sanitizer flags; model's injection_suspected kept as returned")
        await emit("stage", stage="analyze", status="end")
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
