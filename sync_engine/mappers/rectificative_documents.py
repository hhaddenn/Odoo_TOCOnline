def map_odoo_rectificative_state_to_canonical(odoo_state):
    """Mapeia estado Odoo para estado canónico."""
    state_map = {
        "draft": "PENDENTE",
        "posted": "PENDENTE",
        "paid": "LIQUIDADA",
        "cancel": "CANCELADA",
    }
    return state_map.get(odoo_state, "PENDENTE")


def map_odoo_move_type_to_canonical(move_type):
    """Mapeia move_type Odoo para tipo canónico rectificativo."""
    mapping = {
        "out_refund": "sales_refund",
        "in_refund": "purchase_refund",
    }
    return mapping.get(move_type)


def map_canonical_to_toconline_rectificative_type(document_type):
    """Mapeia tipo canónico para tipo TOConline."""
    if document_type == "sales_refund":
        return "NC"
    if document_type == "purchase_refund":
        return "ND"
    return "NC"


def _extract_m2o_id(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def _extract_m2o_name(value, fallback=""):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1] or fallback
    return fallback


def odoo_rectificative_document_to_canonical(odoo_doc):
    """Converte rectificativo Odoo para formato canónico."""
    canonical_type = map_odoo_move_type_to_canonical(odoo_doc.get("move_type"))

    origin_id = _extract_m2o_id(odoo_doc.get("reversed_entry_id"))

    return {
        "external_id": str(odoo_doc.get("id")),
        "document_type": canonical_type,
        "number": odoo_doc.get("name", ""),
        "date": odoo_doc.get("invoice_date"),
        "due_date": odoo_doc.get("invoice_date_due"),
        "partner_id": str(_extract_m2o_id(odoo_doc.get("partner_id"))),
        "amount_total": float(odoo_doc.get("amount_total", 0)),
        "currency_id": _extract_m2o_name(odoo_doc.get("currency_id"), fallback="EUR"),
        "state": map_odoo_rectificative_state_to_canonical(odoo_doc.get("state", "draft")),
        "origin_id": str(origin_id) if origin_id is not None else None,
        "updated_at": odoo_doc.get("write_date"),
    }


def toc_rectificative_document_to_canonical(toc_doc):
    """Converte rectificativo TOConline para formato canónico."""
    toc_type = (toc_doc.get("document_type") or "").upper()
    canonical_type = "sales_refund" if toc_type == "NC" else "purchase_refund"

    return {
        "external_id": str(toc_doc.get("id")),
        "document_type": canonical_type,
        "number": toc_doc.get("number", ""),
        "date": toc_doc.get("date"),
        "due_date": toc_doc.get("due_date"),
        "partner_id": str(toc_doc.get("partner_id")),
        "amount_total": float(toc_doc.get("amount_total", 0)),
        "currency_id": toc_doc.get("currency", "EUR"),
        "state": toc_doc.get("state", "PENDENTE"),
        "origin_id": str(toc_doc.get("original_document_id")) if toc_doc.get("original_document_id") is not None else None,
        "updated_at": toc_doc.get("updated_at"),
    }


def compare_rectificative_documents(source_canonical, target_canonical):
    """Compara dois rectificativos em formato canónico."""
    fields_to_compare = [
        "number",
        "document_type",
        "state",
        "date",
        "due_date",
        "partner_id",
        "currency_id",
        "origin_id",
    ]
    differences = {}

    for field in fields_to_compare:
        source_val = str(source_canonical.get(field, "")).strip()
        target_val = str(target_canonical.get(field, "")).strip()

        if source_val != target_val:
            differences[field] = {
                "source": source_canonical.get(field),
                "target": target_canonical.get(field),
            }

    source_amount = float(source_canonical.get("amount_total", 0) or 0)
    target_amount = float(target_canonical.get("amount_total", 0) or 0)
    if abs(source_amount - target_amount) > 0.01:
        differences["amount_total"] = {
            "source": source_canonical.get("amount_total"),
            "target": target_canonical.get("amount_total"),
        }

    is_equal = len(differences) == 0
    return is_equal, differences


def canonical_to_toconline_rectificative_payload(canonical_doc, toconline_id=None):
    """Converte documento canónico para payload rectificativo TOConline."""
    payload = {
        "number": canonical_doc["number"],
        "date": canonical_doc["date"],
        "due_date": canonical_doc["due_date"],
        "partner_id": canonical_doc["partner_id"],
        "amount_total": canonical_doc["amount_total"],
        "currency": canonical_doc["currency_id"],
        "state": canonical_doc["state"],
        "document_type": map_canonical_to_toconline_rectificative_type(canonical_doc.get("document_type")),
        "original_document_id": canonical_doc.get("origin_id"),
    }

    if toconline_id:
        payload["id"] = toconline_id

    return payload
