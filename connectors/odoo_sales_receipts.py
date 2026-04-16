from connectors.odoo_client import OdooClient, client_from_env


class OdooSalesReceiptsConnector:
    def __init__(self, client: OdooClient):
        self.client = client

    def get_sales_receipts(self, limit: int = 100, company_id: int = 1) -> list:
        domain = [
            ['payment_type', '=', 'inbound'],
            ['company_id', '=', company_id],
        ]
        
        fields = [
            'id',
            'name',
            'date',
            'partner_id',
            'amount',
            'currency_id',
            'state',
            'write_date',
        ]
        
        return self.client.search_read('account.payment', domain, fields, limit=limit) or []

    def get_sales_receipt(self, payment_id: int) -> dict:
        result = self.client.search_read(
            'account.payment',
            [['id', '=', payment_id]],
            ['id', 'name', 'date', 'partner_id', 'amount', 'currency_id', 'state', 'write_date'],
            limit=1,
        )
        return result[0] if result else None

    def create_sales_receipt(self, payload: dict) -> int:
        if 'payment_type' not in payload:
            payload['payment_type'] = 'inbound'
        
        return self.client.execute_kw('account.payment', 'create', [payload])

    def update_sales_receipt(self, payment_id: int, payload: dict) -> bool:
        return self.client.execute_kw('account.payment', 'write', [[payment_id], payload])
