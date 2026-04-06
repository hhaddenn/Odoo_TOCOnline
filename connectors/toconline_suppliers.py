import httpx

from connectors.toconline_client import client_from_company, client_from_env


class TOCSuppliersConnector:
    def __init__(self, api_client=None, company=None):
        self.api_client = api_client or (client_from_company(company) if company else client_from_env())

    def _unwrap(self, payload):
        if hasattr(payload, 'json'):
            payload = payload.json()
        if isinstance(payload, dict) and 'data' in payload:
            return payload['data']
        return payload

    def get_suppliers(self, filters=None):
        return self._unwrap(self.api_client.get('/api/suppliers', params=filters))

    def get_supplier(self, supplier_id):
        try:
            return self._unwrap(self.api_client.get(f'/api/suppliers/{supplier_id}'))
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def create_supplier(self, supplier_data):
        return self._unwrap(self.api_client.post('/api/suppliers', payload=supplier_data))

    def update_supplier(self, supplier_id, supplier_data):
        return self._unwrap(self.api_client.patch(f'/api/suppliers/{supplier_id}', payload=supplier_data))

    def delete_supplier(self, supplier_id):
        try:
            self.api_client.delete(f'/api/suppliers/{supplier_id}')
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return False
            raise