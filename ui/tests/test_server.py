import json

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from server import db
from server.config import Settings
from server.main import LAB_CSP, UI_CSP, create_app
from server.tokens import make_token, verify_token

KEY = {"X-API-Key": "k"}


def fake_agent() -> FastAPI:
    a = FastAPI()

    def check(key):
        if key != "k":
            raise HTTPException(401, "unauthorized")

    @a.get("/auth/check")
    def auth(x_api_key: str | None = Header(None)):
        check(x_api_key)
        return {"ok": True}

    @a.post("/analyze/stream")
    def stream(body: dict, x_api_key: str | None = Header(None)):
        check(x_api_key)
        evs = [{"seq": 1, "type": "check", "id": "request.auth", "status": "pass"},
               {"seq": 2, "type": "result", "echo": body}, {"seq": 3, "type": "done"}]
        return StreamingResponse((f"data: {json.dumps(e)}\n\n" for e in evs), media_type="text/event-stream")

    @a.post("/lab/sanitize-preview")
    def prev(body: dict, x_api_key: str | None = Header(None)):
        check(x_api_key)
        return {"text": body["html"].upper(), "checks": []}

    return a


def make_client(tmp_path, **kw):
    s = Settings(lab_db_path=str(tmp_path / "t.db"), ui_hmac_secret="secret", static_dir=str(tmp_path / "nostatic"),
                 public_base_url="https://ui.test", **kw)
    app = create_app(s, agent_transport=httpx.ASGITransport(app=fake_agent()))
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


# ---------- auth ----------
def test_health_is_open_and_has_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.headers["content-security-policy"] == UI_CSP
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_api_requires_valid_key(client, headers):
    assert client.get("/api/pages", headers=headers).status_code == 401
    assert client.post("/api/run", json={"query": "q"}, headers=headers).status_code == 401
    assert client.get("/api/auth/check", headers=headers).status_code == 401


def test_valid_key_accepted(client):
    assert client.get("/api/auth/check", headers=KEY).json() == {"ok": True}


def test_agent_unreachable_is_503(tmp_path):
    def boom(request):
        raise httpx.ConnectError("down")

    s = Settings(lab_db_path=str(tmp_path / "t.db"), ui_hmac_secret="secret", static_dir="none")
    with TestClient(create_app(s, agent_transport=httpx.MockTransport(boom))) as c:
        assert c.get("/api/auth/check", headers=KEY).status_code == 503


# ---------- seeding / migrations ----------
def test_seeds_loaded_and_idempotent(client, tmp_path):
    pages = client.get("/api/pages", headers=KEY).json()
    assert len(pages) == 26 and all(p["seeded"] for p in pages)
    assert client.post("/api/pages-restore-seeds", headers=KEY).json() == {"added": 0}
    victim = pages[0]["id"]
    assert client.delete(f"/api/pages/{victim}", headers=KEY).status_code == 204
    assert client.post("/api/pages-restore-seeds", headers=KEY).json() == {"added": 1}


def test_migrate_sets_user_version_and_is_noop_second_time(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_db_path_requires_volume_on_railway(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="volume"):
        db.resolve_db_path(Settings())
    assert db.resolve_db_path(Settings(allow_ephemeral_db=True)).name == "lab.db"
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    assert db.resolve_db_path(Settings()) == tmp_path / "lab.db"


def test_hmac_secret_required_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="UI_HMAC_SECRET"):
        create_app(Settings(lab_db_path=str(tmp_path / "x.db")))


def test_data_survives_restart(tmp_path):
    with make_client(tmp_path) as c:
        pid = c.post("/api/pages", json={"name": "mine", "html": "<p>x</p>"}, headers=KEY).json()["id"]
    with make_client(tmp_path) as c:  # new app, same file
        assert c.get(f"/api/pages/{pid}", headers=KEY).json()["name"] == "mine"


# ---------- CRUD ----------
def test_crud_roundtrip(client):
    created = client.post("/api/pages", json={"name": "T", "html": "<p>hi</p>", "technique": "x", "expected_label": "mixed", "notes": "n"}, headers=KEY)
    assert created.status_code == 201
    p = created.json()
    assert p["html"] == "<p>hi</p>" and p["expected_label"] == "mixed" and not p["seeded"]
    assert p["url"].startswith("https://ui.test/lab/p/" + p["id"] + "/")
    up = client.put(f"/api/pages/{p['id']}", json={"name": "T2", "html": "<p>yo</p>"}, headers=KEY).json()
    assert up["name"] == "T2" and up["html"] == "<p>yo</p>" and up["url"] == p["url"]  # URL stable across edits
    assert client.delete(f"/api/pages/{p['id']}", headers=KEY).status_code == 204
    assert client.get(f"/api/pages/{p['id']}", headers=KEY).status_code == 404
    assert client.put("/api/pages/nope", json={"name": "a", "html": "b"}, headers=KEY).status_code == 404


@pytest.mark.parametrize("body", [
    {"name": "", "html": "x"}, {"name": "a", "html": ""}, {"name": "a" * 101, "html": "x"},
    {"name": "a", "html": "x" * 200_001}, {"name": "a", "html": "x", "expected_label": "bogus"}])
def test_validation_rejects(client, body):
    assert client.post("/api/pages", json=body, headers=KEY).status_code == 422


def test_page_limit(tmp_path):
    with make_client(tmp_path, max_pages=27) as c:  # 26 seeds + 1
        assert c.post("/api/pages", json={"name": "a", "html": "x"}, headers=KEY).status_code == 201
        assert c.post("/api/pages", json={"name": "b", "html": "x"}, headers=KEY).status_code == 409


def test_sql_injection_attempts_are_inert(client):
    nasty = "x'); DROP TABLE pages;--"
    p = client.post("/api/pages", json={"name": nasty, "html": nasty, "notes": nasty}, headers=KEY).json()
    assert client.get(f"/api/pages/{p['id']}", headers=KEY).json()["name"] == nasty
    assert len(client.get("/api/pages", headers=KEY).json()) == 27
    assert client.get("/api/pages/x' OR '1'='1", headers=KEY).status_code == 404


# ---------- public lab page ----------
def test_lab_page_served_sandboxed_without_key(client):
    html = "<p style='display:none'>hidden</p><script>alert(1)</script>"
    p = client.post("/api/pages", json={"name": "t", "html": html}, headers=KEY).json()
    r = client.get(p["url"].replace("https://ui.test", ""))   # NO api key
    assert r.status_code == 200 and r.text == html
    assert r.headers["content-security-policy"] == LAB_CSP and "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and "noindex" in r.headers["x-robots-tag"]
    assert r.headers["content-type"].startswith("text/html")


def test_lab_page_bad_token_and_unknown_are_indistinguishable(client):
    p = client.post("/api/pages", json={"name": "t", "html": "x"}, headers=KEY).json()
    a = client.get(f"/lab/p/{p['id']}/badtoken")
    b = client.get("/lab/p/doesnotexist/badtoken")
    assert a.status_code == b.status_code == 404 and a.text == b.text


def test_rotate_revokes_old_url(client):
    p = client.post("/api/pages", json={"name": "t", "html": "x"}, headers=KEY).json()
    old = p["url"].replace("https://ui.test", "")
    assert client.get(old).status_code == 200
    new = client.post(f"/api/pages/{p['id']}/rotate", headers=KEY).json()["url"].replace("https://ui.test", "")
    assert new != old and client.get(old).status_code == 404 and client.get(new).status_code == 200


def test_deleted_page_url_is_gone(client):
    p = client.post("/api/pages", json={"name": "t", "html": "x"}, headers=KEY).json()
    client.delete(f"/api/pages/{p['id']}", headers=KEY)
    assert client.get(p["url"].replace("https://ui.test", "")).status_code == 404


def test_tokens():
    t = make_token("s", "abc", 1)
    assert verify_token("s", "abc", 1, t)
    assert not verify_token("s", "abc", 2, t) and not verify_token("s", "abd", 1, t) and not verify_token("other", "abc", 1, t)


# ---------- run proxy ----------
def test_run_streams_agent_events(client):
    r = client.post("/api/run", json={"urls": ["https://a.test/x"], "subject": "Acme"}, headers=KEY)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    assert [e["type"] for e in evs] == ["check", "result", "done"]
    assert evs[1]["echo"]["subject"] == "Acme"


def test_run_validates_input(client):
    assert client.post("/api/run", json={}, headers=KEY).status_code == 422
    assert client.post("/api/run", json={"query": "x" * 301}, headers=KEY).status_code == 422
    assert client.post("/api/run", json={"urls": ["u"] * 21}, headers=KEY).status_code == 422


def test_run_rate_limited_per_key(tmp_path):
    with make_client(tmp_path, run_rate_per_min=2) as c:
        codes = [c.post("/api/run", json={"query": "q"}, headers=KEY).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_sanitize_preview_proxy(client):
    assert client.post("/api/sanitize-preview", json={"html": "ab"}, headers=KEY).json()["text"] == "AB"
    assert client.post("/api/sanitize-preview", json={"html": ""}, headers=KEY).status_code == 422
    assert client.post("/api/sanitize-preview", json={"html": "a"}).status_code == 401


# ---------- export / import ----------
def test_export_import_roundtrip_excludes_seeds(client):
    client.post("/api/pages", json={"name": "mine", "html": "<p>1</p>", "technique": "t"}, headers=KEY)
    exp = client.get("/api/export", headers=KEY).json()
    assert [p["name"] for p in exp["pages"]] == ["mine"]  # seeds are reproducible, not exported
    assert client.post("/api/import", json={"pages": exp["pages"]}, headers=KEY).json() == {"imported": 1}
    assert len(client.get("/api/pages", headers=KEY).json()) == 28


def test_payloads_endpoint(client):
    p = client.get("/api/lab/payloads", headers=KEY).json()
    assert len(p) == 15 and {"id", "technique", "text", "target_label"} <= set(p[0])
    assert client.get("/api/lab/payloads").status_code == 401


# ---------- SPA fallback ----------
def test_spa_served_and_api_paths_not_swallowed(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "assets" / "a.js").write_text("1")
    s = Settings(lab_db_path=str(tmp_path / "t.db"), ui_hmac_secret="s", static_dir=str(static))
    with TestClient(create_app(s, agent_transport=httpx.ASGITransport(app=fake_agent()))) as c:
        assert c.get("/").text == "<html>app</html>" and c.get("/some/route").text == "<html>app</html>"
        assert c.get("/assets/a.js").text == "1"
        assert c.get("/api/nonexistent").status_code == 404
        assert c.get("/../../etc/passwd").status_code in (200, 404) and "root:" not in c.get("/..%2f..%2fetc/passwd").text


def test_v2_migration_fixes_existing_benign_control_label(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    conn.execute("BEGIN"); conn.execute(db.MIGRATIONS[0].split(";")[0]); conn.execute("PRAGMA user_version=1"); conn.execute("COMMIT")
    conn.execute("INSERT INTO pages (id,name,html,expected_label,seed_key,created_at,updated_at) VALUES ('a','b','h','positive','fixture:benign_control','t','t')")
    conn.execute("INSERT INTO pages (id,name,html,expected_label,seed_key,created_at,updated_at) VALUES ('c','d','h','positive','case:i01','t','t')")
    db.migrate(conn)
    labels = dict(conn.execute("SELECT id, expected_label FROM pages").fetchall())
    assert labels == {"a": "neutral", "c": "positive"}   # only the one mislabeled fixture is touched


def test_repeated_bad_keys_are_throttled_per_client(tmp_path):
    with make_client(tmp_path, auth_fail_limit_per_min=3) as c:
        bad = lambda ip: c.get("/api/pages", headers={"X-API-Key": "nope", "X-Real-IP": ip}).status_code
        assert [bad("9.9.9.9") for _ in range(3)] == [401, 401, 401]
        assert bad("9.9.9.9") == 429
        assert c.get("/api/pages", headers={**KEY, "X-Real-IP": "9.9.9.9"}).status_code == 429   # even the right key, once throttled
        assert c.get("/api/pages", headers={**KEY, "X-Real-IP": "8.8.8.8"}).status_code == 200
        r = c.get("/api/pages", headers={"X-API-Key": "nope", "X-Real-IP": "9.9.9.9"})
        assert r.headers["retry-after"] == "60"


def test_missing_key_does_not_count_as_a_guess(tmp_path):
    with make_client(tmp_path, auth_fail_limit_per_min=2) as c:
        for _ in range(5):
            assert c.get("/api/pages", headers={"X-Real-IP": "7.7.7.7"}).status_code == 401
        assert c.get("/api/pages", headers={**KEY, "X-Real-IP": "7.7.7.7"}).status_code == 200


def test_extra_security_headers(client):
    h = client.get("/api/health").headers
    assert h["strict-transport-security"].startswith("max-age=") and "camera=()" in h["permissions-policy"]
    assert h["cross-origin-opener-policy"] == "same-origin"
