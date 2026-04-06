from .toconline_client import client_from_company, client_from_env


class TOCTaxesConnector:
    def __init__(self, api_client=None, company=None):
        if api_client:
            self.client = api_client
        elif company:
            self.client = client_from_company(company)
        else:
            self.client = client_from_env()

    def connect(self):
        self.client.authenticate()
        return self.client.health_check()

    def get_taxes(self, filters=None):
        return self.client.get('/api/taxes', params=filters)
    
    def get_oss_taxes(self):
        return self.client.get('/api/oss_taxes')