import hashlib
import json
import logging
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import db
from .config import Settings, get_settings
from .tokens import make_token, verify_token

log = logging.getLogger("ui")
Label = Literal["positive", "negative", "neutral", "mixed"]

UI_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src 'self'; "
          "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
# Lab pages are hostile-by-design content. `sandbox` gives them an opaque origin (no access to the UI's storage),
# no scripts, no forms. Inline styles are allowed so the human-view preview renders hidden text realistically.
LAB_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"


class PageIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    html: str = Field(min_length=1, max_length=200_000)
    technique: str = Field("", max_length=60)
    expected_label: Label = "negative"
    notes: str = Field("", max_length=500)


class RunIn(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=20)
    query: str | None = Field(None, max_length=300)
    subject: str | None = Field(None, max_length=200)


class PreviewIn(BaseModel):
    html: str = Field(min_length=1, max_length=200_000)


class ImportIn(BaseModel):
    pages: list[PageIn] = Field(max_length=200)


def create_app(settings: Settings | None = None, agent_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or get_settings()
    db_path = db.resolve_db_path(s)
    if not s.ui_hmac_secret.get_secret_value():
        if s.allow_ephemeral_db or not __import__("os").environ.get("RAILWAY_ENVIRONMENT"):
            s = s.model_copy(update={"ui_hmac_secret": secrets.token_urlsafe(32)})  # dev: random per start
        else:
            raise RuntimeError("UI_HMAC_SECRET must be set in production (it signs public lab-page URLs).")
    secret = s.ui_hmac_secret.get_secret_value()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(db_path)
        db.migrate(conn)
        added = db.seed(conn)
        conn.close()
        log.info("lab db at %s (seeded %d new pages)", db_path, added)
        app.state.agent = httpx.AsyncClient(base_url=s.agent_url, transport=agent_transport, timeout=httpx.Timeout(10, read=300))
        yield
        await app.state.agent.aclose()

    app = FastAPI(title="sentiment-ui", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth_cache = {}          # sha256(key) -> expiry
    app.state.runs = defaultdict(deque)  # sha256(key) -> recent run timestamps

    # ---------- helpers ----------
    def get_db():
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def base_url(request: Request) -> str:
        if s.public_base_url:
            return s.public_base_url.rstrip("/")
        return str(request.base_url).rstrip("/")

    def public_url(request: Request, row: sqlite3.Row) -> str:
        return f"{base_url(request)}/lab/p/{row['id']}/{make_token(secret, row['id'], row['token_version'])}"

    def summary(request: Request, row: sqlite3.Row, with_html: bool = False) -> dict:
        d = {"id": row["id"], "name": row["name"], "technique": row["technique"], "expected_label": row["expected_label"],
             "notes": row["notes"], "seeded": row["seed_key"] is not None, "updated_at": row["updated_at"],
             "url": public_url(request, row)}
        if with_html:
            d["html"] = row["html"]
        return d

    async def require_key(x_api_key: str | None = Header(None)) -> str:
        """Validates the key by asking the agent. The key is never stored, only a short-lived hash of a *valid* one."""
        if not x_api_key:
            raise HTTPException(401, "missing API key")
        h = hashlib.sha256(x_api_key.encode()).hexdigest()
        if app.state.auth_cache.get(h, 0) > time.time():
            return h
        try:
            r = await app.state.agent.get("/auth/check", headers={"X-API-Key": x_api_key})
        except httpx.HTTPError:
            raise HTTPException(503, "agent unreachable")
        if r.status_code == 401:
            raise HTTPException(401, "invalid API key")
        if r.status_code != 200:
            raise HTTPException(502, "agent error")
        app.state.auth_cache[h] = time.time() + 60
        return h

    def row_or_404(conn: sqlite3.Connection, page_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if not row:
            raise HTTPException(404, "page not found")
        return row

    # ---------- middleware ----------
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        if not request.url.path.startswith("/lab/"):
            resp.headers.setdefault("Content-Security-Policy", UI_CSP)
        return resp

    # ---------- misc ----------
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/auth/check")
    async def auth_check(_: str = Depends(require_key)):
        return {"ok": True}

    # ---------- run (SSE proxy) ----------
    @app.post("/api/run")
    async def run(body: RunIn, key_hash: str = Depends(require_key), x_api_key: str = Header(...)):
        if not body.urls and not body.query:
            raise HTTPException(422, "Provide urls and/or query")
        window = app.state.runs[key_hash]
        t = time.time()
        while window and window[0] < t - 60:
            window.popleft()
        if len(window) >= s.run_rate_per_min:
            raise HTTPException(429, "rate limit: too many runs, wait a minute")
        window.append(t)

        req = app.state.agent.build_request("POST", "/analyze/stream", json=body.model_dump(), headers={"X-API-Key": x_api_key})
        try:
            r = await app.state.agent.send(req, stream=True)
        except httpx.HTTPError:
            raise HTTPException(503, "agent unreachable")
        if r.status_code != 200:
            await r.aclose()
            raise HTTPException(401 if r.status_code == 401 else 502, "agent rejected the request")
        return StreamingResponse(r.aiter_raw(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                                 background=BackgroundTask(r.aclose))

    @app.post("/api/sanitize-preview")
    async def sanitize_preview(body: PreviewIn, _: str = Depends(require_key), x_api_key: str = Header(...)):
        try:
            r = await app.state.agent.post("/lab/sanitize-preview", json=body.model_dump(), headers={"X-API-Key": x_api_key})
        except httpx.HTTPError:
            raise HTTPException(503, "agent unreachable")
        if r.status_code != 200:
            raise HTTPException(502, "sanitizer unavailable")
        return r.json()

    # ---------- lab pages (CRUD, key required) ----------
    @app.get("/api/pages")
    def list_pages(request: Request, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        rows = conn.execute("SELECT * FROM pages ORDER BY seed_key IS NOT NULL, updated_at DESC, name").fetchall()
        return [summary(request, r) for r in rows]

    @app.post("/api/pages", status_code=201)
    def create_page(body: PageIn, request: Request, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        if conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] >= s.max_pages:
            raise HTTPException(409, f"page limit reached ({s.max_pages})")
        pid, ts = db.new_id(), db.now()
        conn.execute("INSERT INTO pages (id,name,html,technique,expected_label,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (pid, body.name, body.html, body.technique, body.expected_label, body.notes, ts, ts))
        return summary(request, row_or_404(conn, pid), with_html=True)

    @app.get("/api/pages/{page_id}")
    def get_page(page_id: str, request: Request, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        return summary(request, row_or_404(conn, page_id), with_html=True)

    @app.put("/api/pages/{page_id}")
    def update_page(page_id: str, body: PageIn, request: Request, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        row_or_404(conn, page_id)
        conn.execute("UPDATE pages SET name=?, html=?, technique=?, expected_label=?, notes=?, updated_at=? WHERE id=?",
                     (body.name, body.html, body.technique, body.expected_label, body.notes, db.now(), page_id))
        return summary(request, row_or_404(conn, page_id), with_html=True)

    @app.delete("/api/pages/{page_id}", status_code=204)
    def delete_page(page_id: str, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        row_or_404(conn, page_id)
        conn.execute("DELETE FROM pages WHERE id=?", (page_id,))
        return Response(status_code=204)

    @app.post("/api/pages/{page_id}/rotate")
    def rotate_token(page_id: str, request: Request, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        """Revoke the current public URL and issue a new one."""
        row_or_404(conn, page_id)
        conn.execute("UPDATE pages SET token_version = token_version + 1 WHERE id=?", (page_id,))
        return summary(request, row_or_404(conn, page_id))

    @app.post("/api/pages-restore-seeds")
    def restore_seeds(_: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        return {"added": db.seed(conn)}

    @app.get("/api/lab/payloads")
    def payloads(_: str = Depends(require_key)):
        return json.loads((db.SEED_DIR / "payloads.json").read_text())

    @app.get("/api/export")
    def export(_: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        rows = conn.execute("SELECT * FROM pages WHERE seed_key IS NULL ORDER BY created_at").fetchall()
        pages = [{"name": r["name"], "html": r["html"], "technique": r["technique"], "expected_label": r["expected_label"], "notes": r["notes"]} for r in rows]
        return JSONResponse({"version": 1, "pages": pages}, headers={"Content-Disposition": 'attachment; filename="lab-pages.json"'})

    @app.post("/api/import")
    def import_pages(body: ImportIn, _: str = Depends(require_key), conn: sqlite3.Connection = Depends(get_db)):
        room = s.max_pages - conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        if len(body.pages) > room:
            raise HTTPException(409, f"import would exceed the page limit ({s.max_pages})")
        for p in body.pages:
            ts = db.now()
            conn.execute("INSERT INTO pages (id,name,html,technique,expected_label,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (db.new_id(), p.name, p.html, p.technique, p.expected_label, p.notes, ts, ts))
        return {"imported": len(body.pages)}

    # ---------- public, sandboxed lab page (fetched by fetch-mcp, so no API key) ----------
    @app.get("/lab/p/{page_id}/{token}")
    def lab_page(page_id: str, token: str, conn: sqlite3.Connection = Depends(get_db)):
        row = conn.execute("SELECT html, token_version FROM pages WHERE id=?", (page_id,)).fetchone()
        if not row or not verify_token(secret, page_id, row["token_version"], token):
            raise HTTPException(404, "not found")  # same answer for missing page and bad token
        return HTMLResponse(row["html"], headers={
            "Content-Security-Policy": LAB_CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"})

    # ---------- SPA ----------
    static = Path(s.static_dir)
    if (static / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "lab/")):
                raise HTTPException(404, "not found")
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app


app = None  # set by `server.asgi` (kept separate so importing for tests has no side effects)
