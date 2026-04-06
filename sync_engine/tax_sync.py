class TaxSyncEngine:
  def __init__(self, odoo_client, source_client, company_id, logger=None):
    self.odoo = odoo_client
    self.source = source_client
    self.company_id = company_id
    self.logger = logger

  def run(self):
    taxes = self.source.get_taxes()
    created, updated, skipped, failed = 0, 0, 0, 0

    for tax in taxes:
      try:
        result = self._sync_tax(tax)
        if result == "created":
          created += 1
        elif result == "updated":
          updated += 1
        else:
          skipped += 1
      except Exception as exc:
        failed += 1
        if self.logger:
          self.logger.exception("Erro ao sincronizar imposto %s: %s", tax, exc)

    return {
      "created": created,
      "updated": updated,
      "skipped": skipped,
      "failed": failed,
      "total": len(taxes),
    }

  def _sync_tax(self, tax):
    external_id = str(tax.get("id") or "").strip()
    if not external_id:
      return "skipped"

    payload = self._prepare_tax_vals(tax)
    if not payload.get("name"):
      return "skipped"

    tax_id = self._find_existing_tax_id(external_id, payload["name"])
    if tax_id:
      self.odoo.write("account.tax", [tax_id], payload)
      return "updated"

    payload["x_external_id"] = external_id
    self.odoo.create("account.tax", payload)
    return "created"

  def _find_existing_tax_id(self, external_id, name):
    domain_external = [
      ("x_external_id", "=", external_id),
      ("company_id", "=", self.company_id),
    ]
    ids = self.odoo.search("account.tax", domain_external, limit=1)
    if ids:
      return ids[0]

    domain_name = [
      ("name", "=", name),
      ("company_id", "=", self.company_id),
    ]
    ids = self.odoo.search("account.tax", domain_name, limit=1)
    return ids[0] if ids else None

  def _prepare_tax_vals(self, tax):
    amount = float(tax.get("rate") or 0.0)
    amount_type = tax.get("amount_type") or "percent"
    tax_use = tax.get("type_tax_use") or "sale"

    return {
      "name": (tax.get("name") or "").strip(),
      "amount": amount,
      "amount_type": amount_type,
      "type_tax_use": tax_use,
      "company_id": self.company_id,
      "active": bool(tax.get("active", True)),
      "price_include": bool(tax.get("price_include", False)),
    }