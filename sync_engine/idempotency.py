from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from state.models import IdempotencyKey


@dataclass(frozen=True)
class IdempotencyResult:
    is_duplicate: bool
    key: str


def build_key(company_id: int, entity_type: str, operation: str, payload: dict[str, Any] | None = None) -> str:
    canonical_payload = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    raw = f"{company_id}:{entity_type}:{operation}:{canonical_payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def idempotent_operation(company_id: int, entity_type: str, operation: str, payload: dict[str, Any] | None = None):
    key = build_key(company_id, entity_type, operation, payload=payload)

    with transaction.atomic():
        obj, created = IdempotencyKey.objects.select_for_update().get_or_create(
            key=key,
            defaults={
                "company_id": company_id,
                "entity_type": entity_type,
                "operation": operation,
                "payload": payload or {},
                "status": IdempotencyKey.Status.IN_PROGRESS,
            },
        )
        if (not created) and obj.status == IdempotencyKey.Status.COMPLETED:
            yield IdempotencyResult(is_duplicate=True, key=key)
            return

        if not created:
            obj.status = IdempotencyKey.Status.IN_PROGRESS
            obj.payload = payload or obj.payload
            obj.last_error = ""
            obj.save(update_fields=["status", "payload", "last_error", "updated_at"])

    try:
        yield IdempotencyResult(is_duplicate=False, key=key)
    except Exception as exc:
        IdempotencyKey.objects.filter(key=key).update(
            status=IdempotencyKey.Status.FAILED,
            attempt_count=F("attempt_count") + 1,
            last_error=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        raise
    else:
        IdempotencyKey.objects.filter(key=key).update(
            status=IdempotencyKey.Status.COMPLETED,
            attempt_count=F("attempt_count") + 1,
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
