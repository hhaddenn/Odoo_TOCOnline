from django.contrib import admin
from django.urls import path

from sync_engine.metrics import metrics_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("metrics", metrics_view),
]
