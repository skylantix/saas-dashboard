from django.urls import path

from . import views

app_name = "recovery"

urlpatterns = [
    path("", views.recover, name="recover"),
    path("send-code/", views.recover_send_code, name="recover_send_code"),
    path("verify-code/", views.recover_verify_code, name="recover_verify_code"),
    path(
        "checkout-session/",
        views.recover_checkout_session,
        name="recover_checkout_session",
    ),
]
