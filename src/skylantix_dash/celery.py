import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "skylantix_dash.settings")

app = Celery("skylantix_dash")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
