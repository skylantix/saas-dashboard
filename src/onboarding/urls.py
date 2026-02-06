from django.urls import path

from . import views

app_name = "onboarding"

urlpatterns = [
    path("", views.start, name="start"),
    path("plan/", views.plan, name="plan"),
    path("addons/", views.addons, name="addons"),
    path("checkout/", views.checkout, name="checkout"),
    path(
        "checkout/session/",
        views.create_checkout_session,
        name="create_checkout_session",
    ),
    path("checkout/validate/", views.validate_account, name="validate_account"),
    path(
        "checkout/send-code/",
        views.send_verification_code,
        name="send_verification_code",
    ),
    path("checkout/verify-code/", views.verify_email_code, name="verify_email_code"),
    path("success/", views.success, name="success"),
    path(
        "success/resend-password/",
        views.resend_password_email,
        name="resend_password_email",
    ),
    path("cancel/", views.cancel, name="cancel"),
    path("waitlist/", views.waitlist, name="waitlist"),
    path("waitlist/submit/", views.waitlist_submit, name="waitlist_submit"),
    path("webhook/", views.stripe_webhook, name="webhook"),
]
