"""
URL configuration for skylantix_dash project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import logging

from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.urls import include, path

logger = logging.getLogger(__name__)


def admin_login(request):
    """Redirect admin login to OIDC, or show error if already authenticated but not staff."""
    if request.user.is_authenticated:
        return HttpResponseForbidden('Access denied. You must be a member of the Skylantix Admin group.')
    return redirect('/oidc/authenticate/?next=' + request.GET.get('next', '/admin/'))


def health(request):
    """Health check endpoint for load balancers and monitoring.

    Returns 200 with ``{"status": "ok"}`` when the application and
    database are reachable, or 503 if the database check fails.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return JsonResponse({"status": "error", "detail": "database unavailable"}, status=503)


def metrics(request):
    """Prometheus metrics endpoint, gated by a bearer token.

    Configure ``PROMETHEUS_METRICS_API_KEY`` in your environment. Prometheus
    scrape config should include::

        authorization:
          type: Bearer
          credentials: <your-key>
    """
    expected_key = settings.PROMETHEUS_METRICS_API_KEY
    if not expected_key:
        return HttpResponse("Metrics endpoint not configured.", status=404)

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != expected_key:
        return HttpResponse("Unauthorized", status=401)

    # Delegate to the django-prometheus export view.
    from django_prometheus.exports import ExportToDjangoView

    return ExportToDjangoView(request)


admin.site.login = admin_login

urlpatterns = [
    path('health/', health, name='health'),
    path('metrics', metrics, name='prometheus-metrics'),
    path('admin/', admin.site.urls),
    path('oidc/', include('mozilla_django_oidc.urls')),
    path('onboarding/', include('onboarding.urls')),
    path('recovery/', include('onboarding.urls_recovery')),
    path('', include('dashboard.urls')),
]
