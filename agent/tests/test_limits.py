import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.graph import build_graph
from app.limits import AuthFailureLimiter, RunLimiter
from tests.test_stream import DocGatherer, FakeLLM


class Clock:
    def __init__(self, t=1_700_000_000.0): self.t = t
    def __call__(self): return self.t


def codes(fn):
    try:
        fn(); return None
    except HTTPException as e:
        return e.status_code, e.headers


def test_rate_limit_is_a_sliding_minute():
    c = Clock(); lim = RunLimiter(per_min=2, daily_cap=100, max_concurrent=10, clock=c)
    lim.acquire(); lim.release(); lim.acquire(); lim.release()
    status, headers = codes(lim.acquire)
    assert status == 429 and int(headers["Retry-After"]) >= 1
    c.t += 61
    assert codes(lim.acquire) is None


def test_concurrency_cap_and_release():
    lim = RunLimiter(per_min=100, daily_cap=100, max_concurrent=2)
    lim.acquire(); lim.acquire()
    assert codes(lim.acquire)[0] == 429
    lim.release()
    assert codes(lim.acquire) is None
    lim.release(); lim.release(); lim.release(); lim.release()   # over-release never goes negative
    assert lim.active == 0


def test_daily_cap_resets_at_utc_midnight_and_failed_attempts_still_count():
    c = Clock(1_700_000_000.0)   # 2023-11-14 22:13 UTC
    lim = RunLimiter(per_min=100, daily_cap=3, max_concurrent=100, clock=c)
    for _ in range(3):
        lim.acquire(); lim.release()
    assert codes(lim.acquire)[0] == 429
    c.t += 3 * 3600   # past midnight UTC
    assert codes(lim.acquire) is None


def test_rejected_acquire_does_not_consume_capacity():
    lim = RunLimiter(per_min=1, daily_cap=10, max_concurrent=5)
    lim.acquire(); lim.release()
    for _ in range(5):
        assert codes(lim.acquire)[0] == 429
    assert lim.active == 0 and lim._today == 1


def test_auth_failure_limiter_is_per_client_and_expires():
    c = Clock(); lim = AuthFailureLimiter(3, clock=c)
    for _ in range(3):
        lim.record_failure("1.1.1.1")
    assert codes(lambda: lim.check("1.1.1.1"))[0] == 429
    assert codes(lambda: lim.check("2.2.2.2")) is None
    c.t += 61
    assert codes(lambda: lim.check("1.1.1.1")) is None


@pytest.fixture
def client(monkeypatch):
    from app import main

    s = Settings(agent_api_key="k", deepseek_api_key="x", rate_per_min=2, max_concurrent_runs=5, auth_fail_limit_per_min=3)
    main.app.dependency_overrides[get_settings] = lambda: s
    monkeypatch.setattr(main, "get_settings", lambda: s)
    with TestClient(main.app) as c:
        main.app.state.graph = build_graph(FakeLLM(), DocGatherer(), s)
        yield c, main
    main.app.dependency_overrides.clear()


H = {"X-API-Key": "k"}
BODY = {"urls": ["https://a.test/x"], "subject": "Acme"}


def test_stream_and_analyze_share_the_rate_limit_and_send_retry_after(client):
    c, _ = client
    assert c.post("/analyze/stream", json=BODY, headers=H).status_code == 200
    assert c.post("/analyze", json=BODY, headers=H).status_code == 200
    r = c.post("/analyze/stream", json=BODY, headers=H)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert c.post("/analyze", json=BODY, headers=H).status_code == 429


def test_slot_is_released_when_a_stream_finishes(client):
    c, main = client
    main.app.state.limiter.max_concurrent = 1
    main.app.state.limiter.per_min = 100
    for _ in range(3):   # would fail on the 2nd call if the slot leaked
        assert c.post("/analyze/stream", json=BODY, headers=H).status_code == 200
    assert main.app.state.limiter.active == 0


def test_slot_is_released_when_the_graph_fails(client):
    c, main = client
    main.app.state.limiter.per_min = 100

    class Boom:
        async def ainvoke(self, *a, **k): raise RuntimeError("x")

    main.app.state.graph = Boom()
    assert c.post("/analyze", json=BODY, headers=H).status_code == 502
    c.post("/analyze/stream", json=BODY, headers=H)
    assert main.app.state.limiter.active == 0


def test_repeated_bad_keys_get_throttled_but_other_clients_are_unaffected(client):
    c, _ = client
    bad = lambda ip: c.get("/auth/check", headers={"X-API-Key": "nope", "X-Real-IP": ip}).status_code
    assert [bad("9.9.9.9") for _ in range(3)] == [401, 401, 401]
    assert bad("9.9.9.9") == 429
    # even the correct key is refused from a throttled client, so guessing can't continue past the limit
    assert c.get("/auth/check", headers={**H, "X-Real-IP": "9.9.9.9"}).status_code == 429
    assert c.get("/auth/check", headers={**H, "X-Real-IP": "8.8.8.8"}).status_code == 200


def test_oversized_url_list_is_rejected(client):
    c, _ = client
    assert c.post("/analyze/stream", json={"urls": [f"https://a.test/{i}" for i in range(21)]}, headers=H).status_code == 422
