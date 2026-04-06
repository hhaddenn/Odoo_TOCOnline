from typing import Any


def odoo_product_to_canonical(odoo_product: dict[str, Any]) -> dict[str, Any]:
  # Converte um produto do Odoo (`product.product`) para formato canónico.
  raw_tax_ref = odoo_product.get("taxes_id")
  if isinstance(raw_tax_ref, (list, tuple)) and raw_tax_ref:
    tax_ref = raw_tax_ref[0]
  elif raw_tax_ref:
    tax_ref = raw_tax_ref
  else:
    tax_ref = None

  code = odoo_product.get("default_code")
  return {
    "external_id": odoo_product.get("id"),
    "code": code,
    "default_code": code,
    "name": odoo_product.get("name"),
    "price": odoo_product.get("list_price"),
    "list_price": odoo_product.get("list_price"),
    "tax_ref": tax_ref,
    "updated_at": odoo_product.get("write_date"),
  }


def toc_product_to_canonical(toc_product: dict[str, Any]) -> dict[str, Any]:
  # Converte um produto da TOConline para formato canónico.
  attributes = toc_product.get("attributes", {}) if isinstance(toc_product, dict) else {}
  raw_tax_ref = attributes.get("tax_id") or toc_product.get("tax_id")
  if isinstance(raw_tax_ref, (list, tuple)) and raw_tax_ref:
    tax_ref = raw_tax_ref[0]
  elif raw_tax_ref:
    tax_ref = raw_tax_ref
  else:
    tax_ref = None

  code = attributes.get("item_code") or attributes.get("default_code") or toc_product.get("item_code")
  price = attributes.get("price") if attributes.get("price") is not None else attributes.get("list_price")
  name = attributes.get("item_description") or attributes.get("name")

  return {
    "external_id": toc_product.get("id"),
    "code": code,
    "default_code": code,
    "name": name,
    "price": price,
    "list_price": price,
    "tax_ref": tax_ref,
    "updated_at": attributes.get("updated_at") or attributes.get("modified_at") or toc_product.get("updated_at"),
  }


def compare_products(source: dict[str, Any], target: dict[str, Any]) -> dict[str, dict[str, Any]]:
  # Compara dois produtos canónicos e devolve as diferenças relevantes.
  diffs: dict[str, dict[str, Any]] = {}
  for field in ("code", "name", "price", "tax_ref"):
    source_value = source.get(field)
    target_value = target.get(field)
    if source_value != target_value:
      diffs[field] = {"source": source_value, "target": target_value}
  return diffs


def canonical_to_toconline_product_payload(canonical_product: dict[str, Any], toconline_id: str | int | None = None,) -> dict[str, Any]:
  # Converte produto canónico para payload JSON:API da TOConline.
  attributes = {
    "item_code": canonical_product.get("code") or canonical_product.get("default_code"),
    "item_description": canonical_product.get("name"),
    "price": canonical_product.get("price") if canonical_product.get("price") is not None else canonical_product.get("list_price"),
    "tax_id": canonical_product.get("tax_ref"),
  }

  # Remove campos vazios para evitar 400 por validação de formato/tipo no destino.
  attributes = {
    key: value
    for key, value in attributes.items()
    if value not in (None, "", False)
  }

  payload = {
    "data": {
      "type": "products",
      "attributes": attributes,
    },
  }

  if toconline_id is not None:
    payload["data"]["id"] = str(toconline_id)

  return payload

def odoo_product_to_toconline_payload(
  odoo_product: dict[str, Any],
  toconline_id: str | int | None = None,
) -> dict[str, Any]:
  # Converte um produto do Odoo diretamente para payload JSON:API da TOConline.
  canonical = odoo_product_to_canonical(odoo_product)
  return canonical_to_toconline_product_payload(canonical, toconline_id=toconline_id)