from enum import Enum


class SyncDecision(Enum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


class DocumentSyncEngine:
    def __init__(self, odoo_connector, toc_connector, mapper, logger=None):
        self.odoo_connector = odoo_connector
        self.toc_connector = toc_connector
        self.mapper = mapper
        self.logger = logger
        self.decisions = []

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
                self.decisions.append({
                    "decision": SyncDecision.CREATE,
                    "odoo_id": odoo_doc["external_id"],
                    "toc_id": None,
                    "reason": "Novo documento no Odoo",
                    "payload": to_payload(odoo_doc),
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
                        self.decisions.append({
                            "decision": SyncDecision.UPDATE,
                            "odoo_id": odoo_doc["external_id"],
                            "toc_id": toc_equivalent["external_id"],
                            "reason": f"Diferenças: {', '.join(diff.keys())}",
                            "payload": to_payload(odoo_doc, toc_equivalent["external_id"]),
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