import logging

from .odoo_client import OdooClient

logger = logging.getLogger(__name__)

class OdooTaxesConnector:

  def __init__(self, odoo_url=None, database=None, username=None, password=None, client=None):
    self.client = client or OdooClient(
      base_url=odoo_url,
      db=database,
      username=username,
      password=password,
    )

  def connect(self):
    self.client.authenticate()
    return self.client.health_check()

  def _has_account_tax_model(self):
    models = self.client.execute_kw(
      model="ir.model",
      method="search_read",
      args=[[['model', '=', 'account.tax']]],
      kwargs={"fields": ["model"], "limit": 1},
    )
    return bool(models)
  
  def get_taxes(self, limit=200):
    if not self._has_account_tax_model():
      logger.warning("Modelo account.tax indisponível (provavelmente Odoo free sem contabilidade).")
      return []

    return self.client.execute_kw(
      model="account.tax",
      method="search_read",
      args=[[]],
      kwargs={"fields": ["id", "name", "amount_type", "amount", "country_id", "write_date", "type_tax_use"], "limit": limit}
    )
    