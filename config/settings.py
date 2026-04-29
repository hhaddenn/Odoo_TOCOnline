import os
from pathlib import Path
from celery.schedules import crontab

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("Missing SECRET_KEY or DJANGO_SECRET_KEY in environment.")
DEBUG = os.getenv("DEBUG", "False") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "django_celery_beat",
    # local
    "connectors.apps.ConnectorsConfig",
    "sync_engine.apps.SyncEngineConfig",
    "state.apps.StateConfig",
    "audit.apps.AuditConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ── Database ───────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432"),
    }
}

# ── Cache / Celery ─────────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

CELERY_BEAT_SCHEDULE = {
    # ── Health Checks ──────────────────────────────────────────────────────────
    "health-check-toconline-every-5-min": {
        "task": "sync_engine.tasks.health_check_toconline",
        "schedule": crontab(minute="*/5"),
        "kwargs": {"company_id": 1},
    },
    # ── Master Data (Customers, Suppliers) ──────────────────────────────────────
    "sync-customers-every-10-min": {
        "task": "sync_engine.tasks.sync_customers",
        "schedule": crontab(minute="*/10"),
        "kwargs": {"company_id": 1, "dry_run": False, "allow_delete": False},
    },
    "sync-suppliers-every-10-min": {
        "task": "sync_engine.tasks.sync_suppliers",
        "schedule": crontab(minute="*/10"),
        "kwargs": {"company_id": 1, "dry_run": False, "allow_delete": False},
    },
    # ── Documents (Sales, Purchases, etc) ────────────────────────────────────────
    "sync-sales-documents-every-15-min": {
        "task": "sync_engine.tasks.sync_sales_documents",
        "schedule": crontab(minute="*/15"),
        "kwargs": {"company_id": 1, "dry_run": False},
    },
    "sync-purchase-documents-every-15-min": {
        "task": "sync_engine.tasks.sync_purchase_documents",
        "schedule": crontab(minute="*/15"),
        "kwargs": {"company_id": 1, "dry_run": False},
    },
    "sync-all-document-types-every-30-min": {
        "task": "sync_engine.tasks.sync_all_document_types",
        "schedule": crontab(minute="*/30"),
        "kwargs": {"company_id": 1, "dry_run": False},
    },
    # ── Maintenance & Alerts ──────────────────────────────────────────────────────
    "refresh-toconline-tokens-every-30-min": {
        "task": "sync_engine.tasks.force_refresh_all_toconline_tokens",
        "schedule": crontab(minute="*/30"),
    },
    "evaluate-sync-alerts-every-5-min": {
        "task": "sync_engine.tasks.evaluate_sync_alerts",
        "schedule": crontab(minute="*/5"),
    },
    "purge-old-sync-logs-daily": {
        "task": "sync_engine.tasks.purge_old_sync_logs",
        "schedule": crontab(minute=30, hour=3),
    },
    "reprocess-dead-letters-every-15-min": {
        "task": "sync_engine.tasks.reprocess_dead_letters",
        "schedule": crontab(minute="*/15"),
    },
}

SYNC_HTTP_TIMEOUT_SECONDS = int(os.getenv("SYNC_HTTP_TIMEOUT_SECONDS", "30"))
SYNC_HTTP_MAX_RETRIES = int(os.getenv("SYNC_HTTP_MAX_RETRIES", "3"))
SYNC_HTTP_BACKOFF_BASE_SECONDS = float(os.getenv("SYNC_HTTP_BACKOFF_BASE_SECONDS", "1.0"))
SYNC_HTTP_BACKOFF_MAX_SECONDS = float(os.getenv("SYNC_HTTP_BACKOFF_MAX_SECONDS", "30.0"))
SYNC_HTTP_BACKOFF_JITTER_SECONDS = float(os.getenv("SYNC_HTTP_BACKOFF_JITTER_SECONDS", "0.25"))

SYNC_BREAKER_FAILURE_THRESHOLD = int(os.getenv("SYNC_BREAKER_FAILURE_THRESHOLD", "5"))
SYNC_BREAKER_COOLDOWN_SECONDS = int(os.getenv("SYNC_BREAKER_COOLDOWN_SECONDS", "60"))

SYNC_ALERT_WINDOW_MINUTES = int(os.getenv("SYNC_ALERT_WINDOW_MINUTES", "15"))
SYNC_ALERT_FAILURE_RATE_THRESHOLD = float(os.getenv("SYNC_ALERT_FAILURE_RATE_THRESHOLD", "0.20"))

SYNC_LOG_RETENTION_DAYS = int(os.getenv("SYNC_LOG_RETENTION_DAYS", "180"))

# ── Auth ───────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── i18n ──────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "pt-pt"
TIME_ZONE = "Europe/Lisbon"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
