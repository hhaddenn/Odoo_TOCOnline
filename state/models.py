from django.db import models


class Company(models.Model):
    """Empresa configurada para sync entre Odoo e TOConline."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CompanyConnection(models.Model):
    """Credenciais de ligação a um sistema externo (Odoo ou TOConline)."""

    class SystemType(models.TextChoices):
        ODOO = "odoo", "Odoo"
        TOCONLINE = "toconline", "TOConline"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="connections",
    )
    system = models.CharField(max_length=20, choices=SystemType.choices)
    base_url = models.URLField()
    # Credenciais armazenadas como JSON (tokens renovados são salvos automaticamente via callback)
    credentials = models.JSONField(
        default=dict,
        help_text="JSON com credenciais: {db, username, password} para Odoo ou {client_id, client_secret, refresh_token, access_token} para TOConline",
    )
    is_active = models.BooleanField(default=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company Connection"
        verbose_name_plural = "Company Connections"
        unique_together = [("company", "system")]

    def __str__(self) -> str:
        return f"{self.company.name} → {self.get_system_display()}"


class EntityLink(models.Model):
    """Mapeamento entre um registo Odoo e o correspondente em TOConline."""

    class EntityType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SUPPLIER = "supplier", "Supplier"
        PRODUCT = "product", "Product"
        TAX = "tax", "Tax"
        INVOICE = "invoice", "Invoice"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="entity_links",
    )
    entity_type = models.CharField(max_length=50, choices=EntityType.choices)
    odoo_id = models.BigIntegerField()
    toconline_id = models.CharField(max_length=255)
    # Hash do canonical para detectar drift de dados
    canonical_hash = models.CharField(max_length=64, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Entity Link"
        verbose_name_plural = "Entity Links"
        unique_together = [("company", "entity_type", "odoo_id")]
        indexes = [
            models.Index(fields=["company", "entity_type", "toconline_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type} odoo:{self.odoo_id} ↔ toc:{self.toconline_id}"


class DeletionTombstone(models.Model):
    """Regista tentativa de delete para confirmar após N ciclos."""

    class System(models.TextChoices):
        ODOO = "odoo"
        TOCONLINE = "toconline"

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    entity_type = models.CharField(max_length=50)
    system = models.CharField(max_length=20, choices=System.choices)
    original_id = models.CharField(max_length=255)  # odoo_id ou toconline_id
    pending_since = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmation_count = models.IntegerField(default=0)  # Ciclos confirmados

    class Meta:
        unique_together = [("company", "system", "original_id", "entity_type")]


class EntitySnapshot(models.Model):
    """Snapshot de lista de entidades para detectar deletes."""

    class System(models.TextChoices):
        ODOO = "odoo"
        TOCONLINE = "toconline"

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    entity_type = models.CharField(max_length=50)
    system = models.CharField(max_length=20, choices=System.choices)

    # IDs presentes neste snapshot (JSON)
    entity_ids = models.JSONField(default=list)  # ["1", "2", "3"]

    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("company", "system", "entity_type")]
        ordering = ["-taken_at"]
