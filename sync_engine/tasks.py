from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Callable

from celery import shared_task
from django.conf import settings
from django.utils import timezone
import httpx

from sync_engine.dead_letter import publish_dead_letter
from sync_engine.idempotency import idempotent_operation
from sync_engine.metrics import increment_sync_total, timed_operation
from sync_engine.retry import (
    CircuitBreakerOpenError,
    circuit_breaker_preflight,
    circuit_breaker_record_failure,
    circuit_breaker_record_success,
)

logger = logging.getLogger(__name__)


def _is_retryable_http_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return True
    return bool(exc.response.status_code >= 500 or exc.response.status_code == 429)


def _run_hardened(
    *,
    company_id: int,
    entity_type: str,
    operation: str,
    payload: dict[str, Any],
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    scope = f"company:{company_id}:operation:{operation}"
    endpoint = f"task:{operation}"

    try:
        circuit_breaker_preflight(scope)
    except CircuitBreakerOpenError as exc:
        increment_sync_total(entity_type, endpoint, "blocked")
        raise exc

    with idempotent_operation(company_id, entity_type, operation, payload=payload) as idem:
        if idem.is_duplicate:
            increment_sync_total(entity_type, endpoint, "duplicate")
            return {
                "status": "skipped",
                "reason": "duplicate_operation",
                "idempotency_key": idem.key,
                "company_id": company_id,
                "operation": operation,
            }

        try:
            with timed_operation(entity_type, endpoint):
                result = fn()
        except Exception as exc:
            circuit_breaker_record_failure(scope)
            increment_sync_total(entity_type, endpoint, "error")
            publish_dead_letter(
                company_id=company_id,
                entity_type=entity_type,
                operation=operation,
                endpoint=endpoint,
                payload=payload,
                error=exc,
                retry_count=int(getattr(settings, "SYNC_HTTP_MAX_RETRIES", 3)),
            )
            raise

        circuit_breaker_record_success(scope)
        increment_sync_total(entity_type, endpoint, "success")
        if isinstance(result, dict):
            result.setdefault("idempotency_key", idem.key)
        return result


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
    from audit.models import SyncLog
    from connectors.odoo_client import OdooClient
    from state.models import CompanyConnection

    def _execute() -> dict:
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
            raise
        finally:
            client.close()

    try:
        return _run_hardened(
            company_id=company_id,
            entity_type="health",
            operation="health_check_odoo",
            payload={"company_id": company_id},
            fn=_execute,
        )
    except Exception as exc:
        if _is_retryable_http_error(exc):
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def health_check_toconline(self, company_id: int) -> dict:
    from audit.models import SyncLog
    from connectors.toconline_client import client_from_company
    from state.models import Company, CompanyConnection

    def _execute() -> dict:
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
            raise
        finally:
            client.close()

    try:
        return _run_hardened(
            company_id=company_id,
            entity_type="health",
            operation="health_check_toconline",
            payload={"company_id": company_id},
            fn=_execute,
        )
    except Exception as exc:
        if _is_retryable_http_error(exc):
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def force_refresh_toconline_token(self, company_id: int) -> dict:
    from connectors.toconline_client import client_from_company
    from state.models import Company, CompanyConnection

    def _execute() -> dict:
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
            conn.rotated_at = timezone.now()
            conn.save(update_fields=["rotated_at", "updated_at"])
            return {
                "status": "ok",
                "company_id": company_id,
                "has_access_token": bool(conn.credentials.get("access_token")),
                "has_refresh_token": bool(conn.credentials.get("refresh_token")),
                "access_token_expires_at": conn.credentials.get("access_token_expires_at"),
                "rotated_at": conn.rotated_at.isoformat() if conn.rotated_at else None,
            }
        finally:
            client.close()

    try:
        return _run_hardened(
            company_id=company_id,
            entity_type="token",
            operation="force_refresh_toconline_token",
            payload={"company_id": company_id},
            fn=_execute,
        )
    except Exception as exc:
        if _is_retryable_http_error(exc):
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=0)
def force_refresh_all_toconline_tokens(self) -> dict:
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

    def _execute() -> dict:
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
                raise
        except Exception as exc:
            log_sync_failure(None, exc)
            raise

    return _run_hardened(
        company_id=company_id,
        entity_type="customer",
        operation="sync_customers",
        payload={"company_id": company_id, "dry_run": dry_run, "allow_delete": allow_delete},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_products(self, company_id: int, dry_run: bool = True, allow_delete: bool = False) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_products import OdooProductsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_products import TOCProductsConnector
    from state.models import Company
    from sync_engine.product_sync import ProductSync

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = ProductSync(
            company=company,
            odoo_connector=OdooProductsConnector(client=odoo_client_from_env()),
            toconline_connector=TOCProductsConnector(api_client=client_from_company(company)),
        )
        return engine.run(dry_run=dry_run, allow_delete=allow_delete)

    return _run_hardened(
        company_id=company_id,
        entity_type="product",
        operation="sync_products",
        payload={"company_id": company_id, "dry_run": dry_run, "allow_delete": allow_delete},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_suppliers(self, company_id: int, dry_run: bool = True, allow_delete: bool = False) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_suppliers import OdooSuppliersConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_suppliers import TOCSuppliersConnector
    from state.models import Company
    from sync_engine.supplier_sync import SupplierSync

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = SupplierSync(
            company=company,
            odoo_connector=OdooSuppliersConnector(client=odoo_client_from_env()),
            toconline_connector=TOCSuppliersConnector(api_client=client_from_company(company)),
        )
        return engine.run(dry_run=dry_run, allow_delete=allow_delete)

    return _run_hardened(
        company_id=company_id,
        entity_type="supplier",
        operation="sync_suppliers",
        payload={"company_id": company_id, "dry_run": dry_run, "allow_delete": allow_delete},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_sales_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_sales_documents import OdooSalesDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_sales_documents import TOCSalesDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import sales_documents
    from sync_engine.document_sync import DocumentSyncEngine

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = DocumentSyncEngine(
            odoo_connector=OdooSalesDocumentsConnector(client=odoo_client_from_env()),
            toc_connector=TOCSalesDocumentsConnector(api_client=client_from_company(company)),
            mapper=sales_documents,
            logger=logger,
        )
        return {
            "status": "SUCCESS",
            "document_type": "sales_invoice",
            "company_id": company_id,
            "dry_run": dry_run,
            **engine.run(document_type="sales_invoice", dry_run=dry_run),
        }

    return _run_hardened(
        company_id=company_id,
        entity_type="document",
        operation="sync_sales_documents",
        payload={"company_id": company_id, "dry_run": dry_run},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_purchase_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_purchase_documents import OdooPurchaseDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_purchase_documents import TOCPurchaseDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import purchase_documents
    from sync_engine.document_sync import DocumentSyncEngine

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = DocumentSyncEngine(
            odoo_connector=OdooPurchaseDocumentsConnector(client=odoo_client_from_env()),
            toc_connector=TOCPurchaseDocumentsConnector(api_client=client_from_company(company)),
            mapper=purchase_documents,
            logger=logger,
        )
        return {
            "status": "SUCCESS",
            "document_type": "purchase_invoice",
            "company_id": company_id,
            "dry_run": dry_run,
            **engine.run(document_type="purchase_invoice", dry_run=dry_run),
        }

    return _run_hardened(
        company_id=company_id,
        entity_type="document",
        operation="sync_purchase_documents",
        payload={"company_id": company_id, "dry_run": dry_run},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_rectificative_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_rectificative_documents import OdooRectificativeDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_rectificative_documents import TOCRectificativeDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import rectificative_documents
    from sync_engine.document_sync import DocumentSyncEngine

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = DocumentSyncEngine(
            odoo_connector=OdooRectificativeDocumentsConnector(client=odoo_client_from_env()),
            toc_connector=TOCRectificativeDocumentsConnector(api_client=client_from_company(company)),
            mapper=rectificative_documents,
            logger=logger,
        )
        return {
            "status": "SUCCESS",
            "document_type": "rectificative_document",
            "company_id": company_id,
            "dry_run": dry_run,
            **engine.run(document_type="rectificative_document", dry_run=dry_run),
        }

    return _run_hardened(
        company_id=company_id,
        entity_type="document",
        operation="sync_rectificative_documents",
        payload={"company_id": company_id, "dry_run": dry_run},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_shipment_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_shipment_documents import OdooShipmentDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_shipment_documents import TOCShipmentDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import shipment_documents
    from sync_engine.document_sync import DocumentSyncEngine

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = DocumentSyncEngine(
            odoo_connector=OdooShipmentDocumentsConnector(client=odoo_client_from_env()),
            toc_connector=TOCShipmentDocumentsConnector(client=client_from_company(company)),
            mapper=shipment_documents,
            logger=logger,
        )
        return {
            "status": "SUCCESS",
            "document_type": "shipment_document",
            "company_id": company_id,
            "dry_run": dry_run,
            **engine.run(document_type="shipment_document", dry_run=dry_run),
        }

    return _run_hardened(
        company_id=company_id,
        entity_type="document",
        operation="sync_shipment_documents",
        payload={"company_id": company_id, "dry_run": dry_run},
        fn=_execute,
    )


@shared_task(bind=True, max_retries=0)
def sync_sales_receipts(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_sales_receipts import OdooSalesReceiptsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_sales_receipts import TOCSalesReceiptsConnector
    from state.models import Company
    from sync_engine.mappers import sales_receipts
    from sync_engine.document_sync import DocumentSyncEngine

    def _execute() -> dict:
        company = Company.objects.get(id=company_id, is_active=True)
        engine = DocumentSyncEngine(
            odoo_connector=OdooSalesReceiptsConnector(client=odoo_client_from_env()),
            toc_connector=TOCSalesReceiptsConnector(client=client_from_company(company)),
            mapper=sales_receipts,
            logger=logger,
        )
        return {
            "status": "SUCCESS",
            "document_type": "sales_receipt",
            "company_id": company_id,
            "dry_run": dry_run,
            **engine.run(document_type="sales_receipt", dry_run=dry_run),
        }

    return _run_hardened(
        company_id=company_id,
        entity_type="document",
        operation="sync_sales_receipts",
        payload={"company_id": company_id, "dry_run": dry_run},
        fn=_execute,
    )


DOCUMENT_SYNC_TASKS = {
    "sales_invoice": sync_sales_documents,
    "purchase_invoice": sync_purchase_documents,
    "rectificative_document": sync_rectificative_documents,
    "shipment_document": sync_shipment_documents,
    "sales_receipt": sync_sales_receipts,
}


@shared_task(bind=True, max_retries=0)
def sync_documents_by_type(self, company_id: int, document_type: str, dry_run: bool = True) -> dict:
    from state.models import Company

    if not Company.objects.filter(id=company_id, is_active=True).exists():
        return {
            "status": "ERROR",
            "message": f"Empresa {company_id} não encontrada ou inativa",
            "company_id": company_id,
            "document_type": document_type,
            "dry_run": dry_run,
        }

    sync_task = DOCUMENT_SYNC_TASKS.get(document_type)
    if sync_task is None:
        return {
            "status": "ERROR",
            "message": f"Tipo documental inválido: {document_type}",
            "company_id": company_id,
            "document_type": document_type,
            "dry_run": dry_run,
            "supported_types": sorted(DOCUMENT_SYNC_TASKS.keys()),
        }

    result = sync_task.run(company_id=company_id, dry_run=dry_run)
    return {
        "status": "SUCCESS",
        "company_id": company_id,
        "document_type": document_type,
        "dry_run": dry_run,
        **result,
    }


@shared_task(bind=True, max_retries=0)
def sync_all_document_types(self, company_id: int, dry_run: bool = True) -> dict:
    from state.models import Company

    if not Company.objects.filter(id=company_id, is_active=True).exists():
        return {
            "status": "ERROR",
            "message": f"Empresa {company_id} não encontrada ou inativa",
            "company_id": company_id,
            "dry_run": dry_run,
        }

    per_type = {}
    totals = {"total": 0, "creates": 0, "updates": 0, "skips": 0}

    for doc_type, sync_task in DOCUMENT_SYNC_TASKS.items():
        result = sync_task.run(company_id=company_id, dry_run=dry_run)
        per_type[doc_type] = result
        summary = result.get("summary", {})
        totals["total"] += int(summary.get("total", 0) or 0)
        totals["creates"] += int(summary.get("creates", 0) or 0)
        totals["updates"] += int(summary.get("updates", 0) or 0)
        totals["skips"] += int(summary.get("skips", 0) or 0)

    return {
        "status": "SUCCESS",
        "company_id": company_id,
        "dry_run": dry_run,
        "results_by_type": per_type,
        "summary": totals,
    }


@shared_task(bind=True, max_retries=0)
def evaluate_sync_alerts(self) -> dict:
    from audit.models import SyncAlert, SyncLog
    from state.models import CompanyConnection

    window_minutes = int(getattr(settings, "SYNC_ALERT_WINDOW_MINUTES", 15))
    failure_threshold = float(getattr(settings, "SYNC_ALERT_FAILURE_RATE_THRESHOLD", 0.2))
    since = timezone.now() - timedelta(minutes=window_minutes)

    scoped_logs = SyncLog.objects.filter(created_at__gte=since).values("company_id")
    company_ids = sorted({row["company_id"] for row in scoped_logs if row["company_id"]})
    alerts_created = 0

    for company_id in company_ids:
        logs = SyncLog.objects.filter(company_id=company_id, created_at__gte=since)
        total = logs.count()
        if total == 0:
            continue

        failures = logs.filter(status=SyncLog.Status.ERROR).count()
        failure_rate = failures / total
        if failure_rate >= failure_threshold:
            SyncAlert.objects.create(
                company_id=company_id,
                alert_type=SyncAlert.AlertType.FAILURE_RATE,
                message=f"Failure rate {failure_rate:.2%} in last {window_minutes}m",
                context={
                    "window_minutes": window_minutes,
                    "failure_rate": failure_rate,
                    "total": total,
                    "failures": failures,
                },
            )
            alerts_created += 1

    expired_conns = CompanyConnection.objects.filter(
        system=CompanyConnection.SystemType.TOCONLINE,
        is_active=True,
    )
    now_ts = timezone.now().timestamp()
    for conn in expired_conns:
        expires_at = (conn.credentials or {}).get("access_token_expires_at")
        if not expires_at:
            continue
        try:
            expires_at_float = float(expires_at)
        except (TypeError, ValueError):
            continue

        if expires_at_float < now_ts and (timezone.now() - conn.updated_at) > timedelta(minutes=window_minutes):
            SyncAlert.objects.create(
                company=conn.company,
                alert_type=SyncAlert.AlertType.TOKEN_EXPIRED,
                message="TOConline token expired and no refresh detected in alert window",
                context={
                    "expires_at": expires_at_float,
                    "updated_at": conn.updated_at.isoformat(),
                },
            )
            alerts_created += 1

    return {"status": "ok", "alerts_created": alerts_created, "window_minutes": window_minutes}


@shared_task(bind=True, max_retries=0)
def purge_old_sync_logs(self) -> dict:
    from audit.models import SyncLog

    retention_days = int(getattr(settings, "SYNC_LOG_RETENTION_DAYS", 180))
    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted, _ = SyncLog.objects.filter(created_at__lt=cutoff).delete()
    return {"status": "ok", "deleted": deleted, "retention_days": retention_days}


@shared_task(bind=True, max_retries=0)
def reprocess_dead_letters(self, limit: int = 50) -> dict:
    from state.models import DeadLetterEntry

    entries = DeadLetterEntry.objects.filter(is_reprocessed=False).order_by("created_at")[:limit]
    processed = 0
    for entry in entries:
        # Minimal workflow for now: mark as reprocessed to support manual replay loops.
        entry.is_reprocessed = True
        entry.reprocessed_at = timezone.now()
        entry.save(update_fields=["is_reprocessed", "reprocessed_at"])
        processed += 1

    return {"status": "ok", "processed": processed, "limit": limit}