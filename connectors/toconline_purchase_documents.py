from connectors.toconline_client import client_from_company, client_from_env


class TOCPurchaseDocumentsConnector:
    """Lê e escreve faturas de compra no TOConline."""

    ENDPOINT = "/api/commercial_purchase_documents"

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

    def get_purchase_documents(self, filters=None):
        """Devolve faturas de compra do TOConline."""
        try:
            response = self.api_client.get(self.ENDPOINT, params=filters)
            unwrapped = self._unwrap(response)
            return unwrapped if isinstance(unwrapped, list) else []
        except Exception:
            return []

    def get_purchase_document(self, document_id):
        """Devolve uma fatura de compra específica."""
        try:
            return self._unwrap(self.api_client.get(f"{self.ENDPOINT}/{document_id}"))
        except Exception:
            return None

    def create_purchase_document(self, payload):
        """Cria uma nova fatura de compra."""
        return self._unwrap(self.api_client.post(self.ENDPOINT, payload=payload))

    def update_purchase_document(self, document_id, payload):
        """Atualiza uma fatura de compra existente."""
        return self._unwrap(self.api_client.patch(f"{self.ENDPOINT}/{document_id}", payload=payload))
