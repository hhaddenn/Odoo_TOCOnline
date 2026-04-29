def map_odoo_purchase_state_to_canonical(odoo_state):
    """Mapeia estado Odoo para estado canónico."""
    state_map = {
        "draft": "PENDENTE",
        "posted": "PENDENTE",
        "paid": "LIQUIDADA",
        "cancel": "CANCELADA",
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


def odoo_purchase_document_to_canonical(odoo_doc):
    """Converte fatura de compra Odoo para formato canónico."""
    return {
        "external_id": str(odoo_doc.get("id")),
        "document_type": "purchase_invoice",
        "number": _fallback_number("BILL", odoo_doc.get("id"), odoo_doc.get("name"), odoo_doc.get("ref"), odoo_doc.get("payment_reference")),
        "date": odoo_doc.get("invoice_date"),
        "due_date": odoo_doc.get("invoice_date_due"),
        "partner_id": str(_extract_m2o_id(odoo_doc.get("partner_id"))) if _extract_m2o_id(odoo_doc.get("partner_id")) is not None else None,
        "amount_total": float(odoo_doc.get("amount_total", 0)),
        "currency_id": _extract_m2o_name(odoo_doc.get("currency_id"), fallback="EUR"),
        "state": map_odoo_purchase_state_to_canonical(odoo_doc.get("state", "draft")),
        "origin_id": None,
        "updated_at": odoo_doc.get("write_date"),
    }


def map_canonical_to_toconline_document_type(document_type):
    mapping = {
        "purchase_invoice": "FC",
    }
    return mapping.get(document_type, "FC")


def toc_purchase_document_to_canonical(toc_doc):
    """Converte fatura de compra TOConline para formato canónico."""
    return {
        "external_id": str(toc_doc.get("id")),
        "document_type": "purchase_invoice",
        "number": _fallback_number("BILL", toc_doc.get("id"), toc_doc.get("number"), toc_doc.get("document_number"), toc_doc.get("invoice_number")),
        "date": toc_doc.get("date"),
        "due_date": toc_doc.get("due_date"),
        "partner_id": str(toc_doc.get("supplier_id")) if toc_doc.get("supplier_id") not in (None, False, "") else None,
        "amount_total": float(toc_doc.get("amount_total", 0)),
        "currency_id": toc_doc.get("currency", "EUR"),
        "state": toc_doc.get("state", "PENDENTE"),
        "origin_id": str(toc_doc.get("original_document_id")) if toc_doc.get("original_document_id") not in (None, False, "") else None,
        "external_reference": toc_doc.get("external_reference"),
        "updated_at": toc_doc.get("updated_at"),
    }


def compare_purchase_documents(source_canonical, target_canonical):
    """Compara dois documentos de compra em formato canónico."""
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
                "target": target_canonical.get(target_field),
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


def canonical_to_toconline_purchase_document_payload(canonical_doc, toconline_id=None):
    """Converte documento canónico para payload da API TOConline."""
    amount_total = float(canonical_doc.get("amount_total", 0) or 0)
    fallback_line_price = round(amount_total, 2) if amount_total > 0 else 1.0
    lines = canonical_doc.get("lines") or [
        {
            "item_type": "Product",
            "description": f"Sync Odoo {canonical_doc.get('number', 'documento')}",
            "quantity": 1,
            "unit_price": fallback_line_price,
            "tax_code": "NOR",
        }
    ]

    payload = {
        "document_type": map_canonical_to_toconline_document_type(canonical_doc.get("document_type")),
        "date": canonical_doc["date"],
        "company_id": canonical_doc.get("company_id"),
        "document_series_id": canonical_doc.get("document_series_id"),
        "supplier_tax_registration_number": canonical_doc.get("supplier_tax_registration_number"),
        "supplier_business_name": canonical_doc.get("supplier_business_name"),
        "supplier_address_detail": canonical_doc.get("supplier_address_detail"),
        "supplier_postcode": canonical_doc.get("supplier_postcode"),
        "supplier_city": canonical_doc.get("supplier_city"),
        "supplier_country": canonical_doc.get("supplier_country"),
        "due_date": canonical_doc["due_date"],
        "payment_mechanism": "MO",
        "vat_included_prices": False,
        "tax_exemption_reason_id": canonical_doc.get("tax_exemption_reason_id"),
        "currency_id": canonical_doc.get("currency_id"),
        "currency_iso_code": canonical_doc["currency_id"],
        "currency_conversion_rate": 1,
        "retention_type": "TD",
        "supplier_id": canonical_doc.get("supplier_id"),
        "settlement_expression": canonical_doc.get("settlement_expression"),
        "retention_total": canonical_doc.get("retention_total", 0),
        "notes": "",
        "external_reference": canonical_doc["number"],
        "lines": lines,
    }

    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    if toconline_id:
        payload["id"] = toconline_id

    return payload
