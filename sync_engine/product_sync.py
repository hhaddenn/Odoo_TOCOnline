from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any

from django.utils import timezone

from connectors.odoo_client import client_from_env as odoo_client_from_env
from connectors.odoo_products import OdooProductsConnector
from connectors.toconline_client import client_from_company, client_from_env as toconline_client_from_env
from connectors.toconline_products import TOCProductsConnector
from state.models import Company, EntityLink
from sync_engine.mappers.products import (
    canonical_to_toconline_product_payload,
    compare_products,
    odoo_product_to_canonical,
    toc_product_to_canonical,
)


class SyncAction(str, Enum):
    CREATE_IN_TOC = "create_in_toc"
    UPDATE_TOC_FROM_ODOO = "update_toc_from_odoo"
    DELETE_IN_TOC = "delete_in_toc"
    SKIP = "skip"


@dataclass
class SyncDecision:
    action: SyncAction
    reason: str
    odoo_product: dict[str, Any] | None = None
    toc_product: dict[str, Any] | None = None
    odoo_id: int | None = None
    toc_id: str | None = None
    differences: dict[str, dict[str, Any]] | None = None


class ProductSync:
    def __init__(
        self,
        company: Company | None = None,
        odoo_connector: OdooProductsConnector | None = None,
        toconline_connector: TOCProductsConnector | None = None,
    ) -> None:
        self.company = company
        self.odoo_connector = odoo_connector or OdooProductsConnector(client=odoo_client_from_env())
        toconline_client = client_from_company(company) if company else toconline_client_from_env()
        self.toconline_connector = toconline_connector or TOCProductsConnector(api_client=toconline_client)

    def _extract_toc_products(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        return []

    def _normalize_code(self, value: Any) -> str:
        return str(value or "").strip()

    def _canonical_hash(self, canonical_product: dict[str, Any]) -> str:
        payload = json.dumps(canonical_product, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _find_link_by_odoo_id(self, odoo_id: int) -> EntityLink | None:
        if self.company is None:
            return None
        return EntityLink.objects.filter(
            company=self.company,
            entity_type=EntityLink.EntityType.PRODUCT,
            odoo_id=odoo_id,
        ).first()

    def _upsert_entity_link(self, odoo_id: int, toc_id: str, canonical_product: dict[str, Any]) -> None:
        if self.company is None:
            return
        EntityLink.objects.update_or_create(
            company=self.company,
            entity_type=EntityLink.EntityType.PRODUCT,
            odoo_id=odoo_id,
            defaults={
                "toconline_id": str(toc_id),
                "canonical_hash": self._canonical_hash(canonical_product),
                "last_synced_at": timezone.now(),
            },
        )

    def _delete_entity_link(self, odoo_id: int | None = None, toconline_id: str | int | None = None) -> int:
        if self.company is None:
            return 0
        query = EntityLink.objects.filter(
            company=self.company,
            entity_type=EntityLink.EntityType.PRODUCT,
        )
        if odoo_id is not None:
            query = query.filter(odoo_id=odoo_id)
        if toconline_id is not None:
            query = query.filter(toconline_id=str(toconline_id))
        return query.delete()[0]

    def _build_decision(
        self,
        odoo_product: dict[str, Any],
        toc_product: dict[str, Any] | None,
    ) -> SyncDecision:
        canonical = odoo_product_to_canonical(odoo_product)
        code = self._normalize_code(canonical.get("code"))

        if not code:
            return SyncDecision(
                action=SyncAction.SKIP,
                reason="missing default_code",
                odoo_product=odoo_product,
                toc_product=toc_product,
                odoo_id=odoo_product.get("id"),
            )

        if toc_product is None:
            return SyncDecision(
                action=SyncAction.CREATE_IN_TOC,
                reason="no matching TOConline product",
                odoo_product=odoo_product,
                toc_product=None,
                odoo_id=odoo_product.get("id"),
            )

        toc_canonical = toc_product_to_canonical(toc_product)
        diffs = compare_products(canonical, toc_canonical)
        if not diffs:
            return SyncDecision(
                action=SyncAction.SKIP,
                reason="already in sync",
                odoo_product=odoo_product,
                toc_product=toc_product,
                odoo_id=odoo_product.get("id"),
                toc_id=str(toc_product.get("id")) if toc_product.get("id") is not None else None,
            )

        return SyncDecision(
            action=SyncAction.UPDATE_TOC_FROM_ODOO,
            reason="fields differ",
            odoo_product=odoo_product,
            toc_product=toc_product,
            odoo_id=odoo_product.get("id"),
            toc_id=str(toc_product.get("id")) if toc_product.get("id") is not None else None,
            differences=diffs,
        )

    def plan_sync(self, dry_run: bool = True, allow_delete: bool = False) -> dict[str, Any]:
        odoo_products = self.odoo_connector.get_products()
        toc_products = self._extract_toc_products(self.toconline_connector.get_products())

        toc_by_code: dict[str, dict[str, Any]] = {}
        for toc_product in toc_products:
            canonical = toc_product_to_canonical(toc_product)
            code = self._normalize_code(canonical.get("code"))
            if code and code not in toc_by_code:
                toc_by_code[code] = toc_product

        decisions: list[SyncDecision] = []
        created = updated = deleted = skipped = failed = 0
        matched_toc_ids: set[str] = set()

        for odoo_product in odoo_products:
            try:
                canonical = odoo_product_to_canonical(odoo_product)
                code = self._normalize_code(canonical.get("code"))
                if not code:
                    decision = SyncDecision(
                        action=SyncAction.SKIP,
                        reason="missing default_code",
                        odoo_product=odoo_product,
                        odoo_id=odoo_product.get("id"),
                    )
                else:
                    toc_product = toc_by_code.get(code)
                    if toc_product is None:
                        link = self._find_link_by_odoo_id(int(odoo_product.get("id"))) if odoo_product.get("id") is not None else None
                        if link is not None:
                            toc_product = next((item for item in toc_products if str(item.get("id")) == str(link.toconline_id)), None)
                    if toc_product is not None and toc_product.get("id") is not None:
                        matched_toc_ids.add(str(toc_product.get("id")))
                    decision = self._build_decision(odoo_product, toc_product)

                decisions.append(decision)
                if decision.action == SyncAction.CREATE_IN_TOC:
                    created += 1
                elif decision.action == SyncAction.UPDATE_TOC_FROM_ODOO:
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1

        for toc_product in toc_products:
            toc_id = toc_product.get("id")
            if toc_id is None:
                continue
            toc_id_str = str(toc_id)
            if toc_id_str in matched_toc_ids:
                continue

            action = SyncAction.DELETE_IN_TOC if allow_delete else SyncAction.SKIP
            reason = "Only in TOConline: delete by policy" if allow_delete else "Only in TOConline: skip (allow_delete=False)"
            decisions.append(
                SyncDecision(
                    action=action,
                    reason=reason,
                    toc_product=toc_product,
                    toc_id=toc_id_str,
                )
            )
            if action == SyncAction.DELETE_IN_TOC:
                deleted += 1
            else:
                skipped += 1

        return {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "skipped": skipped,
            "failed": failed,
            "total": len(decisions),
            "decisions": decisions,
        }

    def apply_decisions(self, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        decisions: list[SyncDecision] = plan.get("decisions", [])
        created = updated = deleted = skipped = failed = 0

        for decision in decisions:
            try:
                if decision.action == SyncAction.CREATE_IN_TOC:
                    created += 1
                    if not dry_run:
                        canonical = odoo_product_to_canonical(decision.odoo_product or {})
                        payload = canonical_to_toconline_product_payload(canonical)
                        response = self.toconline_connector.create_product(payload)
                        toc_id = str(response.get("id") or response.get("data", {}).get("id") or "")
                        if toc_id:
                            self._upsert_entity_link(int(decision.odoo_id), toc_id, canonical)
                elif decision.action == SyncAction.UPDATE_TOC_FROM_ODOO:
                    updated += 1
                    if not dry_run and decision.toc_id is not None:
                        canonical = odoo_product_to_canonical(decision.odoo_product or {})
                        payload = canonical_to_toconline_product_payload(canonical, toconline_id=decision.toc_id)
                        self.toconline_connector.update_product(decision.toc_id, payload)
                        self._upsert_entity_link(int(decision.odoo_id), decision.toc_id, canonical)
                elif decision.action == SyncAction.DELETE_IN_TOC:
                    deleted += 1
                    if not dry_run and decision.toc_id is not None:
                        self.toconline_connector.delete_product(decision.toc_id)
                        self._delete_entity_link(odoo_id=decision.odoo_id, toconline_id=decision.toc_id)
                else:
                    skipped += 1
            except Exception:
                failed += 1

        return {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "skipped": skipped,
            "failed": failed,
            "total": len(decisions),
            "dry_run": dry_run,
        }

    def run(self, dry_run: bool = True, allow_delete: bool = False) -> dict[str, Any]:
        plan = self.plan_sync(dry_run=dry_run, allow_delete=allow_delete)
        result = self.apply_decisions(plan, dry_run=dry_run)
        result["plan"] = plan
        return result
