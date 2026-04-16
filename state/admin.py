from django.contrib import admin

from .models import Company, CompanyConnection, DeadLetterEntry, EntityLink, IdempotencyKey


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(CompanyConnection)
class CompanyConnectionAdmin(admin.ModelAdmin):
    list_display = ("company", "system", "base_url", "is_active", "last_tested_at", "rotated_at")
    list_filter = ("system", "is_active")
    search_fields = ("company__name",)


@admin.register(EntityLink)
class EntityLinkAdmin(admin.ModelAdmin):
    list_display = ("company", "entity_type", "odoo_id", "toconline_id", "last_synced_at")
    list_filter = ("entity_type", "company")
    search_fields = ("odoo_id", "toconline_id")


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("company", "entity_type", "operation", "status", "attempt_count", "updated_at")
    list_filter = ("status", "entity_type", "company")
    search_fields = ("key", "operation")


@admin.register(DeadLetterEntry)
class DeadLetterEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "entity_type", "operation", "retry_count", "is_reprocessed")
    list_filter = ("entity_type", "company", "is_reprocessed")
    search_fields = ("operation", "error_message")
