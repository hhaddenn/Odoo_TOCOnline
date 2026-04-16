from connectors.odoo_client import OdooClient, client_from_env


class OdooRectificativeDocumentsConnector:
    """Lê documentos rectificativos do Odoo."""

    MODEL = "account.move"
    DOMAIN = [["move_type", "in", ["out_refund", "in_refund"]]]
    FIELDS = [
        "id",
        "name",
        "move_type",
        "invoice_date",
        "invoice_date_due",
        "partner_id",
        "reversed_entry_id",
        "amount_total",
        "currency_id",
        "state",
        "write_date",
    ]

    def __init__(
        self,
        odoo_client=None,
        client=None,
        odoo_url=None,
        database=None,
        username=None,
        password=None,
    ):
        if client is not None:
            self.odoo_client = client
        elif odoo_client is not None:
            self.odoo_client = odoo_client
        elif all([odoo_url, database, username, password]):
            self.odoo_client = OdooClient(
                base_url=odoo_url,
                db=database,
                username=username,
                password=password,
            )
        else:
            self.odoo_client = client_from_env()

    def connect(self):
        self.odoo_client.authenticate()
        return self.odoo_client.health_check()

    def get_rectificative_documents(self, limit=200, company_id=None):
        """Devolve rectificativos do Odoo."""
        domain = self.DOMAIN.copy()

        if company_id:
            domain.append(["company_id", "=", company_id])

        try:
            docs = self.odoo_client.execute_kw(
                self.MODEL,
                "search_read",
                [domain],
                {"fields": self.FIELDS, "limit": limit},
            )
            return docs
        except Exception:
            return []
