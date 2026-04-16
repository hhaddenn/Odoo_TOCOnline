from django.db import models

from state.models import Company


class SyncLog(models.Model):
    """Registo de cada operação de sync."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"
        PARTIAL = "partial", "Partial"

    class Direction(models.TextChoices):
        ODOO_TO_TOC = "odoo_to_toc", "Odoo → TOConline"
        TOC_TO_ODOO = "toc_to_odoo", "TOConline → Odoo"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sync_logs",
        null=True,
        blank=True,
    )
    entity_type = models.CharField(max_length=50)
    direction = models.CharField(
        max_length=20,
        choices=Direction.choices,
        default=Direction.ODOO_TO_TOC,
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    # Identificadores do registo afectado
    odoo_id = models.BigIntegerField(null=True, blank=True)
    toconline_id = models.CharField(max_length=255, blank=True)
    # Payload útil para debug
    request_payload = models.JSONField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    duration_ms = models.IntegerField(null=True, blank=True, help_text="Duração em milissegundos")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Sync Log"
        verbose_name_plural = "Sync Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "entity_type", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.status.upper()}] {self.entity_type} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class SyncAlert(models.Model):
    class AlertType(models.TextChoices):
        FAILURE_RATE = "failure_rate", "Failure Rate"
        TOKEN_EXPIRED = "token_expired", "Token Expired"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sync_alerts",
        null=True,
        blank=True,
    )
    alert_type = models.CharField(max_length=30, choices=AlertType.choices)
    message = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["alert_type", "created_at"]),
            models.Index(fields=["company", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.alert_type}] {self.created_at:%Y-%m-%d %H:%M:%S}"
