

def _extract_m2o_id(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 1:
        return value[0]
    return value


def _extract_m2o_name(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[1]
    return str(value)


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


def odoo_sales_receipt_to_canonical(odoo_receipt: dict) -> dict:
    state_map = {
        'draft': 'PENDENTE',
        'posted': 'LIQUIDADA',
        'sent': 'ENVIADA',
        'reconciled': 'LIQUIDADA',
        'cancelled': 'CANCELADA',
    }
    canonical_state = state_map.get(odoo_receipt.get('state', ''), 'PENDENTE')
    
    return {
        'external_id': str(odoo_receipt.get('id')),
        'document_type': 'sales_receipt',
        'number': _fallback_number('RCPT', odoo_receipt.get('id'), odoo_receipt.get('name'), odoo_receipt.get('payment_reference')),
        'date': str(odoo_receipt.get('date', '')),
        'partner_id': str(_extract_m2o_id(odoo_receipt.get('partner_id')) or ''),
        'amount': float(odoo_receipt.get('amount', 0)),
        'currency': _extract_m2o_name(odoo_receipt.get('currency_id', '')) or 'EUR',
        'state': canonical_state,
        'updated_at': odoo_receipt.get('write_date'),
    }


def toc_sales_receipt_to_canonical(toc_receipt: dict) -> dict:
    state_map = {
        'PENDENTE': 'PENDENTE',
        'ENVIADA': 'ENVIADA',
        'LIQUIDADA': 'LIQUIDADA',
        'CANCELADA': 'CANCELADA',
    }
    canonical_state = state_map.get(toc_receipt.get('state', ''), 'PENDENTE')
    
    return {
        'external_id': str(toc_receipt.get('id')),
        'document_type': 'sales_receipt',
        'number': _fallback_number('RCPT', toc_receipt.get('id'), toc_receipt.get('number'), toc_receipt.get('document_number')),
        'date': str(toc_receipt.get('date', '')),
        'partner_id': str(toc_receipt.get('partner_id') or '') if toc_receipt.get('partner_id') not in (None, False, '') else None,
        'amount': float(toc_receipt.get('amount', 0)),
        'currency': toc_receipt.get('currency', 'EUR'),
        'state': canonical_state,
    }


def compare_sales_receipts(source_canonical: dict, target_canonical: dict) -> tuple:
    fields_to_compare = ['date', 'partner_id', 'state']
    differences = {}
    
    for field in fields_to_compare:
        source_val = str(source_canonical.get(field, "")).strip()
        target_val = str(target_canonical.get(field, "")).strip()
        
        if source_val != target_val:
            differences[field] = {
                "source": source_canonical.get(field),
                "target": target_canonical.get(field)
            }
    
    source_amount = float(source_canonical.get("amount", 0) or 0)
    target_amount = float(target_canonical.get("amount", 0) or 0)
    if abs(source_amount - target_amount) > 0.01:
        differences["amount"] = {
            "source": source_canonical.get("amount"),
            "target": target_canonical.get("amount")
        }
    
    is_equal = len(differences) == 0
    return is_equal, differences


def canonical_to_toconline_sales_receipt_payload(canonical_receipt: dict, toconline_id: str = None) -> dict:
    payload = {
        'number': canonical_receipt.get('number'),
        'date': canonical_receipt.get('date'),
        'partner_id': canonical_receipt.get('partner_id'),
        'amount': canonical_receipt.get('amount'),
        'currency': canonical_receipt.get('currency', 'EUR'),
        'state': canonical_receipt.get('state', 'PENDENTE'),
    }
    
    if toconline_id:
        payload['id'] = toconline_id
    
    return payload
