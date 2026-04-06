from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any

from django.utils import timezone

from connectors.odoo_client import client_from_env as odoo_client_from_env
from connectors.odoo_suppliers import OdooSuppliersConnector
from connectors.toconline_client import client_from_company, client_from_env as toconline_client_from_env
from connectors.toconline_suppliers import TOCSuppliersConnector
from state.models import Company, EntityLink
from sync_engine.mappers.suppliers import canonical_to_toconline_supplier_payload, odoo_supplier_to_canonical


class SyncAction(str, Enum):
  CREATE_IN_TOC = "create_in_toc"
  UPDATE_TOC_FROM_ODOO = "update_toc_from_odoo"
  SKIP = "skip"


@dataclass
class SyncDecision:
  action: SyncAction
  reason: str
  odoo_supplier: dict[str, Any] | None = None
  toc_supplier: dict[str, Any] | None = None
  odoo_id: int | None = None
  toc_id: str | None = None


class SupplierSync:
  def __init__(
    self,
    company: Company | None = None,
    odoo_connector: OdooSuppliersConnector | None = None,
    toconline_connector: TOCSuppliersConnector | None = None,
  ) -> None:
    self.company = company
    self.odoo_connector = odoo_connector or OdooSuppliersConnector(client=odoo_client_from_env())
    toc_client = client_from_company(company) if company else toconline_client_from_env()
    self.toconline_connector = toconline_connector or TOCSuppliersConnector(api_client=toc_client)

  def _extract_toc_suppliers(self, payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
      return payload
    if isinstance(payload, dict):
      data = payload.get('data')
      if isinstance(data, list):
        return data
      if isinstance(data, dict):
        return [data]
    return []

  def _canonical_hash(self, canonical_supplier: dict[str, Any]) -> str:
    payload = json.dumps(canonical_supplier, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

  def _supplier_key(self, canonical_supplier: dict[str, Any]) -> str:
    vat = str(canonical_supplier.get('vat') or '').strip().lower()
    if vat:
      return f'vat:{vat}'
    name = str(canonical_supplier.get('name') or '').strip().lower()
    return f'name:{name}' if name else ''

  def _find_link_by_odoo_id(self, odoo_id: int) -> EntityLink | None:
    if self.company is None:
      return None
    return EntityLink.objects.filter(
      company=self.company,
      entity_type=EntityLink.EntityType.SUPPLIER,
      odoo_id=odoo_id,
    ).first()

  def _upsert_entity_link(self, odoo_id: int, toc_id: str, canonical_supplier: dict[str, Any]) -> None:
    if self.company is None:
      return
    EntityLink.objects.update_or_create(
      company=self.company,
      entity_type=EntityLink.EntityType.SUPPLIER,
      odoo_id=odoo_id,
      defaults={
        'toconline_id': str(toc_id),
        'canonical_hash': self._canonical_hash(canonical_supplier),
        'last_synced_at': timezone.now(),
      },
    )

  def _select_toc_supplier(self, canonical_supplier: dict[str, Any], toc_suppliers: list[dict[str, Any]]) -> dict[str, Any] | None:
    vat = str(canonical_supplier.get('vat') or '').strip().lower()
    if vat:
      for supplier in toc_suppliers:
        attributes = supplier.get('attributes', {}) if isinstance(supplier, dict) else {}
        candidate_vat = str(attributes.get('tax_registration_number') or '').strip().lower()
        if candidate_vat == vat:
          return supplier
    name = str(canonical_supplier.get('name') or '').strip().lower()
    if name:
      for supplier in toc_suppliers:
        attributes = supplier.get('attributes', {}) if isinstance(supplier, dict) else {}
        candidate_name = str(attributes.get('business_name') or attributes.get('name') or '').strip().lower()
        if candidate_name == name:
          return supplier
    return None

  def _build_decision(self, odoo_supplier: dict[str, Any], toc_supplier: dict[str, Any] | None) -> SyncDecision:
    canonical = odoo_supplier_to_canonical(odoo_supplier)
    if not canonical.get('name'):
      return SyncDecision(action=SyncAction.SKIP, reason='missing name', odoo_supplier=odoo_supplier, toc_supplier=toc_supplier, odoo_id=odoo_supplier.get('id'))

    if toc_supplier is None:
      return SyncDecision(action=SyncAction.CREATE_IN_TOC, reason='no matching TOConline supplier', odoo_supplier=odoo_supplier, odoo_id=odoo_supplier.get('id'))

    toc_attrs = toc_supplier.get('attributes', {}) if isinstance(toc_supplier, dict) else {}
    toc_canonical = {
      'external_id': toc_supplier.get('id'),
      'name': toc_attrs.get('business_name') or toc_attrs.get('name'),
      'vat': toc_attrs.get('tax_registration_number'),
      'email': toc_attrs.get('email'),
      'phone': toc_attrs.get('phone_number') or toc_attrs.get('phone'),
      'street': toc_attrs.get('street'),
      'city': toc_attrs.get('city'),
      'zip': toc_attrs.get('zip_code') or toc_attrs.get('zip'),
      'country': toc_attrs.get('country'),
    }
    if self._supplier_key(canonical) == self._supplier_key(toc_canonical) and canonical.get('email') == toc_canonical.get('email') and canonical.get('phone') == toc_canonical.get('phone'):
      return SyncDecision(action=SyncAction.SKIP, reason='already in sync', odoo_supplier=odoo_supplier, toc_supplier=toc_supplier, odoo_id=odoo_supplier.get('id'), toc_id=str(toc_supplier.get('id')) if toc_supplier.get('id') is not None else None)

    return SyncDecision(action=SyncAction.UPDATE_TOC_FROM_ODOO, reason='fields differ', odoo_supplier=odoo_supplier, toc_supplier=toc_supplier, odoo_id=odoo_supplier.get('id'), toc_id=str(toc_supplier.get('id')) if toc_supplier.get('id') is not None else None)

  def plan_sync(self, dry_run: bool = True) -> dict[str, Any]:
    odoo_suppliers = self.odoo_connector.get_suppliers()
    toc_suppliers = self._extract_toc_suppliers(self.toconline_connector.get_suppliers())

    toc_by_key: dict[str, dict[str, Any]] = {}
    for toc_supplier in toc_suppliers:
      attrs = toc_supplier.get('attributes', {}) if isinstance(toc_supplier, dict) else {}
      key = self._supplier_key({
        'name': attrs.get('business_name') or attrs.get('name'),
        'vat': attrs.get('tax_registration_number'),
      })
      if key and key not in toc_by_key:
        toc_by_key[key] = toc_supplier

    decisions: list[SyncDecision] = []
    created = updated = skipped = failed = 0

    for odoo_supplier in odoo_suppliers:
      try:
        canonical = odoo_supplier_to_canonical(odoo_supplier)
        key = self._supplier_key(canonical)
        toc_supplier = toc_by_key.get(key) if key else None
        if toc_supplier is None and odoo_supplier.get('id') is not None:
          link = self._find_link_by_odoo_id(int(odoo_supplier.get('id')))
          if link is not None:
            toc_supplier = next((item for item in toc_suppliers if str(item.get('id')) == str(link.toconline_id)), None)
        if toc_supplier is None:
          toc_supplier = self._select_toc_supplier(canonical, toc_suppliers)

        decision = self._build_decision(odoo_supplier, toc_supplier)
        decisions.append(decision)
        if decision.action == SyncAction.CREATE_IN_TOC:
          created += 1
        elif decision.action == SyncAction.UPDATE_TOC_FROM_ODOO:
          updated += 1
        else:
          skipped += 1
      except Exception:
        failed += 1

    return {
      'created': created,
      'updated': updated,
      'skipped': skipped,
      'failed': failed,
      'total': len(odoo_suppliers),
      'decisions': decisions,
      'dry_run': dry_run,
    }

  def apply_decisions(self, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    decisions: list[SyncDecision] = plan.get('decisions', [])
    created = updated = skipped = failed = 0

    for decision in decisions:
      try:
        canonical = odoo_supplier_to_canonical(decision.odoo_supplier or {})
        if decision.action == SyncAction.CREATE_IN_TOC:
          created += 1
          if not dry_run:
            payload = canonical_to_toconline_supplier_payload(canonical)
            response = self.toconline_connector.create_supplier(payload)
            toc_id = str(response.get('id') or response.get('data', {}).get('id') or '') if isinstance(response, dict) else ''
            if toc_id and decision.odoo_id is not None:
              self._upsert_entity_link(int(decision.odoo_id), toc_id, canonical)
        elif decision.action == SyncAction.UPDATE_TOC_FROM_ODOO:
          updated += 1
          if not dry_run and decision.toc_id is not None:
            payload = canonical_to_toconline_supplier_payload(canonical, toconline_id=decision.toc_id)
            self.toconline_connector.update_supplier(decision.toc_id, payload)
            if decision.odoo_id is not None:
              self._upsert_entity_link(int(decision.odoo_id), decision.toc_id, canonical)
        else:
          skipped += 1
      except Exception:
        failed += 1

    return {
      'created': created,
      'updated': updated,
      'skipped': skipped,
      'failed': failed,
      'total': len(decisions),
      'dry_run': dry_run,
    }

  def run(self, dry_run: bool = True) -> dict[str, Any]:
    plan = self.plan_sync(dry_run=dry_run)
    result = self.apply_decisions(plan, dry_run=dry_run)
    result['plan'] = plan
    return result


def run(company: Company | None = None, odoo_connector: OdooSuppliersConnector | None = None, toconline_connector: TOCSuppliersConnector | None = None, dry_run: bool = True) -> dict[str, Any]:
  engine = SupplierSync(company=company, odoo_connector=odoo_connector, toconline_connector=toconline_connector)
  return engine.run(dry_run=dry_run)