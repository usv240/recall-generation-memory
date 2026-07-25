"""Small, dependency-free controls for the public Recall demo and API clients."""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass
from fastapi import HTTPException, Request
from .config import config


@dataclass(frozen=True)
class Actor:
    kind: str
    label: str


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 3600) -> tuple[bool, int]:
        current = time.time()
        cutoff = current - window_seconds
        with self._lock:
            values = [value for value in self._hits.get(key, []) if value >= cutoff]
            if len(values) >= limit:
                self._hits[key] = values
                return False, 0
            values.append(current)
            self._hits[key] = values
            return True, max(0, limit - len(values))


limiter = SlidingWindowLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _api_key_actor(request: Request) -> Actor | None:
    supplied = request.headers.get("x-recall-key", "").strip()
    if not supplied:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
    for position, expected in enumerate(config.RECALL_API_KEYS):
        if hmac.compare_digest(supplied, expected):
            fingerprint = hashlib.sha256(expected.encode()).hexdigest()[:8]
            return Actor("api_key", f"key_{position + 1}_{fingerprint}")
    return None


def require_private_api_key(request: Request) -> Actor:
    actor = _api_key_actor(request)
    if not actor: raise HTTPException(401, "An X-Recall-Key is required for this operation.")
    return actor


def require_generation_access(request: Request, *, consume_public_quota: bool = True) -> Actor:
    workspace_actor = getattr(request.state, "workspace_actor", None)
    if workspace_actor:
        return Actor("workspace", workspace_actor)
    actor = _api_key_actor(request)
    if actor:
        return actor
    if not config.RECALL_ALLOW_PUBLIC_GENERATE:
        raise HTTPException(401, "An X-Recall-Key is required to generate media.")
    ip = _client_ip(request)
    if not consume_public_quota:
        return Actor("public_demo", f"ip_{hashlib.sha256(ip.encode()).hexdigest()[:10]}")
    allowed, remaining = limiter.allow(f"public:{ip}", config.RECALL_PUBLIC_GENERATIONS_PER_HOUR)
    if not allowed:
        raise HTTPException(429, "Public demo generation limit reached. Try later or use an X-Recall-Key.")
    return Actor("public_demo", f"ip_{hashlib.sha256(ip.encode()).hexdigest()[:10]}_remaining_{remaining}")