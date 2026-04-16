from __future__ import annotations

import time
from contextlib import contextmanager

from django.http import HttpResponse

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
except Exception:  # pragma: no cover
    Counter = None
    Histogram = None
    CONTENT_TYPE_LATEST = "text/plain"
    generate_latest = None


if Counter and Histogram:
    SYNC_TOTAL = Counter(
        "sync_operations_total",
        "Total sync operations",
        ["entity", "endpoint", "result"],
    )
    SYNC_LATENCY = Histogram(
        "sync_endpoint_latency_seconds",
        "Latency of sync endpoints",
        ["entity", "endpoint"],
    )
else:  # pragma: no cover
    SYNC_TOTAL = None
    SYNC_LATENCY = None


def increment_sync_total(entity: str, endpoint: str, result: str) -> None:
    if SYNC_TOTAL:
        SYNC_TOTAL.labels(entity=entity, endpoint=endpoint, result=result).inc()


def observe_latency_seconds(entity: str, endpoint: str, seconds: float) -> None:
    if SYNC_LATENCY:
        SYNC_LATENCY.labels(entity=entity, endpoint=endpoint).observe(seconds)


@contextmanager
def timed_operation(entity: str, endpoint: str):
    start = time.monotonic()
    try:
        yield
    finally:
        observe_latency_seconds(entity=entity, endpoint=endpoint, seconds=(time.monotonic() - start))


def metrics_view(_request):
    if not generate_latest:
        return HttpResponse("prometheus-client not installed", status=503)
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
