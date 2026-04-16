from __future__ import annotations

import random
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def _setting(name: str, default):
    try:
        return getattr(settings, name, default)
    except Exception:
        return default


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = _setting("SYNC_HTTP_MAX_RETRIES", 3)
    backoff_base_seconds: float = _setting("SYNC_HTTP_BACKOFF_BASE_SECONDS", 1.0)
    backoff_max_seconds: float = _setting("SYNC_HTTP_BACKOFF_MAX_SECONDS", 30.0)
    jitter_seconds: float = _setting("SYNC_HTTP_BACKOFF_JITTER_SECONDS", 0.25)


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = _setting("SYNC_BREAKER_FAILURE_THRESHOLD", 5)
    cooldown_seconds: int = _setting("SYNC_BREAKER_COOLDOWN_SECONDS", 60)


def should_retry_http_status(status_code: int) -> bool:
    return status_code in RETRYABLE_HTTP_STATUSES


def calculate_backoff_seconds(attempt: int, policy: RetryPolicy | None = None) -> float:
    cfg = policy or RetryPolicy()
    expo = cfg.backoff_base_seconds * (2 ** max(0, attempt - 1))
    capped = min(expo, cfg.backoff_max_seconds)
    jitter = random.uniform(0.0, cfg.jitter_seconds)
    return capped + jitter


def sleep_with_backoff(attempt: int, policy: RetryPolicy | None = None) -> None:
    time.sleep(calculate_backoff_seconds(attempt, policy=policy))


class CircuitBreakerOpenError(RuntimeError):
    pass


def _breaker_key(scope: str) -> str:
    return f"sync:breaker:{scope}"


def _now_ts() -> float:
    return timezone.now().timestamp()


def circuit_breaker_preflight(scope: str, policy: CircuitBreakerPolicy | None = None) -> None:
    cfg = policy or CircuitBreakerPolicy()
    key = _breaker_key(scope)
    state = cache.get(key) or {"state": "closed", "consecutive_failures": 0, "opened_at": None}

    if state["state"] == "open":
        opened_at = float(state.get("opened_at") or 0.0)
        elapsed = _now_ts() - opened_at
        if elapsed < cfg.cooldown_seconds:
            remaining = int(cfg.cooldown_seconds - elapsed)
            raise CircuitBreakerOpenError(f"Circuit breaker OPEN for {scope} (cooldown {remaining}s)")
        state["state"] = "half_open"
        cache.set(key, state, timeout=cfg.cooldown_seconds * 4)


def circuit_breaker_record_success(scope: str, policy: CircuitBreakerPolicy | None = None) -> None:
    cfg = policy or CircuitBreakerPolicy()
    key = _breaker_key(scope)
    cache.set(
        key,
        {"state": "closed", "consecutive_failures": 0, "opened_at": None},
        timeout=cfg.cooldown_seconds * 4,
    )


def circuit_breaker_record_failure(scope: str, policy: CircuitBreakerPolicy | None = None) -> None:
    cfg = policy or CircuitBreakerPolicy()
    key = _breaker_key(scope)
    state = cache.get(key) or {"state": "closed", "consecutive_failures": 0, "opened_at": None}
    failures = int(state.get("consecutive_failures") or 0) + 1
    next_state = "open" if failures >= cfg.failure_threshold else "closed"
    cache.set(
        key,
        {"state": next_state, "consecutive_failures": failures, "opened_at": _now_ts() if next_state == "open" else None},
        timeout=cfg.cooldown_seconds * 4,
    )
