import os

from celery import Celery
from kombu import Exchange, Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("odoo_sync")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.task_queues = (
    Queue("default", Exchange("default"), routing_key="default"),
    Queue("sync", Exchange("sync"), routing_key="sync"),
    Queue("dead_letter", Exchange("dead_letter"), routing_key="dead_letter"),
)
app.conf.task_default_queue = "default"
app.conf.task_default_exchange = "default"
app.conf.task_default_routing_key = "default"
app.conf.task_routes = {
    "sync_engine.tasks.sync_*": {"queue": "sync", "routing_key": "sync"},
    "sync_engine.tasks.reprocess_dead_letters": {"queue": "dead_letter", "routing_key": "dead_letter"},
}
app.conf.task_annotations = {
    "sync_engine.tasks.health_check_odoo": {"rate_limit": "30/m"},
    "sync_engine.tasks.health_check_toconline": {"rate_limit": "30/m"},
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
