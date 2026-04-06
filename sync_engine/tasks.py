"""
Tarefas Celery de sync — Fase A stub.
Expandidas nas fases B/C/D.
"""
from __future__ import annotations

import logging
import time

from celery import shared_task
from django.utils import timezone
import httpx

logger = logging.getLogger(__name__)


def _persist_toconline_tokens(conn, access_token, refresh_token, token_url=None, access_token_expires_at=None):
    conn.credentials = conn.credentials or {}
    conn.credentials["access_token"] = access_token
    conn.credentials["refresh_token"] = refresh_token
    if token_url:
        conn.credentials["token_url"] = token_url
    if access_token_expires_at is not None:
        conn.credentials["access_token_expires_at"] = access_token_expires_at
    conn.save(update_fields=["credentials", "updated_at"])


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def health_check_odoo(self, company_id: int) -> dict:
    """Verifica conectividade com Odoo para uma empresa."""
    from audit.models import SyncLog
    from connectors.odoo_client import OdooClient
    from state.models import CompanyConnection

    conn = CompanyConnection.objects.get(
        company_id=company_id,
        system=CompanyConnection.SystemType.ODOO,
        is_active=True,
    )
    creds = conn.credentials
    client = OdooClient(
        base_url=conn.base_url,
        db=creds["db"],
        username=creds["username"],
        password=creds["password"],
    )
    t0 = time.monotonic()
    try:
        result = client.health_check()
        duration = int((time.monotonic() - t0) * 1000)
        conn.last_tested_at = timezone.now()
        conn.save(update_fields=["last_tested_at"])
        SyncLog.objects.create(
            company_id=company_id,
            entity_type="__health__",
            direction=SyncLog.Direction.ODOO_TO_TOC,
            status=SyncLog.Status.OK,
            response_payload=result,
            duration_ms=duration,
        )
        logger.info("Odoo health check OK (company=%s, %dms)", company_id, duration)
        return {"status": "ok", "duration_ms": duration}
    except Exception as exc:
        SyncLog.objects.create(
            company_id=company_id,
            entity_type="__health__",
            direction=SyncLog.Direction.ODOO_TO_TOC,
            status=SyncLog.Status.ERROR,
            error_message=str(exc),
        )
        if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
            raise exc
        raise self.retry(exc=exc)
    finally:
        client.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def health_check_toconline(self, company_id: int) -> dict:
    """Verifica conectividade com TOConline para uma empresa."""
    from audit.models import SyncLog
    from connectors.toconline_client import client_from_company
    from state.models import Company, CompanyConnection

    conn = CompanyConnection.objects.get(
        company_id=company_id,
        system=CompanyConnection.SystemType.TOCONLINE,
        is_active=True,
    )
    company = Company.objects.get(id=company_id, is_active=True)

    def on_token_refresh(access_token, refresh_token, token_url=None, access_token_expires_at=None):
        _persist_toconline_tokens(
            conn,
            access_token,
            refresh_token,
            token_url=token_url,
            access_token_expires_at=access_token_expires_at,
        )

    client = client_from_company(company, on_token_refresh=on_token_refresh)
    t0 = time.monotonic()
    try:
        result = client.health_check()
        duration = int((time.monotonic() - t0) * 1000)
        conn.last_tested_at = timezone.now()
        conn.save(update_fields=["last_tested_at"])
        SyncLog.objects.create(
            company_id=company_id,
            entity_type="__health__",
            direction=SyncLog.Direction.TOC_TO_ODOO,
            status=SyncLog.Status.OK,
            response_payload=result,
            duration_ms=duration,
        )
        logger.info("TOConline health check OK (company=%s, %dms)", company_id, duration)
        return {"status": "ok", "duration_ms": duration}
    except Exception as exc:
        SyncLog.objects.create(
            company_id=company_id,
            entity_type="__health__",
            direction=SyncLog.Direction.TOC_TO_ODOO,
            status=SyncLog.Status.ERROR,
            error_message=str(exc),
        )
        if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
            raise exc
        raise self.retry(exc=exc)
    finally:
        client.close()


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def force_refresh_toconline_token(self, company_id: int) -> dict:
    """Força refresh do token TOConline para uma empresa (hard refresh)."""
    from connectors.toconline_client import client_from_company
    from state.models import Company, CompanyConnection

    conn = CompanyConnection.objects.get(
        company_id=company_id,
        system=CompanyConnection.SystemType.TOCONLINE,
        is_active=True,
    )
    company = Company.objects.get(id=company_id, is_active=True)

    def on_token_refresh(access_token, refresh_token, token_url=None, access_token_expires_at=None):
        _persist_toconline_tokens(
            conn,
            access_token,
            refresh_token,
            token_url=token_url,
            access_token_expires_at=access_token_expires_at,
        )

    client = client_from_company(company, on_token_refresh=on_token_refresh)
    try:
        client.authenticate(force_refresh=True)
        return {
            "status": "ok",
            "company_id": company_id,
            "has_access_token": bool(conn.credentials.get("access_token")),
            "has_refresh_token": bool(conn.credentials.get("refresh_token")),
            "access_token_expires_at": conn.credentials.get("access_token_expires_at"),
        }
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
            raise exc
        raise self.retry(exc=exc)
    finally:
        client.close()


@shared_task(bind=True, max_retries=0)
def force_refresh_all_toconline_tokens(self) -> dict:
    """Força refresh do token TOConline para todas as empresas ativas com ligação ativa."""
    from state.models import CompanyConnection

    connections = CompanyConnection.objects.filter(
        system=CompanyConnection.SystemType.TOCONLINE,
        is_active=True,
        company__is_active=True,
    ).select_related("company")

    refreshed = 0
    failed = []
    for conn in connections:
        try:
            force_refresh_toconline_token(company_id=conn.company_id)
            refreshed += 1
        except Exception as exc:
            failed.append({"company_id": conn.company_id, "error": str(exc)})

    return {
        "status": "ok" if not failed else "partial",
        "refreshed": refreshed,
        "failed": failed,
    }


@shared_task(bind=True, max_retries=0)
def sync_customers(self, company_id: int, dry_run: bool = True, allow_delete: bool = False) -> dict:
    from audit.services import log_apply_errors, log_plan_decisions, log_sync_failure
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_customers import OdooCustomerConnector
    from connectors.toconline_customer import TOCCustomerConnector
    from state.models import Company
    from sync_engine.customer_sync import CustomerSyncEngine

    try:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = CustomerSyncEngine(
            company=company,
            odoo_connector=OdooCustomerConnector(client=odoo_client_from_env()),
            toconline_connector=TOCCustomerConnector(company=company),
        )
        plan = engine.plan_sync(allow_delete=allow_delete)
        logged_decisions = log_plan_decisions(company, plan, dry_run=dry_run)

        if dry_run:
            return {
                "mode": "dry_run",
                "company_id": company_id,
                "logged_decisions": logged_decisions,
                "plan": plan,
            }
        else:
            try:
                result = engine.apply_decisions(plan, dry_run=False)
                logged_errors = log_apply_errors(company, result, dry_run=False)
                return {
                    "mode": "apply",
                    "company_id": company_id,
                    "result": result,
                    "logged_errors": logged_errors,
                }
            except Exception as exc:
                log_sync_failure(company, exc, context={"plan": plan})
                raise exc
    except Exception as exc:
        log_sync_failure(None, exc)
        raise exc