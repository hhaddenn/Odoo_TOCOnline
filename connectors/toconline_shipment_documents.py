from connectors.toconline_client import TOConlineClient, client_from_company


class TOCShipmentDocumentsConnector:
    ENDPOINT = "/api/commercial_shipments"

    def __init__(self, client: TOConlineClient):
        self.client = client

    @staticmethod
    def _unwrap(response: dict) -> dict:
        if isinstance(response, dict):
            if "data" in response:
                return response["data"] if isinstance(response["data"], dict) else {}
            return response
        return {}

    def get_shipment_documents(self, limit: int = 100) -> list:
        try:
            response = self.client.get(self.ENDPOINT, params={"limit": limit})
            data = response.get("data", []) if isinstance(response, dict) else []
            return data if isinstance(data, list) else []
        except Exception as e:
            return []

    def get_shipment_document(self, shipment_id: str) -> dict:
        try:
            response = self.client.get(f"{self.ENDPOINT}/{shipment_id}")
            return self._unwrap(response)
        except Exception:
            return {}

    def create_shipment_document(self, payload: dict) -> dict:
        try:
            response = self.client.post(self.ENDPOINT, payload=payload)
            return self._unwrap(response)
        except Exception as e:
            return {"error": str(e)}

    def update_shipment_document(self, shipment_id: str, payload: dict) -> dict:
        try:
            response = self.client.patch(f"{self.ENDPOINT}/{shipment_id}", payload=payload)
            return self._unwrap(response)
        except Exception as e:
            return {"error": str(e)}
