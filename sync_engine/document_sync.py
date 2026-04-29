from enum import Enum

from state.models import EntityLink


class SyncDecision(Enum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


class DocumentSyncEngine:
    def __init__(self, odoo_connector, toc_connector, mapper, logger=None, company=None):
        self.odoo_connector = odoo_connector
        self.toc_connector = toc_connector
        self.mapper = mapper
        self.logger = logger
        self.company = company
        self.decisions = []
        self._entity_cache: dict[tuple[str, str], dict] = {}

    def _log_warning(self, message):
        if self.logger:
            self.logger.warning(message)

    def _log_info(self, message):
        if self.logger:
            self.logger.info(message)

    def _log_error(self, message):
        if self.logger:
            self.logger.error(message)
    
    def _is_state_protected(self, state: str) -> bool:
        protected_states = ["LIQUIDADA", "CANCELADA", "VENCIDA"]
        return state in protected_states
    
    def _is_state_valid(self, state: str) -> bool:
        valid_states = ["PENDENTE", "ENVIADA", "PREPARADA", "COMPLETA", "LIQUIDADA", "CANCELADA", "VENCIDA"]
        return state in valid_states

    def _find_entity_link(self, entity_type: str, odoo_id: str | int | None):
        if self.company is None or odoo_id in (None, ""):
            return None

        return EntityLink.objects.filter(
            company=self.company,
            entity_type=entity_type,
            odoo_id=int(odoo_id),
        ).order_by("-updated_at", "-id").first()

    def _resolve_counterparty_id(self, document_type: str, canonical_doc: dict) -> str | None:
        partner_id = canonical_doc.get("partner_id")
        link = None

        if document_type == "purchase_invoice":
            link = self._find_entity_link(EntityLink.EntityType.SUPPLIER, partner_id)
        elif document_type == "sales_invoice":
            link = self._find_entity_link(EntityLink.EntityType.CUSTOMER, partner_id)
        elif document_type == "rectificative_document":
            if canonical_doc.get("document_type") == "purchase_refund":
                link = self._find_entity_link(EntityLink.EntityType.SUPPLIER, partner_id)
            else:
                link = self._find_entity_link(EntityLink.EntityType.CUSTOMER, partner_id)

        return str(link.toconline_id) if link and link.toconline_id else None

    def _get_toc_api_client(self):
        api_client = getattr(self.toc_connector, "api_client", None)
        if api_client is not None:
            return api_client
        return getattr(self.toc_connector, "client", None)

    def _fetch_toc_entity(self, entity_path: str, entity_id: str | int | None) -> dict | None:
        if entity_id in (None, ""):
            return None

        cache_key = (entity_path, str(entity_id))
        if cache_key in self._entity_cache:
            return self._entity_cache[cache_key]

        api_client = self._get_toc_api_client()
        if api_client is None:
            return None

        try:
            payload = api_client.get(f"/api/{entity_path}/{entity_id}")
        except Exception:
            return None

        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
            entity = payload["data"]
        elif isinstance(payload, dict):
            entity = payload
        else:
            entity = None

        if isinstance(entity, dict):
            self._entity_cache[cache_key] = entity
            return entity
        return None

    def _enrich_payload_context(self, document_type: str, canonical_doc: dict) -> dict:
        enriched = dict(canonical_doc)
        counterparty_id = self._resolve_counterparty_id(document_type, canonical_doc)

        company_id = None
        document_series_id = None
        api_client = self._get_toc_api_client()
        if api_client and hasattr(api_client, "get_current_company_id"):
            company_id = api_client.get_current_company_id()
        if api_client and hasattr(api_client, "get_default_document_series_id"):
            document_series_id = api_client.get_default_document_series_id(
                "FC" if document_type == "purchase_invoice" else "FT" if document_type == "sales_invoice" else ""
            )

        if document_type == "purchase_invoice":
            enriched["supplier_id"] = counterparty_id
            if company_id is not None:
                enriched["company_id"] = company_id
            if document_series_id is not None:
                enriched["document_series_id"] = document_series_id
            supplier = self._fetch_toc_entity("suppliers", counterparty_id)
            supplier_attrs = supplier.get("attributes", {}) if isinstance(supplier, dict) else {}
            enriched["supplier_tax_registration_number"] = supplier_attrs.get("tax_registration_number")
            enriched["supplier_business_name"] = supplier_attrs.get("business_name")
            enriched["supplier_address_detail"] = supplier_attrs.get("address_detail") or ""
            enriched["supplier_postcode"] = supplier_attrs.get("postcode") or ""
            enriched["supplier_city"] = supplier_attrs.get("city") or ""
            enriched["supplier_country"] = supplier_attrs.get("country_iso_alpha_2") or "PT"
        elif document_type == "sales_invoice":
            enriched["customer_id"] = counterparty_id
            if document_series_id is not None:
                enriched["document_series_id"] = document_series_id
            customer = self._fetch_toc_entity("customers", counterparty_id)
            customer_attrs = customer.get("attributes", {}) if isinstance(customer, dict) else {}
            enriched["customer_tax_registration_number"] = customer_attrs.get("tax_registration_number")
            enriched["customer_business_name"] = customer_attrs.get("business_name")
            enriched["customer_address_detail"] = customer_attrs.get("address_detail") or ""
            enriched["customer_postcode"] = customer_attrs.get("postcode") or ""
            enriched["customer_city"] = customer_attrs.get("city") or ""
            enriched["customer_country"] = customer_attrs.get("country_iso_alpha_2") or "PT"
        elif document_type == "rectificative_document":
            if canonical_doc.get("document_type") == "purchase_refund":
                enriched["supplier_id"] = counterparty_id
            else:
                enriched["customer_id"] = counterparty_id

        return enriched

    def _has_required_payload_fields(self, document_type: str, payload: dict) -> tuple[bool, list[str]]:
        if document_type == "shipment_document":
            required_fields = ["number", "date", "partner_id", "location_id", "location_dest_id", "company_id"]
        elif document_type == "purchase_invoice":
            required_fields = ["document_type", "date", "currency_iso_code", "lines"]
        elif document_type == "rectificative_document":
            required_fields = ["document_type", "date", "currency_iso_code"]
            if payload.get("document_type") == "ND":
                required_fields.append("supplier_id")
            else:
                required_fields.append("customer_id")
        elif document_type == "sales_receipt":
            required_fields = ["date", "partner_id"]
        else:
            required_fields = ["document_type", "date", "currency_iso_code", "lines"]
        missing: list[str] = []
        for field in required_fields:
            value = payload.get(field)
            if value in (None, False, "", [], {}):
                missing.append(field)

        if document_type == "purchase_invoice" and payload.get("supplier_id") in (None, "") and payload.get("supplier_tax_registration_number") in (None, ""):
            missing.append("supplier_id|supplier_tax_registration_number")
        if document_type == "sales_invoice" and payload.get("customer_id") in (None, "") and payload.get("customer_tax_registration_number") in (None, ""):
            missing.append("customer_id|customer_tax_registration_number")
        return (len(missing) == 0, missing)
    
    def plan_sync(self, document_type="sales_invoice", dry_run=True):
        self.decisions = []
        
        if document_type == "sales_invoice":
            odoo_docs = self.odoo_connector.get_sales_documents() or []
            toc_docs = self.toc_connector.get_sales_documents() or []
            to_canonical = self.mapper.odoo_sales_document_to_canonical
            toc_to_canonical = self.mapper.toc_sales_document_to_canonical
            compare_fn = self.mapper.compare_sales_documents
            to_payload = self.mapper.canonical_to_toconline_sales_document_payload
        elif document_type == "purchase_invoice":
            odoo_docs = self.odoo_connector.get_purchase_documents() or []
            toc_docs = self.toc_connector.get_purchase_documents() or []
            to_canonical = self.mapper.odoo_purchase_document_to_canonical
            toc_to_canonical = self.mapper.toc_purchase_document_to_canonical
            compare_fn = self.mapper.compare_purchase_documents
            to_payload = self.mapper.canonical_to_toconline_purchase_document_payload
        elif document_type == "rectificative_document":
            odoo_docs = self.odoo_connector.get_rectificative_documents() or []
            toc_docs = self.toc_connector.get_rectificative_documents() or []
            to_canonical = self.mapper.odoo_rectificative_document_to_canonical
            toc_to_canonical = self.mapper.toc_rectificative_document_to_canonical
            compare_fn = self.mapper.compare_rectificative_documents
            to_payload = self.mapper.canonical_to_toconline_rectificative_payload
        elif document_type == "shipment_document":
            odoo_docs = self.odoo_connector.get_shipment_documents() or []
            toc_docs = self.toc_connector.get_shipment_documents() or []
            to_canonical = self.mapper.odoo_shipment_document_to_canonical
            toc_to_canonical = self.mapper.toc_shipment_document_to_canonical
            compare_fn = self.mapper.compare_shipment_documents
            to_payload = self.mapper.canonical_to_toconline_shipment_payload
        elif document_type == "sales_receipt":
            odoo_docs = self.odoo_connector.get_sales_receipts() or []
            toc_docs = self.toc_connector.get_sales_receipts() or []
            to_canonical = self.mapper.odoo_sales_receipt_to_canonical
            toc_to_canonical = self.mapper.toc_sales_receipt_to_canonical
            compare_fn = self.mapper.compare_sales_receipts
            to_payload = self.mapper.canonical_to_toconline_sales_receipt_payload
        else:
            self._log_warning(f"Tipo de documento desconhecido: {document_type}")
            return []
        
        odoo_canonical = [to_canonical(doc) for doc in odoo_docs if isinstance(doc, dict)]
        toc_canonical = [toc_to_canonical(doc) for doc in toc_docs if isinstance(doc, dict)]
        
        for odoo_doc in odoo_canonical:
            if not self._is_state_valid(odoo_doc.get("state")):
                self._log_warning(f"Estado inválido '{odoo_doc.get('state')}' para documento {odoo_doc['external_id']}")
                self.decisions.append({
                    "decision": SyncDecision.SKIP,
                    "odoo_id": odoo_doc["external_id"],
                    "toc_id": None,
                    "reason": f"Estado inválido ou não mapeável: '{odoo_doc.get('state')}'",
                    "document_type": document_type
                })
                continue
            
            toc_equivalent = self._find_equivalent(odoo_doc, toc_canonical)
            
            if not toc_equivalent:
                payload = to_payload(self._enrich_payload_context(document_type, odoo_doc))
                valid_payload, missing_fields = self._has_required_payload_fields(document_type, payload)
                if not valid_payload:
                    self.decisions.append({
                        "decision": SyncDecision.SKIP,
                        "odoo_id": odoo_doc["external_id"],
                        "toc_id": None,
                        "reason": f"Campos obrigatórios em falta: {', '.join(missing_fields)}",
                        "document_type": document_type,
                    })
                    continue

                self.decisions.append({
                    "decision": SyncDecision.CREATE,
                    "odoo_id": odoo_doc["external_id"],
                    "toc_id": None,
                    "reason": "Novo documento no Odoo",
                    "payload": payload,
                    "document_type": document_type
                })
            else:
                equal, diff = compare_fn(odoo_doc, toc_equivalent)
                
                if equal:
                    self.decisions.append({
                        "decision": SyncDecision.SKIP,
                        "odoo_id": odoo_doc["external_id"],
                        "toc_id": toc_equivalent["external_id"],
                        "reason": "Documentos iguais",
                        "document_type": document_type
                    })
                else:
                    if self._is_state_protected(odoo_doc.get("state")) or self._is_state_protected(toc_equivalent.get("state")):
                        reason = f"Documentos finalizados: Odoo={odoo_doc.get('state')}, TOConline={toc_equivalent.get('state')}"
                        self._log_warning(f"Bloqueado UPDATE: {reason}")
                        self.decisions.append({
                            "decision": SyncDecision.SKIP,
                            "odoo_id": odoo_doc["external_id"],
                            "toc_id": toc_equivalent["external_id"],
                            "reason": reason,
                            "document_type": document_type,
                            "log_level": "WARNING"
                        })
                    else:
                        payload = to_payload(self._enrich_payload_context(document_type, odoo_doc), toc_equivalent["external_id"])
                        valid_payload, missing_fields = self._has_required_payload_fields(document_type, payload)
                        if not valid_payload:
                            self.decisions.append({
                                "decision": SyncDecision.SKIP,
                                "odoo_id": odoo_doc["external_id"],
                                "toc_id": toc_equivalent["external_id"],
                                "reason": f"Campos obrigatórios em falta: {', '.join(missing_fields)}",
                                "document_type": document_type,
                                "log_level": "WARNING"
                            })
                            continue

                        self.decisions.append({
                            "decision": SyncDecision.UPDATE,
                            "odoo_id": odoo_doc["external_id"],
                            "toc_id": toc_equivalent["external_id"],
                            "reason": f"Diferenças: {', '.join(diff.keys())}",
                            "payload": payload,
                            "document_type": document_type,
                            "changes": diff
                        })
        
        return self.decisions
    
    def apply_decisions(self, plan, dry_run=True):
        results = []
        
        for decision in plan:
            if decision["decision"] == SyncDecision.CREATE:
                if dry_run:
                    results.append({
                        "status": "DRY_RUN",
                        "decision": "CREATE",
                        "odoo_id": decision["odoo_id"],
                        "reason": "Não criado em dry-run"
                    })
                else:
                    try:
                        if decision.get("document_type") == "purchase_invoice":
                            response = self.toc_connector.create_purchase_document(decision["payload"])
                        elif decision.get("document_type") == "rectificative_document":
                            response = self.toc_connector.create_rectificative_document(decision["payload"])
                        elif decision.get("document_type") == "shipment_document":
                            response = self.toc_connector.create_shipment_document(decision["payload"])
                        elif decision.get("document_type") == "sales_receipt":
                            response = self.toc_connector.create_sales_receipt(decision["payload"])
                        else:
                            response = self.toc_connector.create_sales_document(decision["payload"])
                        results.append({
                            "status": "SUCCESS",
                            "decision": "CREATE",
                            "odoo_id": decision["odoo_id"],
                            "toc_id": response.get("id")
                        })
                        self._log_info(f"Criado documento {decision['payload']['number']}")
                    except Exception as e:
                        results.append({
                            "status": "ERROR",
                            "decision": "CREATE",
                            "odoo_id": decision["odoo_id"],
                            "error": str(e)
                        })
                        self._log_error(f"Erro ao criar documento: {e}")
            
            elif decision["decision"] == SyncDecision.UPDATE:
                if dry_run:
                    results.append({
                        "status": "DRY_RUN",
                        "decision": "UPDATE",
                        "toc_id": decision["toc_id"],
                        "reason": "Não atualizado em dry-run"
                    })
                else:
                    try:
                        if decision.get("document_type") == "purchase_invoice":
                            self.toc_connector.update_purchase_document(
                                decision["toc_id"],
                                decision["payload"]
                            )
                        elif decision.get("document_type") == "rectificative_document":
                            self.toc_connector.update_rectificative_document(
                                decision["toc_id"],
                                decision["payload"]
                            )
                        elif decision.get("document_type") == "shipment_document":
                            self.toc_connector.update_shipment_document(
                                decision["toc_id"],
                                decision["payload"]
                            )
                        elif decision.get("document_type") == "sales_receipt":
                            self.toc_connector.update_sales_receipt(
                                decision["toc_id"],
                                decision["payload"]
                            )
                        else:
                            self.toc_connector.update_sales_document(
                                decision["toc_id"],
                                decision["payload"]
                            )
                        results.append({
                            "status": "SUCCESS",
                            "decision": "UPDATE",
                            "toc_id": decision["toc_id"]
                        })
                        self._log_info(f"Atualizado documento {decision['toc_id']}")
                    except Exception as e:
                        results.append({
                            "status": "ERROR",
                            "decision": "UPDATE",
                            "toc_id": decision["toc_id"],
                            "error": str(e)
                        })
            
            elif decision["decision"] == SyncDecision.SKIP:
                results.append({
                    "status": "SKIP",
                    "decision": "SKIP",
                    "reason": decision.get("reason", "Sem mudanças")
                })
        
        return results
    
    def run(self, document_type="sales_invoice", dry_run=True):
        plan = self.plan_sync(document_type=document_type, dry_run=dry_run)
        results = self.apply_decisions(plan, dry_run=dry_run)
        
        return {
            "plan": plan,
            "results": results,
            "summary": {
                "total": len(plan),
                "creates": sum(1 for d in plan if d["decision"] == SyncDecision.CREATE),
                "updates": sum(1 for d in plan if d["decision"] == SyncDecision.UPDATE),
                "skips": sum(1 for d in plan if d["decision"] == SyncDecision.SKIP)
            }
        }
    
    def _find_equivalent(self, odoo_doc, toc_docs):
        source_number = str(odoo_doc.get("number", "")).strip().lower()
        if not source_number:
            return None
        for toc_doc in toc_docs:
            target_number = str(toc_doc.get("number", "")).strip().lower()
            if source_number == target_number:
                return toc_doc
        return None