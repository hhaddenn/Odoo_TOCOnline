# Modelo de conector TOC Online Customer
import logging
from .toconline_client import client_from_env, client_from_company

logger = logging.getLogger(__name__)


class TOCCustomerConnector:
    """Conector para integração com o TOC Online, focado na gestão de clientes."""

    def __init__(self, api_client=None, company=None):
        """Inicialização do conector com o cliente de API necessário."""
        if api_client:
            self.client = api_client
        elif company:
            # Carrega credenciais da BD e passa callback para persistir tokens
            def on_token_refresh(access_token, refresh_token, token_url=None, access_token_expires_at=None):
                """Callback para salvar tokens renovados na BD."""
                if not company:
                    return
                from state.models import CompanyConnection
                try:
                    conn = CompanyConnection.objects.get(
                        company=company,
                        system=CompanyConnection.SystemType.TOCONLINE,
                        is_active=True,
                    )
                    conn.credentials = conn.credentials or {}
                    conn.credentials["access_token"] = access_token
                    conn.credentials["refresh_token"] = refresh_token
                    if token_url:
                        conn.credentials["token_url"] = token_url
                    if access_token_expires_at is not None:
                        conn.credentials["access_token_expires_at"] = access_token_expires_at
                    conn.save()
                    logger.info(f"Tokens de {company.name} atualizados na BD")
                except CompanyConnection.DoesNotExist:
                    logger.warning(f"CompanyConnection não encontrada para {company.name}")
            
            self.client = client_from_company(company, on_token_refresh=on_token_refresh)
        else:
            self.client = client_from_env()

    def connect(self):
        """Estabelece a conexão com o TOC Online e autentica-se."""
        self.client.authenticate()
        return self.client.health_check()

    def get_customers(self):
        """Pega a lista de clientes do TOC Online."""
        return self.client.get("/api/customers")

    def _validate_customer_payload(self, customer_data):
        """Valida payload do cliente."""
        if not isinstance(customer_data, dict):
            raise ValueError("Dados do cliente devem ser um dicionário")
        if not customer_data.get("data"):
            raise ValueError("O campo 'data' é obrigatório")
        if not customer_data["data"].get("attributes"):
            raise ValueError("O campo 'attributes' é obrigatório dentro de 'data'")

    def create_customer(self, customer_data):
        """Cria um novo cliente no TOC Online."""
        self._validate_customer_payload(customer_data)
        return self.client.post("/api/customers", customer_data)

    def update_customer(self, customer_id, customer_data):
        """Atualiza um cliente existente no TOC Online."""
        self._validate_customer_payload(customer_data)
        if not customer_id:
            raise ValueError("ID do cliente é obrigatório para atualização")
        return self.client.patch(f"/api/customers/{customer_id}", customer_data)

    def delete_customer(self, customer_id):
        """Remove um cliente do TOC Online."""
        if not customer_id:
            raise ValueError("ID do cliente é obrigatório para exclusão")
        return self.client.delete(f"/api/customers/{customer_id}")


if __name__ == "__main__":
    # Exemplo de uso do conector TOC Online
    connector = TOCCustomerConnector()
    if connector.connect():
        print("Conexão estabelecida com o TOC Online!")
        customers = connector.get_customers()
        print(f"Clientes encontrados: {len(customers)}")
    else:
        print("Falha ao conectar com o TOC Online. Verifique as credenciais e a conexão.")
