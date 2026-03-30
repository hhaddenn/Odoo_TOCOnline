from __future__ import annotations

from typing import Any


def odoo_to_canonical(odoo_customer: dict[str, Any]) -> dict[str, Any]:
  # Converte um customer do Odoo (`res.partner`) para formato canónico.
  raw_country = odoo_customer.get("country_id")
  if isinstance(raw_country, (list, tuple)) and raw_country:
    country = raw_country[0]
  elif raw_country:
    country = raw_country
  else:
    country = None

  return {
    "external_id": odoo_customer.get("id"),
    "name": odoo_customer.get("name"),
    "vat": odoo_customer.get("vat"),
    "email": odoo_customer.get("email"),
    "phone": odoo_customer.get("phone"),
    "address": {
      "street": odoo_customer.get("street"),
      "zip": odoo_customer.get("zip"),
      "city": odoo_customer.get("city"),
    },
    "country": country,
    "updated_at": odoo_customer.get("write_date"),
  }


def canonical_to_toconline_payload(canonical_customer: dict[str, Any], toconline_id: str | int | None = None,) -> dict[str, Any]:
  # Converte customer canónico para payload JSON:API da TOConline.
  attributes = {
    "business_name": canonical_customer.get("name"),
    "tax_registration_number": canonical_customer.get("vat"),
    "email": canonical_customer.get("email"),
    "phone_number": canonical_customer.get("phone"),
  }

  # Remove campos vazios para evitar 400 por validação de formato/tipo no destino.
  attributes = {
    key: value
    for key, value in attributes.items()
    if value not in (None, "", False)
  }

  payload = {
    "data": {
      "type": "customers",
      "attributes": attributes,
    },
  }

  if toconline_id is not None:
    payload["data"]["id"] = str(toconline_id)

  return payload


def odoo_to_toconline_payload(
  odoo_customer: dict[str, Any],
  toconline_id: str | int | None = None,
) -> dict[str, Any]:
  # Atalho: Odoo -> canónico -> payload TOConline.
  return canonical_to_toconline_payload(odoo_to_canonical(odoo_customer), toconline_id)