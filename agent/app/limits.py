"""Cost and abuse guards for the public agent. In-memory (single instance); counters reset on redeploy."""
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """Best-effort client IP behind Railway's proxy. Prefers X-Real-IP, then the LAST X-Forwarded-For hop (the one our
    proxy appended; earlier hops are client-controlled). A spoofed value can only evade the throttle, never the key check."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class RunLimiter:
    """Bounds how much DeepSeek spend and compute the endpoint can be driven to: rate, daily cap, concurrency."""

    def __init__(self, per_min: int, daily_cap: int, max_concurrent: int, clock=time.time):
        self.per_min, self.daily_cap, self.max_concurrent, self.clock = per_min, daily_cap, max_concurrent, clock
        self._recent: deque[float] = deque()
        self._day = ""
        self._today = 0
        self.active = 0

    def acquire(self) -> None:
        now = self.clock()
        while self._recent and self._recent[0] < now - 60:
            self._recent.popleft()
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._today = day, 0
        if self.active >= self.max_concurrent:
            raise HTTPException(429, "too many analyses already running; try again shortly", headers={"Retry-After": "5"})
        if len(self._recent) >= self.per_min:
            wait = max(1, int(60 - (now - self._recent[0])))
            raise HTTPException(429, "rate limit reached; slow down", headers={"Retry-After": str(wait)})
        if self._today >= self.daily_cap:
            raise HTTPException(429, "daily analysis limit reached", headers={"Retry-After": "3600"})
        self._recent.append(now)
        self._today += 1
        self.active += 1

    def release(self) -> None:
        self.active = max(0, self.active - 1)


class AuthFailureLimiter:
    """Slows repeated bad-key attempts per client. Defence in depth: the key is long and random, so this mainly
    stops log spam and cheap guessing from one source."""

    def __init__(self, limit_per_min: int, clock=time.time):
        self.limit, self.clock = limit_per_min, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, ip: str) -> deque[float]:
        q = self._fails[ip]
        while q and q[0] < self.clock() - 60:
            q.popleft()
        if not q:
            self._fails.pop(ip, None)
            return self._fails[ip]
        return q

    def check(self, ip: str) -> None:
        if len(self._trim(ip)) >= self.limit:
            raise HTTPException(429, "too many failed attempts; wait a minute", headers={"Retry-After": "60"})

    def record_failure(self, ip: str) -> None:
        self._trim(ip).append(self.clock())
