import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .checks_catalog import describe

Emit = Callable[..., Awaitable[None]]


class Emitter:
    """Numbers events and pushes them onto a queue (or drops them when there is no listener)."""

    def __init__(self, queue: asyncio.Queue | None = None):
        self.queue = queue
        self.seq = 0

    async def __call__(self, type_: str, **fields: Any) -> None:
        self.seq += 1
        if self.queue is not None:
            await self.queue.put({"seq": self.seq, "ts": round(time.time(), 3), "type": type_, **fields})


async def noop_emit(type_: str, **fields: Any) -> None:
    return None


def get_emit(config: dict | None) -> Emit:
    return ((config or {}).get("configurable") or {}).get("emit") or noop_emit


async def emit_check(emit: Emit, doc: str | None, check_id: str, status: str, detail: str) -> None:
    d = describe(check_id)
    await emit("check", doc=doc, id=check_id, status=status, detail=detail, title=d["title"], defends=d["defends"])


async def emit_doc(emit: Emit, doc: dict) -> None:
    url = doc.get("source_url")
    await emit("document", url=url, domain=doc.get("domain"), title=doc.get("title"),
               flags=(doc.get("sanitization") or {}).get("flags", []), chars=len(doc.get("text") or ""))
    for c in doc.get("checks") or []:
        await emit_check(emit, url, c.get("id", "?"), c.get("status", "pass"), c.get("detail", ""))


_CODE = re.compile(r"\[([a-z]+(?:\.[a-z_]+)+)\]")


def refusal_code(message: str) -> str | None:
    """fetch-mcp errors look like '[ssrf.dns_ip] Destination address is not allowed.'"""
    m = _CODE.search(message or "")
    return m.group(1) if m else None
