import httpx

from connectors.toconline_client import client_from_company, client_from_env


class TOCProductsConnector:
    def __init__(self, api_client=None, company=None):
      self.api_client = api_client or (client_from_company(company) if company else client_from_env())

    def _unwrap(self, payload):
      if hasattr(payload, 'json'):
        payload = payload.json()
      if isinstance(payload, dict) and 'data' in payload:
        return payload['data']
      return payload

    def connect(self):
      self.api_client.authenticate()
      return self.api_client.health_check()

    def get_products(self, filters=None):
      # Fetch products from TOConline.
      return self._unwrap(self.api_client.get('/api/products', params=filters))

    def get_product(self, product_id):
      try:
        return self._unwrap(self.api_client.get(f'/api/products/{product_id}'))
      except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
          return None
        raise
      
    def get_product_by_id(self, product_id):
      return self.get_product(product_id)
      
    def create_product(self, product_data):
      return self._unwrap(self.api_client.post('/api/products', payload=product_data))
      
    def update_product(self, product_id, product_data):
      return self._unwrap(self.api_client.patch(f'/api/products/{product_id}', payload=product_data))
      
    def delete_product(self, product_id):
      try:
        self.api_client.delete(f'/api/products/{product_id}')
        return True
      except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
          return False
        raise