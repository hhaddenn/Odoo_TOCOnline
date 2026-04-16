from __future__ import annotations

import traceback
from typing import Any

from state.models import DeadLetterEntry


def publish_dead_letter(
    *,
    company_id: int,
    entity_type: str,
    operation: str,
    error: Exception,
    payload: dict[str, Any] | None = None,
    endpoint: str = "",
    retry_count: int = 0,
) -> DeadLetterEntry:
    return DeadLetterEntry.objects.create(
        company_id=company_id,
        entity_type=entity_type,
        operation=operation,
        endpoint=endpoint,
        payload=payload or {},
        error_message=str(error)[:2000],
        stack_trace="".join(traceback.format_exception(type(error), error, error.__traceback__))[:20000],
        retry_count=retry_count,
    )
