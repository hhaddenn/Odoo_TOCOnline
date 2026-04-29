def map_odoo_state_to_canonical(odoo_state):
    """Mapeia estado Odoo para estado canónico."""
    state_map = {
        "draft": "PENDENTE",
        "posted": "PENDENTE",
        "paid": "LIQUIDADA",
        "cancel": "CANCELADA"
    }
    return state_map.get(odoo_state, "PENDENTE")


def map_canonical_to_toconline_document_type(document_type):
    mapping = {
        "sales_invoice": "FT",
    }
    return mapping.get(document_type, "FT")


def _extract_m2o_id(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def _extract_m2o_name(value, fallback=""):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1] or fallback
    return fallback


def _first_truthy(*values):
    for value in values:
        if value not in (None, False, "", [], {}):
            return value
    return None


def _fallback_number(prefix, external_id, *values):
    number = _first_truthy(*values)
    if number not in (None, False, ""):
        return str(number)
    return f"{prefix}-{external_id}"

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
        "number": _fallback_number("INV", odoo_doc.get("id"), odoo_doc.get("name"), odoo_doc.get("ref"), odoo_doc.get("payment_reference")),
        "date": odoo_doc.get("invoice_date"),
        "due_date": odoo_doc.get("invoice_date_due"),
        "partner_id": str(_extract_m2o_id(odoo_doc.get("partner_id"))) if _extract_m2o_id(odoo_doc.get("partner_id")) is not None else None,
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
        "number": _fallback_number("INV", toc_doc.get("id"), toc_doc.get("number"), toc_doc.get("document_number"), toc_doc.get("invoice_number")),
        "date": toc_doc.get("date"),
        "due_date": toc_doc.get("due_date"),
        "partner_id": str(toc_doc.get("customer_id")) if toc_doc.get("customer_id") not in (None, False, "") else None,
        "amount_total": float(toc_doc.get("amount_total", 0)),
        "currency_id": toc_doc.get("currency", "EUR"),
        "state": toc_doc.get("state", "PENDENTE"),
        "origin_id": str(toc_doc.get("original_document_id")) if toc_doc.get("original_document_id") not in (None, False, "") else None,
        "external_reference": toc_doc.get("external_reference"),
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
    field_pairs = [
        ("number", "external_reference"),
        ("state", "state"),
        ("date", "date"),
        ("due_date", "due_date"),
        ("currency_id", "currency_id"),
    ]
    differences = {}
    
    for source_field, target_field in field_pairs:
        source_val = str(source_canonical.get(source_field, "")).strip()
        target_val = str(target_canonical.get(target_field, "")).strip()
        
        if source_val != target_val:
            differences[source_field] = {
                "source": source_canonical.get(source_field),
                "target": target_canonical.get(target_field)
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
    amount_total = float(canonical_doc.get("amount_total", 0) or 0)
    fallback_line_price = round(amount_total, 2) if amount_total > 0 else 1.0
    lines = canonical_doc.get("lines") or [
        {
            "item_type": "Service",
            "description": f"Sync Odoo {canonical_doc.get('number', 'documento')}",
            "quantity": 1,
            "unit_price": fallback_line_price,
        }
    ]

    payload = {
        "document_type": map_canonical_to_toconline_document_type(canonical_doc.get("document_type")),
        "date": canonical_doc["date"],
        "finalize": 1,
        "due_date": canonical_doc["due_date"],
        "customer_tax_registration_number": canonical_doc.get("customer_tax_registration_number"),
        "customer_business_name": canonical_doc.get("customer_business_name"),
        "customer_address_detail": canonical_doc.get("customer_address_detail"),
        "customer_postcode": canonical_doc.get("customer_postcode"),
        "customer_city": canonical_doc.get("customer_city"),
        "customer_country": canonical_doc.get("customer_country"),
        "payment_mechanism": "MO",
        "vat_included_prices": False,
        "operation_country": "PT-MA",
        "currency_iso_code": canonical_doc["currency_id"],
        "currency_conversion_rate": 1,
        "retention": canonical_doc.get("retention", 0),
        "retention_type": "IRS",
        "apply_retention_when_paid": True,
        "customer_id": canonical_doc.get("customer_id"),
        "document_series_id": canonical_doc.get("document_series_id"),
        "settlement_expression": canonical_doc.get("settlement_expression"),
        "notes": canonical_doc.get("notes"),
        "external_reference": canonical_doc["number"],
        "made_available_to": False,
        "lines": lines,
    }

    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    
    if toconline_id:
        payload["id"] = toconline_id
    
    return payload