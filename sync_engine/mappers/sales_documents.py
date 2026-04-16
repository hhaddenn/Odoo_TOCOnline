def map_odoo_state_to_canonical(odoo_state):
    """Mapeia estado Odoo para estado canónico."""
    state_map = {
        "draft": "PENDENTE",
        "posted": "PENDENTE",
        "paid": "LIQUIDADA",
        "cancel": "CANCELADA"
    }
    return state_map.get(odoo_state, "PENDENTE")


def _extract_m2o_id(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def _extract_m2o_name(value, fallback=""):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1] or fallback
    return fallback

def odoo_sales_document_to_canonical(odoo_doc):
    """
    Converte um invoice Odoo para formato canónico.
    
    Args:
        odoo_doc: dict com campos de account.move
    
    Returns:
        dict com estrutura canónica
    """
    return {
        "external_id": str(odoo_doc.get("id")),
        "document_type": "sales_invoice",
        "number": odoo_doc.get("name", ""),
        "date": odoo_doc.get("invoice_date"),
        "due_date": odoo_doc.get("invoice_date_due"),
        "partner_id": str(_extract_m2o_id(odoo_doc.get("partner_id"))),
        "amount_total": float(odoo_doc.get("amount_total", 0)),
        "currency_id": _extract_m2o_name(odoo_doc.get("currency_id"), fallback="EUR"),
        "state": map_odoo_state_to_canonical(odoo_doc.get("state", "draft")),
        "origin_id": None,
        "updated_at": odoo_doc.get("write_date")
    }

def toc_sales_document_to_canonical(toc_doc):
    """
    Converte um documento TOConline para formato canónico.
    
    Args:
        toc_doc: dict com dados do TOConline
    
    Returns:
        dict com estrutura canónica
    """
    return {
        "external_id": str(toc_doc.get("id")),
        "document_type": "sales_invoice",
        "number": toc_doc.get("number", ""),
        "date": toc_doc.get("date"),
        "due_date": toc_doc.get("due_date"),
        "partner_id": str(toc_doc.get("partner_id")),
        "amount_total": float(toc_doc.get("amount_total", 0)),
        "currency_id": toc_doc.get("currency", "EUR"),
        "state": toc_doc.get("state", "PENDENTE"),
        "origin_id": None,
        "updated_at": toc_doc.get("updated_at")
    }

def compare_sales_documents(source_canonical, target_canonical):
    """
    Compara dois documentos em formato canónico.
    
    Args:
        source_canonical: dict de origem (Odoo)
        target_canonical: dict de destino (TOConline)
    
    Returns:
        tuple: (boolean igual?, dict de diferenças)
    """
    fields_to_compare = ["number", "state", "date", "due_date", "partner_id", "currency_id"]
    differences = {}
    
    for field in fields_to_compare:
        source_val = str(source_canonical.get(field, "")).strip()
        target_val = str(target_canonical.get(field, "")).strip()
        
        if source_val != target_val:
            differences[field] = {
                "source": source_canonical.get(field),
                "target": target_canonical.get(field)
            }

    # Compara valores monetarios com tolerancia para evitar ruído de floats.
    source_amount = float(source_canonical.get("amount_total", 0) or 0)
    target_amount = float(target_canonical.get("amount_total", 0) or 0)
    if abs(source_amount - target_amount) > 0.01:
        differences["amount_total"] = {
            "source": source_canonical.get("amount_total"),
            "target": target_canonical.get("amount_total"),
        }
    
    is_equal = len(differences) == 0
    return is_equal, differences

def canonical_to_toconline_sales_document_payload(canonical_doc, toconline_id=None):
    """
    Converte documento canónico para payload da API TOConline.
    
    Args:
        canonical_doc: dict em formato canónico
        toconline_id: optional, se for update
    
    Returns:
        dict pronto para POST/PATCH
    """
    payload = {
        "number": canonical_doc["number"],
        "date": canonical_doc["date"],
        "due_date": canonical_doc["due_date"],
        "partner_id": canonical_doc["partner_id"],
        "amount_total": canonical_doc["amount_total"],
        "currency": canonical_doc["currency_id"],
        "state": canonical_doc["state"],
        "document_type": "sales_invoice"
    }
    
    if toconline_id:
        payload["id"] = toconline_id
    
    return payload