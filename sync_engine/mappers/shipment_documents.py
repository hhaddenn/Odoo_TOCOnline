

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


def odoo_shipment_document_to_canonical(odoo_doc: dict) -> dict:
    picking_type = odoo_doc.get('picking_type_id', {})
    picking_type_code = picking_type[1].lower() if isinstance(picking_type, (list, tuple)) else str(picking_type).lower()
    
    state_map = {
        'draft': 'PENDENTE',
        'waiting': 'PENDENTE',
        'confirmed': 'PENDENTE',
        'assigned': 'PREPARADA',
        'done': 'COMPLETA',
        'cancel': 'CANCELADA',
    }
    canonical_state = state_map.get(odoo_doc.get('state', ''), 'PENDENTE')
    
    type_map = {
        'incoming': 'receipt',
        'outgoing': 'shipment',
        'internal': 'transfer',
    }
    shipment_type = type_map.get(picking_type_code, 'shipment')
    
    return {
        'external_id': str(odoo_doc.get('id')),
        'document_type': 'shipment_document',
        'number': odoo_doc.get('name', ''),
        'type': shipment_type,
        'state': canonical_state,
        'date': str(odoo_doc.get('scheduled_date', '')),
        'partner_id': str(_extract_m2o_id(odoo_doc.get('partner_id')) or ''),
        'location_id': str(_extract_m2o_id(odoo_doc.get('location_id')) or ''),
        'location_dest_id': str(_extract_m2o_id(odoo_doc.get('location_dest_id')) or ''),
        'company_id': str(_extract_m2o_id(odoo_doc.get('company_id')) or ''),
        'updated_at': odoo_doc.get('write_date'),
    }


def toc_shipment_document_to_canonical(toc_doc: dict) -> dict:
    state_map = {
        'PENDENTE': 'PENDENTE',
        'PREPARADA': 'PREPARADA',
        'COMPLETA': 'COMPLETA',
        'CANCELADA': 'CANCELADA',
    }
    canonical_state = state_map.get(toc_doc.get('state', ''), 'PENDENTE')
    
    type_map = {
        'receipt': 'receipt',
        'shipment': 'shipment',
        'transfer': 'transfer',
    }
    shipment_type = type_map.get(toc_doc.get('type', ''), 'shipment')
    
    return {
        'external_id': str(toc_doc.get('id')),
        'document_type': 'shipment_document',
        'number': toc_doc.get('number', ''),
        'type': shipment_type,
        'state': canonical_state,
        'date': str(toc_doc.get('date', '')),
        'partner_id': str(toc_doc.get('partner_id') or ''),
        'location_id': str(toc_doc.get('location_id') or ''),
        'location_dest_id': str(toc_doc.get('location_dest_id') or ''),
        'company_id': str(toc_doc.get('company_id') or ''),
    }


def compare_shipment_documents(source_canonical: dict, target_canonical: dict) -> tuple:
    fields_to_compare = ['type', 'state', 'date', 'partner_id', 'company_id']
    differences = {}
    
    for field in fields_to_compare:
        source_val = str(source_canonical.get(field, "")).strip()
        target_val = str(target_canonical.get(field, "")).strip()
        
        if source_val != target_val:
            differences[field] = {
                "source": source_canonical.get(field),
                "target": target_canonical.get(field)
            }
    
    is_equal = len(differences) == 0
    return is_equal, differences


def canonical_to_toconline_shipment_payload(canonical_doc: dict, toconline_id: str = None) -> dict:
    payload = {
        'number': canonical_doc.get('number'),
        'type': canonical_doc.get('type', 'shipment'),
        'state': canonical_doc.get('state', 'PENDENTE'),
        'date': canonical_doc.get('date'),
        'partner_id': canonical_doc.get('partner_id'),
        'location_id': canonical_doc.get('location_id'),
        'location_dest_id': canonical_doc.get('location_dest_id'),
        'company_id': canonical_doc.get('company_id'),
    }
    
    if toconline_id:
        payload['id'] = toconline_id
    
    return payload
