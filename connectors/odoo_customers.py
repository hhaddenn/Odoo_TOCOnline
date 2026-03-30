# Modelo de conector Odoo Customer
from .odoo_client import OdooClient, client_from_env

class OdooCustomerConnector:
  # Conector para integração com o Odoo, focado na gestão de clientes
  
  def __init__(self, odoo_url=None, database=None, username=None, password=None, client=None):
    # Inicialização do conector com as credenciais Odoo necessárias
    self.client = client or OdooClient(
        base_url=odoo_url,
        db=database,
        username=username,
        password=password,
    )
  
  def connect(self):
    # Estabelece a conexão com o Odoo e autentica-se
    return self.client.authenticate()
  
  def _build_customer_domain(self):
    """Constrói domain compatível entre versões Odoo para obter clientes reais."""
    fields = self.client.execute_kw(
      model="res.partner",
      method="fields_get",
      args=[],
      kwargs={"attributes": ["type"]},
    )

    domain = []

    if "customer_rank" in fields:
      domain.append(["customer_rank", ">", 0])
    elif "customer" in fields:
      domain.append(["customer", "=", True])
    elif "is_company" in fields:
      domain.append(["is_company", "=", True])

    if "active" in fields:
      domain.append(["active", "=", True])

    # Na integração TOConline atual o NIF é essencial para dedupe/link.
    if "vat" in fields:
      domain.append(["vat", "!=", False])

    return domain

  def get_customers(self):
    # Pega a lista de clientes do Odoo
    domain = self._build_customer_domain()

    return self.client.execute_kw(
      model="res.partner",
      method="search_read",
      args=[domain],
      kwargs={"fields": ["id", "name", "vat", "email", "phone", "street", "zip", "city", "country_id", "write_date"], "limit": 100}
    )

  def _validate_customer_data(self, customer_data, require_name=False):
    if not isinstance(customer_data, dict):
      raise ValueError("Dados do cliente devem ser um dicionário")
    if require_name and not customer_data.get("name"):
      raise ValueError("O campo 'name' é obrigatório para criar um cliente")
  
  def create_customer(self, customer_data):
    # Cria um novo cliente em Odoo
    self._validate_customer_data(customer_data, require_name=True)
    
    customer_id = self.client.execute_kw(
      model="res.partner",
      method="create",
      args=[customer_data],
    )
    return int(customer_id)
  
  def update_customer(self, customer_id, customer_data):
    # Atualiza um cliente existente em Odoo
    self._validate_customer_data(customer_data)
    if not customer_id:
      raise ValueError("ID do cliente é obrigatório para atualização")
    
    return bool(self.client.execute_kw(
      model="res.partner",
      method="write",
      args=[[customer_id], customer_data],
    ))
  
  def delete_customer(self, customer_id):
    # Remove um cliente do Odoo
    if not customer_id:
      raise ValueError("ID do cliente é obrigatório para exclusão")
    
    return bool(self.client.execute_kw(
      model="res.partner",
      method="unlink",
      args=[[customer_id]],
    ))


if __name__ == "__main__":
  connector = OdooCustomerConnector(client=client_from_env())
  uid = connector.connect()
  print(f"Ligado ao Odoo com uid={uid}")
  customers = connector.get_customers()
  print(f"Total devolvido: {len(customers)}")
  print("Primeiros 3:", customers[:3])