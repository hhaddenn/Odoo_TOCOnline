from connectors.toconline_client import client_from_company, client_from_env


class TOCSalesDocumentsConnector:
    """Lê e escreve faturas de venda no TOConline."""
    
    ENDPOINT = "/api/commercial_sales_documents"
    
    def __init__(self, api_client=None, company=None):
        self.api_client = api_client or (client_from_company(company) if company else client_from_env())

    def _unwrap(self, payload):
        if hasattr(payload, "json"):
            payload = payload.json()
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def connect(self):
        self.api_client.authenticate()
        return self.api_client.health_check()

    def get_sales_documents(self, filters=None):
        """
        Devolve faturas de venda do TOConline.
        
        Args:
            filters: dict com filtros opcionais, ex: {"state": "PENDENTE"}
        
        Returns:
            lista de dicts com faturas
        """
        try:
            response = self.api_client.get(self.ENDPOINT, params=filters)
            unwrapped = self._unwrap(response)
            return unwrapped if isinstance(unwrapped, list) else []
        except Exception as e:
            return []
    
    def get_sales_document(self, document_id):
        """Devolve uma fatura específica."""
        try:
            return self._unwrap(self.api_client.get(f"{self.ENDPOINT}/{document_id}"))
        except Exception:
            return None
    
    def create_sales_document(self, payload):
        """Cria uma nova fatura."""
        return self._unwrap(self.api_client.post(self.ENDPOINT, payload=payload))
    
    def update_sales_document(self, document_id, payload):
        """Atualiza uma fatura existente."""
        return self._unwrap(self.api_client.patch(f"{self.ENDPOINT}/{document_id}", payload=payload))