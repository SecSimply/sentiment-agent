import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    """Prefers X-Real-IP, then the LAST X-Forwarded-For hop (appended by our proxy). A spoofed value can only evade
    this throttle; it can never bypass the key check."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class AuthFailureLimiter:
    def __init__(self, limit_per_min: int, clock=time.time):
        self.limit, self.clock = limit_per_min, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, ip: str) -> deque[float]:
        q = self._fails[ip]
        while q and q[0] < self.clock() - 60:
            q.popleft()
        return q

    def check(self, ip: str) -> None:
        if len(self._trim(ip)) >= self.limit:
            raise HTTPException(429, "too many failed attempts; wait a minute", headers={"Retry-After": "60"})

    def record_failure(self, ip: str) -> None:
        q = self._trim(ip)
        q.append(self.clock())
        if len(q) == self.limit:   # log the transition once; the client address only, never any key material
            log.warning("auth throttle engaged for client %s", ip)
