from django.contrib import admin

from .models import Company, CompanyConnection, EntityLink


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(CompanyConnection)
class CompanyConnectionAdmin(admin.ModelAdmin):
    list_display = ("company", "system", "base_url", "is_active", "last_tested_at")
    list_filter = ("system", "is_active")
    search_fields = ("company__name",)


@admin.register(EntityLink)
class EntityLinkAdmin(admin.ModelAdmin):
    list_display = ("company", "entity_type", "odoo_id", "toconline_id", "last_synced_at")
    list_filter = ("entity_type", "company")
    search_fields = ("odoo_id", "toconline_id")
