from connectors.odoo_client import OdooClient, client_from_env


class OdooProductsConnector:
  def __init__(self, odoo_client=None, client=None, odoo_url=None, database=None, username=None, password=None):
    if client is not None:
      self.odoo_client = client
    elif odoo_client is not None:
      self.odoo_client = odoo_client
    elif all([odoo_url, database, username, password]):
      self.odoo_client = OdooClient(
        base_url=odoo_url,
        db=database,
        username=username,
        password=password,
      )
    else:
      self.odoo_client = client_from_env()

  def connect(self):
    self.odoo_client.authenticate()
    return self.odoo_client.health_check()
  
  def get_products(self, limit=200):
    # Fetch products from Odoo.
    products = self.odoo_client.search_read(
      'product.product',
      [],
      ['id', 'name', 'default_code', 'list_price', 'uom_id', 'taxes_id', 'write_date'],
      limit=limit,
    )
    return products
  
  def get_product_by_id(self, product_id):
    # Fetch a single product by ID
    product = self.odoo_client.search_read('product.product', [[['id', '=', product_id]]], ['name', 'default_code', 'list_price'])
    return product[0] if product else None
  
  def create_product(self, name, default_code, list_price):
    # Create a new product in Odoo
    product_id = self.odoo_client.create('product.product', {
      'name': name,
      'default_code': default_code,
      'list_price': list_price,
    })
    return product_id
  
  def update_product(self, product_id, name=None, default_code=None, list_price=None):
    # Update an existing product in Odoo
    values = {}
    if name is not None:
      values['name'] = name
    if default_code is not None:
      values['default_code'] = default_code
    if list_price is not None:
      values['list_price'] = list_price
    
    if values:
      self.odoo_client.write('product.product', [product_id], values)
      return True
    return False
  
  def delete_product(self, product_id):
    # Delete a product from Odoo
    self.odoo_client.unlink('product.product', [product_id])
    return True