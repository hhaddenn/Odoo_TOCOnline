from typing import Any


def odoo_tax_to_canonical(odoo_tax: dict[str, Any]) -> dict[str, Any]:
  # Converte um tax do Odoo (`account.tax`) para formato canónico.
  raw_country = odoo_tax.get("country_id")
  if isinstance(raw_country, (list, tuple)) and raw_country:
    country = raw_country[0]
  elif raw_country:
    country = raw_country
  else:
    country = None

  return {
    "external_id": odoo_tax.get("id"),
    "name": odoo_tax.get("name"),
    "amount_type": odoo_tax.get("amount_type"),
    "amount": odoo_tax.get("amount"),
    "country": country,
    "type_tax_use": odoo_tax.get("type_tax_use"),
    "updated_at": odoo_tax.get("write_date"),
  }

def canonical_to_toconline_tax_payload(canonical_tax: dict[str, Any], toconline_id: str | int | None = None,) -> dict[str, Any]:
  # Converte tax canónico para payload JSON:API da TOConline.
  attributes = {
    "name": canonical_tax.get("name"),
    "amount_type": canonical_tax.get("amount_type"),
    "amount": canonical_tax.get("amount"),
    "country": canonical_tax.get("country"),
    "type_tax_use": canonical_tax.get("type_tax_use"),
  }

  # Remove campos vazios para evitar 400 por validação de formato/tipo no destino.
  attributes = {
    key: value
    for key, value in attributes.items()
    if value not in (None, "", False)
  }

  payload = {
    "data": {
      "type": "taxes",
      "attributes": attributes,
    },
  }

  if toconline_id is not None:
    payload["data"]["id"] = str(toconline_id)

  return payload

def odoo_tax_to_toconline_payload(
  odoo_tax: dict[str, Any],
  toconline_id: str | int | None = None,
) -> dict[str, Any]:
  # Converte um tax do Odoo diretamente para payload JSON:API da TOConline.
  canonical = odoo_tax_to_canonical(odoo_tax)
  return canonical_to_toconline_tax_payload(canonical, toconline_id=toconline_id)