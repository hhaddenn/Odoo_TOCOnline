"""Custom Django fields para encriptação usando Fernet."""
import json
import hashlib
from base64 import urlsafe_b64encode
from django.db import models
from cryptography.fernet import Fernet


class EncryptedJSONField(models.TextField):
    """JSONField que armazena dados encriptados com Fernet."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cipher = None
    
    @property
    def cipher(self):
        """Lazy-load cipher usando SECRET_KEY do Django."""
        if self._cipher is None:
            from django.conf import settings
            # Derivar chave Fernet a partir do SECRET_KEY
            secret = settings.SECRET_KEY.encode()
            # Usar SHA256 para gerar 32 bytes, depois encode em base64 para Fernet
            key_bytes = hashlib.sha256(secret + b"odoo_toconline_sync").digest()
            key = urlsafe_b64encode(key_bytes)
            self._cipher = Fernet(key)
        return self._cipher
    
    def get_prep_value(self, value):
        """Encripta valor antes de salvar na BD."""
        if value is None:
            return None
        if isinstance(value, dict):
            json_str = json.dumps(value)
        else:
            json_str = str(value)
        encrypted = self.cipher.encrypt(json_str.encode())
        return encrypted.decode()
    
    def from_db_value(self, value, expression, connection):
        """Decripta valor quando carrega da BD."""
        if value is None:
            return None
        try:
            decrypted = self.cipher.decrypt(value.encode()).decode()
            return json.loads(decrypted)
        except Exception as e:
            # Se falhar decriptação, retorna valor original
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Erro ao desencriptar campo: {e}")
            return None
    
    def to_python(self, value):
        """Converts database value to Python value."""
        if isinstance(value, dict):
            return value
        if value is None:
            return None
        return self.from_db_value(value, None, None)
