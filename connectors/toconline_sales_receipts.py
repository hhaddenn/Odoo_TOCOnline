from connectors.toconline_client import TOConlineClient, client_from_company


class TOCSalesReceiptsConnector:
    ENDPOINT = "/api/commercial_sales_receipts"

    def __init__(self, client: TOConlineClient):
        self.client = client

    @staticmethod
    def _unwrap(response: dict) -> dict:
        if isinstance(response, dict):
            if "data" in response:
                return response["data"] if isinstance(response["data"], dict) else {}
            return response
        return {}

    def get_sales_receipts(self, limit: int = 100) -> list:
        try:
            response = self.client.get(self.ENDPOINT, params={"limit": limit})
            data = response.get("data", []) if isinstance(response, dict) else []
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_sales_receipt(self, receipt_id: str) -> dict:
        try:
            response = self.client.get(f"{self.ENDPOINT}/{receipt_id}")
            return self._unwrap(response)
        except Exception:
            return {}

    def create_sales_receipt(self, payload: dict) -> dict:
        try:
            response = self.client.post(self.ENDPOINT, payload=payload)
            return self._unwrap(response)
        except Exception as e:
            return {"error": str(e)}

    def update_sales_receipt(self, receipt_id: str, payload: dict) -> dict:
        try:
            response = self.client.patch(f"{self.ENDPOINT}/{receipt_id}", payload=payload)
            return self._unwrap(response)
        except Exception as e:
            return {"error": str(e)}
