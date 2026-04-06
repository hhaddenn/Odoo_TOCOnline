from typing import Any


def odoo_supplier_to_canonical(supplier: dict[str, Any]) -> dict[str, Any]:
    raw_country = supplier.get('country_id')
    if isinstance(raw_country, (list, tuple)) and raw_country:
        country = raw_country[0]
    elif raw_country:
        country = raw_country
    else:
        country = None

    return {
        'external_id': supplier.get('id'),
        'name': supplier.get('name'),
        'vat': supplier.get('vat'),
        'email': supplier.get('email', ''),
        'phone': supplier.get('phone', ''),
        'street': supplier.get('street', ''),
        'city': supplier.get('city', ''),
        'zip': supplier.get('zip', ''),
        'country': country,
        'updated_at': supplier.get('write_date'),
    }

def canonical_to_toconline_supplier_payload(canonical_supplier: dict[str, Any], toconline_id: str | int | None = None) -> dict[str, Any]:
    attributes = {
        'business_name': canonical_supplier.get('name'),
        'tax_registration_number': canonical_supplier.get('vat'),
        'email': canonical_supplier.get('email', ''),
        'phone_number': canonical_supplier.get('phone', ''),
        'street': canonical_supplier.get('street', ''),
        'city': canonical_supplier.get('city', ''),
        'zip_code': canonical_supplier.get('zip', ''),
        'country': canonical_supplier.get('country', False),
    }

    # Remove empty fields to avoid 400 errors due to validation on the destination.
    attributes = {key: value for key, value in attributes.items() if value not in (None, '', False)}

    payload = {
        'data': {
            'type': 'suppliers',
            'attributes': attributes,
        },
     }

    if toconline_id is not None:
        payload['data']['id'] = str(toconline_id)

    return payload

def odoo_supplier_to_toconline_payload(supplier: dict[str, Any], toconline_id: str | int | None = None) -> dict[str, Any]:
    canonical = odoo_supplier_to_canonical(supplier)
    return canonical_to_toconline_supplier_payload(canonical, toconline_id=toconline_id)