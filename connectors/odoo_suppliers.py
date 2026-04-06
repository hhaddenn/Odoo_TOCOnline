from connectors.odoo_client import OdooClient, client_from_env


class OdooSuppliersConnector:
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

    def get_suppliers(self, limit=200):
        # Fetch suppliers from Odoo
        suppliers = self.odoo_client.search_read(
            'res.partner',
            [('supplier_rank', '>', 0)],
            ['id', 'name', 'vat', 'email', 'phone', 'street', 'city', 'zip', 'country_id', 'write_date'],
            limit=limit,
        )
        return suppliers

    def create_supplier(self, name, email=None, phone=None, street=None, city=None, zip_code=None, country_name=None):
        supplier_data = {
            'name': name,
            'email': email or '',
            'phone': phone or '',
            'street': street or '',
            'city': city or '',
            'zip': zip_code or '',
            'country_id': self._get_country_id(country_name) if country_name else False,
            'supplier_rank': 1,
        }
        return self.odoo_client.create('res.partner', supplier_data)
    
    def update_supplier(self, supplier_id, name=None, email=None, phone=None, street=None, city=None, zip_code=None, country_name=None):
        supplier_data = {}
        if name is not None:
            supplier_data['name'] = name
        if email is not None:
            supplier_data['email'] = email
        if phone is not None:
            supplier_data['phone'] = phone
        if street is not None:
            supplier_data['street'] = street
        if city is not None:
            supplier_data['city'] = city
        if zip_code is not None:
            supplier_data['zip'] = zip_code
        if country_name is not None:
            supplier_data['country_id'] = self._get_country_id(country_name)
        
        if supplier_data:
            self.odoo_client.write('res.partner', [supplier_id], supplier_data)
            return True
        return False
    
    def delete_supplier(self, supplier_id):
        try:
            self.odoo_client.unlink('res.partner', [supplier_id])
            return True
        except Exception as exc:
            # Handle exceptions (e.g., supplier not found)
            print(f"Error deleting supplier: {exc}")
            return False

    def _get_country_id(self, country_name):
        if not country_name:
            return False

        country = self.odoo_client.search_read('res.country', [('name', '=', country_name)], ['id'], limit=1)
        return country[0]['id'] if country else False