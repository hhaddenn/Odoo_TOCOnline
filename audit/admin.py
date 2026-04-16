from django.contrib import admin

from .models import SyncAlert, SyncLog


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "entity_type", "direction", "status", "duration_ms")
    list_filter = ("status", "direction", "entity_type", "company")
    search_fields = ("entity_type", "error_message", "odoo_id", "toconline_id")
    readonly_fields = ("created_at",)


@admin.register(SyncAlert)
class SyncAlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "alert_type", "message")
    list_filter = ("alert_type", "company")
    search_fields = ("message",)
    readonly_fields = ("created_at",)
