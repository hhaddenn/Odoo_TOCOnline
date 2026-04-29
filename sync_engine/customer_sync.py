

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from django.db import transaction
from django.utils import timezone

from connectors.odoo_client import client_from_env as odoo_client_from_env
from connectors.odoo_customers import OdooCustomerConnector
from connectors.toconline_customer import TOCCustomerConnector
from state.models import Company, DeletionTombstone, EntityLink, EntitySnapshot
from sync_engine.mappers.customers import odoo_to_canonical, odoo_to_toconline_payload


class SyncAction(str, Enum):
	CREATE_IN_TOC = "create_in_toc"
	CREATE_IN_ODOO = "create_in_odoo"
	UPDATE_TOC_FROM_ODOO = "update_toc_from_odoo"
	UPDATE_ODOO_FROM_TOC = "update_odoo_from_toc"
	DELETE_IN_TOC = "delete_in_toc"
	DELETE_IN_ODOO = "delete_in_odoo"
	SKIP = "skip"


@dataclass
class SyncDecision:
	action: SyncAction
	reason: str
	odoo_customer: dict[str, Any] | None = None
	toconline_customer: dict[str, Any] | None = None
	odoo_id: int | None = None
	toconline_id: str | None = None
	odoo_updated_at: datetime | None = None
	toconline_updated_at: datetime | None = None


class CustomerSyncEngine:
	DELETE_CONFIRMATION_CYCLES = 2

	def __init__(
		self,
		company: Company,
		odoo_connector: OdooCustomerConnector | None = None,
		toconline_connector: TOCCustomerConnector | None = None,
	) -> None:
		self.company = company
		self.odoo_connector = odoo_connector or OdooCustomerConnector(client=odoo_client_from_env())
		self.toconline_connector = toconline_connector or TOCCustomerConnector(company=company)
		
	def _extract_toconline_customers(self, payload: Any) -> list[dict[str, Any]]:
		# Converte resposta do TOConline para uma lista de clientes.
		
		if isinstance(payload, list):
			return payload
		
		if isinstance(payload, dict) and "data" in payload:
			data = payload["data"]
			if isinstance(data, list):
				return data
			elif isinstance(data, dict):
				return [data]
			
		return []


	def _parse_odoo_updated_at(self, odoo_customer: dict[str, Any]) -> datetime | None:
		# Converte o campo write_date do Odoo para datetime. Se falhar, tenta updated_at. Se ambos falharem, retorna None.

		write_date = odoo_customer.get("write_date")
		updated_at = odoo_customer.get("updated_at")
		
		if write_date:
			try:
				return datetime.fromisoformat(write_date)
			except ValueError:
				pass
			
		if updated_at:
			try:
				return datetime.fromisoformat(updated_at)
			except ValueError:
				pass

		return None


	def _parse_toconline_updated_at(self, toc_customer: dict[str, Any]) -> datetime | None:
		# Converte o campo updated_at ou modified_at do TOConline para datetime. Se falhar, retorna None.
		
		attributes = toc_customer.get("attributes", {})
		updated_at = attributes.get("updated_at") or attributes.get("modified_at")
		
		if updated_at:
			try:
				return datetime.fromisoformat(updated_at)
			except ValueError:
				pass
			
		return None


	def _canonical_from_toc(self, toc_customer: dict[str, Any]) -> dict[str, str]:
		attributes = toc_customer.get("attributes", {})
		return {
			"name": attributes.get("name") or attributes.get("business_name") or "",
			"vat": attributes.get("tax_registration_number") or "",
			"email": attributes.get("email") or "",
			"phone": attributes.get("phone_number") or attributes.get("phone") or "",
			"street": attributes.get("address_detail") or attributes.get("street") or "",
			"zip": attributes.get("zip_code") or attributes.get("zip") or "",
			"city": attributes.get("city") or "",
			"country": attributes.get("country") or attributes.get("country_code") or "",
		}


	def _customers_have_same_business_data(
		self,
		odoo_customer: dict[str, Any],
		toc_customer: dict[str, Any],
	) -> bool:
		odoo_canonical = odoo_to_canonical(odoo_customer)
		toc_canonical = self._canonical_from_toc(toc_customer)

		def _norm(value: Any) -> str:
			return str(value or "").strip().lower()

		return all(
			_norm(odoo_canonical.get(field)) == _norm(toc_canonical.get(field))
			for field in ("name", "vat", "email", "phone")
		)


	def _find_link_by_odoo_id(self, odoo_id: int) -> EntityLink | None:
		
		query = EntityLink.objects.filter(
      company=self.company,
      entity_type=EntityLink.EntityType.CUSTOMER,
      odoo_id=odoo_id
    )
		
		return query.first()


	def _find_link_by_toconline_id(self, toconline_id: str) -> EntityLink | None:
		
		query = EntityLink.objects.filter(
      company=self.company,
      entity_type=EntityLink.EntityType.CUSTOMER,
      toconline_id=str(toconline_id)
    ).order_by("-updated_at", "-id")
		
		return query.first()


	def _cleanup_duplicate_links_by_toconline_id(self) -> int:
		"""Garante no maximo um EntityLink por toconline_id para customer/empresa."""
		links = list(
			EntityLink.objects.filter(
				company=self.company,
				entity_type=EntityLink.EntityType.CUSTOMER,
			)
			.order_by("toconline_id", "-updated_at", "-id")
		)

		seen_toc_ids: set[str] = set()
		to_delete: list[int] = []
		for link in links:
			toc_id = str(link.toconline_id)
			if toc_id in seen_toc_ids:
				to_delete.append(link.id)
				continue
			seen_toc_ids.add(toc_id)

		if not to_delete:
			return 0

		return EntityLink.objects.filter(id__in=to_delete).delete()[0]


	def _find_toc_by_vat(
		self,
		vat: str | None,
		toconline_customers: list[dict[str, Any]],
	) -> dict[str, Any] | None:
		
		normalized_vat = (vat or "").strip().lower()
		if not normalized_vat:
			return None
		
		query = [
			customer 
			for customer in toconline_customers 
			if customer.get("attributes", {}).get("tax_registration_number", "").strip().lower() 
      == normalized_vat
		]
		
		return query[0] if query else None


	def _find_odoo_by_vat(
		self,
		vat: str | None,
		odoo_customers: list[dict[str, Any]],
	) -> dict[str, Any] | None:
		
		normalized_vat = (vat or "").strip().lower()
		if not normalized_vat:
			return None
		
		query = [
      customer
      for customer in odoo_customers
      if (customer.get("vat") or "").strip().lower() 
			== normalized_vat
    ]
		
		return query[0] if query else None

	def _update_delete_tracking(
		self,
		odoo_customers: list[dict[str, Any]],
		toconline_customers: list[dict[str, Any]],
	) -> None:
		odoo_ids_now = {str(customer.get("id")) for customer in odoo_customers if customer.get("id") is not None}
		toc_ids_now = {str(customer.get("id")) for customer in toconline_customers if customer.get("id") is not None}

		odoo_snapshot, _ = EntitySnapshot.objects.get_or_create(
			company=self.company,
			entity_type=EntityLink.EntityType.CUSTOMER,
			system=EntitySnapshot.System.ODOO,
			defaults={"entity_ids": sorted(odoo_ids_now)},
		)
		toc_snapshot, _ = EntitySnapshot.objects.get_or_create(
			company=self.company,
			entity_type=EntityLink.EntityType.CUSTOMER,
			system=EntitySnapshot.System.TOCONLINE,
			defaults={"entity_ids": sorted(toc_ids_now)},
		)

		odoo_prev_ids = {str(entity_id) for entity_id in (odoo_snapshot.entity_ids or [])}
		toc_prev_ids = {str(entity_id) for entity_id in (toc_snapshot.entity_ids or [])}

		deleted_in_odoo = odoo_prev_ids - odoo_ids_now
		deleted_in_toc = toc_prev_ids - toc_ids_now

		self._upsert_tombstones(DeletionTombstone.System.ODOO, deleted_in_odoo)
		self._upsert_tombstones(DeletionTombstone.System.TOCONLINE, deleted_in_toc)

		odoo_snapshot.entity_ids = sorted(odoo_ids_now)
		toc_snapshot.entity_ids = sorted(toc_ids_now)
		odoo_snapshot.taken_at = timezone.now()
		toc_snapshot.taken_at = timezone.now()
		odoo_snapshot.save(update_fields=["entity_ids", "taken_at"])
		toc_snapshot.save(update_fields=["entity_ids", "taken_at"])

	def _upsert_tombstones(self, system: str, deleted_ids: set[str]) -> None:
		for deleted_id in deleted_ids:
			tombstone, _ = DeletionTombstone.objects.get_or_create(
				company=self.company,
				entity_type=EntityLink.EntityType.CUSTOMER,
				system=system,
				original_id=str(deleted_id),
			)
			tombstone.confirmation_count += 1
			if tombstone.confirmation_count >= self.DELETE_CONFIRMATION_CYCLES and tombstone.confirmed_at is None:
				tombstone.confirmed_at = timezone.now()
			tombstone.save(update_fields=["confirmation_count", "confirmed_at"])

	def _is_confirmed_deleted(self, system: str, original_id: str | int | None) -> bool:
		if original_id in (None, ""):
			return False
		return DeletionTombstone.objects.filter(
			company=self.company,
			entity_type=EntityLink.EntityType.CUSTOMER,
			system=system,
			original_id=str(original_id),
			confirmed_at__isnull=False,
		).exists()

	def _upsert_entity_link(self, odoo_id: int, toconline_id: str | int) -> EntityLink:
		"""
		B5 Tutorial — Persistência de links (passo 1)

		Objetivo:
		- Garantir que existe um único link por (company, entity_type, odoo_id).

		O que este helper faz:
		- Se o link existe: atualiza o toconline_id.
		- Se não existe: cria novo link.

		TODO de aprendizagem:
		- Confirmar no Django shell que chamadas repetidas não criam duplicados.
		"""
		with transaction.atomic():
			link, _ = EntityLink.objects.update_or_create(
				company=self.company,
				entity_type=EntityLink.EntityType.CUSTOMER,
				odoo_id=int(odoo_id),
				defaults={"toconline_id": str(toconline_id)},
			)
			# Evita 1 toconline_id apontar para varios odoo_id.
			EntityLink.objects.filter(
				company=self.company,
				entity_type=EntityLink.EntityType.CUSTOMER,
				toconline_id=str(toconline_id),
			).exclude(id=link.id).delete()
			return link

	def _delete_entity_link(self, odoo_id: int | None = None, toconline_id: str | int | None = None) -> int:
		"""B5: remove link quando delete real acontece."""
		query = EntityLink.objects.filter(
			company=self.company,
			entity_type=EntityLink.EntityType.CUSTOMER,
		)
		if odoo_id is not None:
			query = query.filter(odoo_id=int(odoo_id))
		if toconline_id is not None:
			query = query.filter(toconline_id=str(toconline_id))
		return query.delete()[0]

	def _extract_toconline_id_from_response(self, response_payload: Any) -> str | None:
		"""
		B5 Tutorial — Persistência de links (passo 2)

		Objetivo:
		- Após CREATE no TOConline, extrair o id devolvido para gravar EntityLink.

		Formato esperado (JSON:API):
		- {"data": {"id": "...", ...}}

		Fallback:
		- Alguns endpoints podem devolver {"id": ...} diretamente.
		"""
		if not isinstance(response_payload, dict):
			return None

		data = response_payload.get("data")
		if isinstance(data, dict) and data.get("id") is not None:
			return str(data.get("id"))

		if response_payload.get("id") is not None:
			return str(response_payload.get("id"))

		return None


	def decide_pair_action(
		self,
		odoo_customer: dict[str, Any] | None,
		toc_customer: dict[str, Any] | None,
		allow_delete: bool = False,
	) -> SyncDecision:
		
		odoo_id = int(odoo_customer["id"]) if odoo_customer and odoo_customer.get("id") else None
		toconline_id = str(toc_customer.get("id")) if toc_customer and toc_customer.get("id") else None
		odoo_updated_at = self._parse_odoo_updated_at(odoo_customer) if odoo_customer else None
		toconline_updated_at = self._parse_toconline_updated_at(toc_customer) if toc_customer else None

		base_data = {
			"odoo_customer": odoo_customer,
			"toconline_customer": toc_customer,
			"odoo_id": odoo_id,
			"toconline_id": toconline_id,
			"odoo_updated_at": odoo_updated_at,
			"toconline_updated_at": toconline_updated_at,
		}

		if not odoo_customer and not toc_customer:
			return SyncDecision(
				action=SyncAction.SKIP,
				reason="Nenhum registo encontrado em ambos os lados",
				**base_data,
			)

		if odoo_customer and not toc_customer:
			action = SyncAction.DELETE_IN_ODOO if allow_delete else SyncAction.CREATE_IN_TOC
			reason = "Só existe no Odoo: delete em Odoo por política" if allow_delete else "Só existe no Odoo: criar no TOConline"
			return SyncDecision(action=action, reason=reason, **base_data)

		if toc_customer and not odoo_customer:
			action = SyncAction.DELETE_IN_TOC if allow_delete else SyncAction.CREATE_IN_ODOO
			reason = "Só existe no TOConline: delete em TOConline por política" if allow_delete else "Só existe no TOConline: criar no Odoo"
			return SyncDecision(action=action, reason=reason, **base_data)

		if self._customers_have_same_business_data(odoo_customer, toc_customer):
			return SyncDecision(
				action=SyncAction.SKIP,
				reason="Dados iguais (apenas updated_at pode diferir)",
				**base_data,
			)

		if odoo_updated_at and toconline_updated_at:
			if odoo_updated_at > toconline_updated_at:
				return SyncDecision(
					action=SyncAction.UPDATE_TOC_FROM_ODOO,
					reason="Odoo mais recente: atualizar TOConline",
					**base_data,
				)
			if toconline_updated_at > odoo_updated_at:
				return SyncDecision(
					action=SyncAction.UPDATE_ODOO_FROM_TOC,
					reason="TOConline mais recente: atualizar Odoo",
					**base_data,
				)
			return SyncDecision(
				action=SyncAction.SKIP,
				reason="updated_at igual nos dois lados",
				**base_data,
			)

		if odoo_updated_at and not toconline_updated_at:
			return SyncDecision(
				action=SyncAction.UPDATE_TOC_FROM_ODOO,
				reason="TOConline sem updated_at válido: usar Odoo",
				**base_data,
			)

		if toconline_updated_at and not odoo_updated_at:
			return SyncDecision(
				action=SyncAction.UPDATE_ODOO_FROM_TOC,
				reason="Odoo sem updated_at válido: usar TOConline",
				**base_data,
			)

		return SyncDecision(
			action=SyncAction.SKIP,
			reason="Dados diferentes mas sem updated_at válido em ambos; sem ação automática",
			**base_data,
		)


	def plan_sync(self, allow_delete: bool = False) -> dict[str, Any]:
		# Gera plano completo sem escrever em APIs
		summary: dict[str, Any] = {
			"created": 0,
			"updated": 0,
			"deleted": 0,
			"skipped": 0,
			"errors": [],
			"decisions": [],
		}

		def _bump_counter(action: SyncAction) -> None:
			if action in (SyncAction.CREATE_IN_TOC, SyncAction.CREATE_IN_ODOO):
				summary["created"] += 1
			elif action in (SyncAction.UPDATE_TOC_FROM_ODOO, SyncAction.UPDATE_ODOO_FROM_TOC):
				summary["updated"] += 1
			elif action in (SyncAction.DELETE_IN_TOC, SyncAction.DELETE_IN_ODOO):
				summary["deleted"] += 1
			else:
				summary["skipped"] += 1

		def _serialize_decision(decision: SyncDecision) -> dict[str, Any]:
			return {
				"action": decision.action.value,
				"reason": decision.reason,
				"odoo_id": decision.odoo_id,
				"toconline_id": decision.toconline_id,
				"odoo_updated_at": decision.odoo_updated_at.isoformat() if decision.odoo_updated_at else None,
				"toconline_updated_at": decision.toconline_updated_at.isoformat() if decision.toconline_updated_at else None,
			}

		try:
			self.odoo_connector.connect()
			self.toconline_connector.connect()
			self._cleanup_duplicate_links_by_toconline_id()
		except Exception as exc:
			summary["errors"].append({"error": f"Falha ao conectar a Odoo ou TOConline: {exc}"})
			return summary

		try:
			odoo_customers = self.odoo_connector.get_customers()
			toconline_payload = self.toconline_connector.get_customers()
			toconline_customers = self._extract_toconline_customers(toconline_payload)
		except Exception as exc:
			summary["errors"].append({"error": f"Falha ao obter clientes: {exc}"})
			return summary

		self._update_delete_tracking(odoo_customers, toconline_customers)

		odoo_by_id = {int(customer["id"]): customer for customer in odoo_customers if customer.get("id") is not None}
		toc_by_id = {str(customer.get("id")): customer for customer in toconline_customers if customer.get("id")}
		toc_matched_ids: set[str] = set()

		for odoo_customer in odoo_customers:
			odoo_id = odoo_customer.get("id")
			if not odoo_id:
				summary["errors"].append({"error": "Cliente Odoo sem id", "odoo_customer": odoo_customer})
				continue

			toc_customer: dict[str, Any] | None = None
			link = self._find_link_by_odoo_id(int(odoo_id))

			if link and link.toconline_id:
				toc_customer = toc_by_id.get(str(link.toconline_id))
				if toc_customer is None and self._is_confirmed_deleted(DeletionTombstone.System.TOCONLINE, link.toconline_id):
					decision = SyncDecision(
						action=SyncAction.SKIP,
						reason="TOConline apagado confirmado por tombstone; não recriar automaticamente",
						odoo_customer=odoo_customer,
						odoo_id=int(odoo_id),
						toconline_id=str(link.toconline_id),
						odoo_updated_at=self._parse_odoo_updated_at(odoo_customer),
					)
					summary["decisions"].append(_serialize_decision(decision))
					_bump_counter(decision.action)
					continue

			if toc_customer is None:
				toc_customer = self._find_toc_by_vat(odoo_customer.get("vat"), toconline_customers)
				if toc_customer:
					self._upsert_entity_link(int(odoo_id), str(toc_customer.get("id")))

			elif toc_customer and link and str(link.toconline_id) != str(toc_customer.get("id")):
				self._upsert_entity_link(int(odoo_id), str(toc_customer.get("id")))

			decision = self.decide_pair_action(odoo_customer, toc_customer, allow_delete=allow_delete)
			summary["decisions"].append(_serialize_decision(decision))
			_bump_counter(decision.action)

			if decision.toconline_id:
				toc_matched_ids.add(str(decision.toconline_id))

		for toc_customer in toconline_customers:
			toc_id = toc_customer.get("id")
			if toc_id and str(toc_id) in toc_matched_ids:
				continue

			link = self._find_link_by_toconline_id(str(toc_id)) if toc_id else None
			if link and link.odoo_id is not None:
				linked_odoo_customer = odoo_by_id.get(int(link.odoo_id))
				if linked_odoo_customer:
					decision = self.decide_pair_action(linked_odoo_customer, toc_customer, allow_delete=allow_delete)
					summary["decisions"].append(_serialize_decision(decision))
					_bump_counter(decision.action)
					toc_matched_ids.add(str(toc_id))
					continue

				decision = SyncDecision(
					action=SyncAction.SKIP,
					reason="Link existente para TOConline aponta para Odoo ausente; ignorado para evitar duplicado",
					toconline_customer=toc_customer,
					odoo_id=int(link.odoo_id),
					toconline_id=str(toc_id),
					toconline_updated_at=self._parse_toconline_updated_at(toc_customer),
				)
				summary["decisions"].append(_serialize_decision(decision))
				_bump_counter(decision.action)
				toc_matched_ids.add(str(toc_id))
				continue

			if link and self._is_confirmed_deleted(DeletionTombstone.System.ODOO, link.odoo_id):
				decision = SyncDecision(
					action=SyncAction.SKIP,
					reason="Odoo apagado confirmado por tombstone; não recriar automaticamente",
					toconline_customer=toc_customer,
					odoo_id=int(link.odoo_id),
					toconline_id=str(toc_id),
					toconline_updated_at=self._parse_toconline_updated_at(toc_customer),
				)
				summary["decisions"].append(_serialize_decision(decision))
				_bump_counter(decision.action)
				continue

			odoo_customer = self._find_odoo_by_vat(
				toc_customer.get("attributes", {}).get("tax_registration_number"),
				odoo_customers,
			)
			if odoo_customer and toc_id:
				self._upsert_entity_link(int(odoo_customer["id"]), str(toc_id))
				decision = self.decide_pair_action(odoo_customer, toc_customer, allow_delete=allow_delete)
				summary["decisions"].append(_serialize_decision(decision))
				_bump_counter(decision.action)
				continue

			decision = self.decide_pair_action(None, toc_customer, allow_delete=allow_delete)
			summary["decisions"].append(_serialize_decision(decision))
			_bump_counter(decision.action)

		return summary


	def _toconline_to_odoo_payload(self, toc_customer: dict[str, Any]) -> dict[str, Any]:
		attributes = toc_customer.get("attributes", {})
		return {
			"name": attributes.get("name") or attributes.get("business_name") or "Sem nome",
			"vat": attributes.get("tax_registration_number") or False,
			"email": attributes.get("email") or False,
			"phone": attributes.get("phone_number") or attributes.get("phone") or False,
			"street": attributes.get("address_detail") or attributes.get("street") or False,
			"zip": attributes.get("zip_code") or attributes.get("zip") or False,
			"city": attributes.get("city") or False,
		}


	def apply_decisions(self, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
		
		if dry_run:
			return plan

		result = {
			"created": 0,
			"updated": 0,
			"deleted": 0,
			"skipped": 0,
			"errors": list(plan.get("errors", [])),
			"decisions": list(plan.get("decisions", [])),
		}

		self._cleanup_duplicate_links_by_toconline_id()

		odoo_customers = self.odoo_connector.get_customers()
		toc_payload = self.toconline_connector.get_customers()
		toconline_customers = self._extract_toconline_customers(toc_payload)

		odoo_by_id = {int(customer["id"]): customer for customer in odoo_customers if customer.get("id")}
		toc_by_id = {str(customer.get("id")): customer for customer in toconline_customers if customer.get("id")}

		for decision in result["decisions"]:
			action = decision.get("action")
			odoo_id = decision.get("odoo_id")
			toc_id = decision.get("toconline_id")

			try:
				if action == SyncAction.CREATE_IN_TOC.value:
					odoo_customer = odoo_by_id.get(int(odoo_id)) if odoo_id is not None else None
					if not odoo_customer:
						raise ValueError(f"Cliente Odoo não encontrado para criação no TOConline (odoo_id={odoo_id})")
					payload = odoo_to_toconline_payload(odoo_customer)
					created = self.toconline_connector.create_customer(payload)
					created_toc_id = self._extract_toconline_id_from_response(created)
					if odoo_id is not None and created_toc_id:
						self._upsert_entity_link(int(odoo_id), created_toc_id)
					result["created"] += 1

				elif action == SyncAction.UPDATE_TOC_FROM_ODOO.value:
					odoo_customer = odoo_by_id.get(int(odoo_id)) if odoo_id is not None else None
					if not odoo_customer or not toc_id:
						raise ValueError(
							f"Dados insuficientes para update TOConline (odoo_id={odoo_id}, toconline_id={toc_id})"
						)
					payload = odoo_to_toconline_payload(odoo_customer, toconline_id=str(toc_id))
					self.toconline_connector.update_customer(str(toc_id), payload)
					self._upsert_entity_link(int(odoo_id), str(toc_id))
					result["updated"] += 1

				elif action == SyncAction.UPDATE_ODOO_FROM_TOC.value:
					toc_customer = toc_by_id.get(str(toc_id)) if toc_id else None
					if not toc_customer or odoo_id is None:
						raise ValueError(
							f"Dados insuficientes para update Odoo (odoo_id={odoo_id}, toconline_id={toc_id})"
						)
					payload = self._toconline_to_odoo_payload(toc_customer)
					self.odoo_connector.update_customer(int(odoo_id), payload)
					self._upsert_entity_link(int(odoo_id), str(toc_id))
					result["updated"] += 1

				elif action == SyncAction.CREATE_IN_ODOO.value:
					toc_customer = toc_by_id.get(str(toc_id)) if toc_id else None
					if not toc_customer:
						raise ValueError(f"Cliente TOConline não encontrado para criação no Odoo (toconline_id={toc_id})")

					# Guardrail: evita duplicar cliente Odoo quando o mesmo TOConline ja esta ligado.
					existing_link = self._find_link_by_toconline_id(str(toc_id)) if toc_id else None
					if existing_link and existing_link.odoo_id is not None:
						result["skipped"] += 1
						continue

					vat = toc_customer.get("attributes", {}).get("tax_registration_number")
					existing_odoo = self._find_odoo_by_vat(vat, odoo_customers)
					if existing_odoo and existing_odoo.get("id") is not None and toc_id:
						self._upsert_entity_link(int(existing_odoo["id"]), str(toc_id))
						result["skipped"] += 1
						continue

					payload = self._toconline_to_odoo_payload(toc_customer)
					new_odoo_id = self.odoo_connector.create_customer(payload)
					if toc_id and new_odoo_id:
						self._upsert_entity_link(int(new_odoo_id), str(toc_id))
					result["created"] += 1

				elif action == SyncAction.DELETE_IN_TOC.value:
					if not toc_id:
						raise ValueError("toconline_id obrigatório para DELETE_IN_TOC")
					self.toconline_connector.delete_customer(str(toc_id))
					self._delete_entity_link(odoo_id=odoo_id, toconline_id=toc_id)
					result["deleted"] += 1

				elif action == SyncAction.DELETE_IN_ODOO.value:
					if odoo_id is None:
						raise ValueError("odoo_id obrigatório para DELETE_IN_ODOO")
					self.odoo_connector.delete_customer(int(odoo_id))
					self._delete_entity_link(odoo_id=odoo_id, toconline_id=toc_id)
					result["deleted"] += 1

				else:
					result["skipped"] += 1

			except Exception as exc:
				result["errors"].append(
					{
						"action": action,
						"odoo_id": odoo_id,
						"toconline_id": toc_id,
						"error": str(exc),
					}
				)
			
		return result


if __name__ == "__main__":
	import json
	import os

	import django

	os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
	django.setup()

	company = Company.objects.filter(is_active=True).first()
	if not company:
		raise SystemExit("Nenhuma Company ativa encontrada. Cria uma Company antes de correr o runner.")

	engine = CustomerSyncEngine(company)
	plan = engine.plan_sync(allow_delete=False)

	print("=== DRY RUN: plano de sync ===")
	print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))

	if os.getenv("APPLY_SYNC") == "1":
		result = engine.apply_decisions(plan, dry_run=False)
		print("=== APPLY: resultado da execução ===")
		print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

