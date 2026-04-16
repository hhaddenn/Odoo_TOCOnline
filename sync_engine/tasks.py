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


@shared_task(bind=True, max_retries=0)
def sync_products(self, company_id: int, dry_run: bool = True, allow_delete: bool = False) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_products import OdooProductsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_products import TOCProductsConnector
    from state.models import Company
    from sync_engine.product_sync import ProductSync

    company = Company.objects.get(id=company_id, is_active=True)
    engine = ProductSync(
        company=company,
        odoo_connector=OdooProductsConnector(client=odoo_client_from_env()),
        toconline_connector=TOCProductsConnector(api_client=client_from_company(company)),
    )
    return engine.run(dry_run=dry_run, allow_delete=allow_delete)


@shared_task(bind=True, max_retries=0)
def sync_suppliers(self, company_id: int, dry_run: bool = True, allow_delete: bool = False) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_suppliers import OdooSuppliersConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_suppliers import TOCSuppliersConnector
    from state.models import Company
    from sync_engine.supplier_sync import SupplierSync

    company = Company.objects.get(id=company_id, is_active=True)
    engine = SupplierSync(
        company=company,
        odoo_connector=OdooSuppliersConnector(client=odoo_client_from_env()),
        toconline_connector=TOCSuppliersConnector(api_client=client_from_company(company)),
    )
    return engine.run(dry_run=dry_run, allow_delete=allow_delete)


@shared_task(bind=True, max_retries=0)
def sync_sales_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_sales_documents import OdooSalesDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_sales_documents import TOCSalesDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import sales_documents
    from sync_engine.document_sync import DocumentSyncEngine

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


@shared_task(bind=True, max_retries=0)
def sync_purchase_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_purchase_documents import OdooPurchaseDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_purchase_documents import TOCPurchaseDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import purchase_documents
    from sync_engine.document_sync import DocumentSyncEngine

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


@shared_task(bind=True, max_retries=0)
def sync_rectificative_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_rectificative_documents import OdooRectificativeDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_rectificative_documents import TOCRectificativeDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import rectificative_documents
    from sync_engine.document_sync import DocumentSyncEngine

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


@shared_task(bind=True, max_retries=0)
def sync_shipment_documents(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_shipment_documents import OdooShipmentDocumentsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_shipment_documents import TOCShipmentDocumentsConnector
    from state.models import Company
    from sync_engine.mappers import shipment_documents
    from sync_engine.document_sync import DocumentSyncEngine

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


@shared_task(bind=True, max_retries=0)
def sync_sales_receipts(self, company_id: int, dry_run: bool = True) -> dict:
    from connectors.odoo_client import client_from_env as odoo_client_from_env
    from connectors.odoo_sales_receipts import OdooSalesReceiptsConnector
    from connectors.toconline_client import client_from_company
    from connectors.toconline_sales_receipts import TOCSalesReceiptsConnector
    from state.models import Company
    from sync_engine.mappers import sales_receipts
    from sync_engine.document_sync import DocumentSyncEngine

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