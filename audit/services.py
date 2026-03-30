"""
Micro-passo B6 — Auditoria (tutorial guiado)

Objetivo:
	Registar no SyncLog as decisões do plano e os erros da execução.

Como usar este tutorial:
	- Faz 1 exercício de cada vez.
	- Corre um teste rápido após cada exercício.
	- No fim, integra em B7 (task sync_customers).
"""

from __future__ import annotations

from typing import Any

from audit.models import SyncLog
from state.models import Company


def _action_to_direction(action: str) -> str:
	
	if action in {'create_in_toc', 'update_toc_from_odoo', 'delete_in_toc'}:
		return SyncLog.Direction.ODOO_TO_TOC
	elif action in {'create_in_odoo', 'update_odoo_from_toc', 'delete_in_odoo'}:
		return SyncLog.Direction.TOC_TO_ODOO
	else:
		return SyncLog.Direction.ODOO_TO_TOC # default para "skip" e outros casos


def _action_to_status(action: str, has_error: bool = False) -> str:
	
  if has_error:
    return SyncLog.Status.ERROR
  elif action == "skip":
    return SyncLog.Status.SKIPPED
  else: return SyncLog.Status.OK


def log_plan_decisions(company: Company, plan: dict[str, Any], dry_run: bool = True) -> int:
	
	for decision in plan.get('decisions', []):
		action = decision.get('action', 'unknown')
		reason = decision.get('reason', '')
		odoo_id = decision.get('odoo_id')
		toconline_id = decision.get('toconline_id')
		direction = _action_to_direction(action)
		status = _action_to_status(action)
		request_payload = {
      'action': action,
      'reason': reason,
      'odoo_id': odoo_id,
      'toconline_id': toconline_id,
      'dry_run': dry_run,
    }
		SyncLog.objects.create(
      company=company,
			entity_type="customer",
      direction=direction,
      status=status,
      request_payload=request_payload,
    )
	return len(plan.get('decisions', []))


def log_apply_errors(company: Company, result: dict[str, Any], dry_run: bool = False) -> int:
	
	errors = result.get("errors", [])
	
	for error_item in errors:
		action = error_item.get("action", "unknown")
		odoo_id = error_item.get("odoo_id")
		toconline_id = error_item.get("toconline_id")
		error_message = error_item.get("error", "")
		direction = _action_to_direction(action)
		SyncLog.objects.create(
      company=company,
      entity_type="customer",
      direction=direction,
      status=SyncLog.Status.ERROR,
      error_message=error_message,
      request_payload={
        'action': action,
        'odoo_id': odoo_id,
        'toconline_id': toconline_id,
        'dry_run': dry_run,
      },
    )
	return len(errors)


def log_sync_failure(company: Company | None, error: Exception, context: dict[str, Any] | None = None) -> SyncLog:
	
  return SyncLog.objects.create(
    company=company,
		entity_type="customer",
    direction=SyncLog.Direction.ODOO_TO_TOC,  # ou TOC_TO_ODOO, dependendo do contexto
    status=SyncLog.Status.ERROR,
    error_message=str(error),
    request_payload=context or {},
  )