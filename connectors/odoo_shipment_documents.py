from connectors.odoo_client import OdooClient, client_from_env


class OdooShipmentDocumentsConnector:
    def __init__(self, client: OdooClient):
        self.client = client

    def get_shipment_documents(self, limit: int = 100, company_id: int = 1) -> list:
        domain = [
            ['company_id', '=', company_id],
            ['picking_type_id.code', 'in', ['incoming', 'outgoing']],
        ]
        
        fields = [
            'id',
            'name',
            'picking_type_id',
            'state',
            'scheduled_date',
            'location_id',
            'location_dest_id',
            'partner_id',
            'company_id',
            'write_date',
        ]
        
        return self.client.search_read('stock.picking', domain, fields, limit=limit) or []

    def get_shipment_document(self, shipment_id: int) -> dict:
        result = self.client.search_read(
            'stock.picking',
            [['id', '=', shipment_id]],
            ['id', 'name', 'picking_type_id', 'state', 'scheduled_date', 'location_id', 'location_dest_id', 'partner_id', 'write_date'],
            limit=1,
        )
        return result[0] if result else None

    def create_shipment_document(self, payload: dict) -> int:
        return self.client.execute_kw('stock.picking', 'create', [payload])

    def update_shipment_document(self, shipment_id: int, payload: dict) -> bool:
        return self.client.execute_kw('stock.picking', 'write', [[shipment_id], payload])
