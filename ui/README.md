# Sentiment Lab (UI service)

React/Vite/TypeScript front end + a small FastAPI backend, deployed as one Railway service.

* **Research tab**: enter the agent API key, a subject and a search prompt (or URLs). The run streams every tool call and
  every defensive check (SSRF, sanitizer, analyst isolation, output validation) plus the exact analyst prompt.
* **Injection lab tab**: edit test pages, preview what the sanitizer does (no LLM), or run them end to end through the real
  pipeline and see PASS/FAIL against the expected label. Pages are hosted at unguessable, sandboxed public URLs because the
  fetch service reads them over the public internet (the same SSRF guard as production applies).

No run history is stored. Only lab pages are persisted (SQLite).

## Railway setup

1. New service from this repo, **root directory `/ui`** (Dockerfile build).
2. **Attach a volume** to the service (mount path e.g. `/data`). Railway then sets `RAILWAY_VOLUME_MOUNT_PATH` and the DB lives at
   `<mount>/lab.db`. Without a volume the app refuses to start (set `ALLOW_EPHEMERAL_DB=1` to override; data is lost on redeploy).
3. Variables:

   | Variable | Value |
   |---|---|
   | `AGENT_URL` | agent service URL, e.g. `http://agent.railway.internal:8080` (or its public URL) |
   | `UI_HMAC_SECRET` | long random string (signs the public lab-page URLs; required) |
   | `PUBLIC_BASE_URL` | this service's public URL, e.g. `https://ui-production-xxxx.up.railway.app` (lab URLs are built from it) |

4. Generate a public domain for the service.
5. If you see `unable to open database file`, the non-root container user cannot write to the volume: set `RAILWAY_RUN_UID=0`.
6. Run **one replica** only (SQLite single writer; a volume attaches to one instance).

The UI never stores the API key: the browser keeps it in `sessionStorage` and sends it per request; the backend validates it by
asking the agent (`GET /auth/check`) and forwards it for runs.

## Local development

```bash
# backend (needs the agent running, e.g. on :8000, with AGENT_API_KEY set)
cd ui && AGENT_URL=http://localhost:8000 uv run uvicorn server.asgi:app --port 8001 --reload
# frontend (proxies /api and /lab to :8001)
cd ui/web && npm install && npm run dev
```

Tests: `uv run pytest` (backend) and `cd web && npx vitest run` (frontend).
Regenerate seed pages after changing the eval cases or fixtures: `python scripts/build_seed.py`.
