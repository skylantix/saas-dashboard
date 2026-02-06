import logging

import requests
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


def send_mailgun_email(to, subject, text, html):
    """Send an email via the Mailgun API.

    Returns:
        bool: True if the API accepted the message.

    Raises:
        RuntimeError: On API failure (so Celery autoretry can catch it).
    """
    response = requests.post(
        f"https://api.mailgun.net/v3/{settings.MAILGUN_DOMAIN}/messages",
        auth=("api", settings.MAILGUN_API_KEY),
        data={
            "from": f"Skylantix <no-reply@{settings.MAILGUN_DOMAIN}>",
            "to": to,
            "subject": subject,
            "text": text,
            "html": html,
        },
    )
    if response.status_code in (200, 201):
        return True

    logger.error("Mailgun send failed: %s - %s", response.status_code, response.text)
    raise RuntimeError(f"Mailgun send failed ({response.status_code})")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_keycloak_password_reset_email(self, keycloak_user_id):
    """Send a password-reset / set-password email via Keycloak.

    This is non-critical: a failure should not block account provisioning.
    """
    from skylantix_dash.keycloak import keycloak_admin

    logger.info(
        "Sending Keycloak password reset email for user %s", keycloak_user_id
    )

    success = keycloak_admin.send_reset_password_email(keycloak_user_id)
    if not success:
        logger.warning(
            "Failed to send reset email for Keycloak user %s", keycloak_user_id
        )
        raise RuntimeError(
            f"Keycloak password reset email failed for {keycloak_user_id}"
        )

    logger.info(
        "Sent Keycloak password reset email for user %s", keycloak_user_id
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def sync_user_post_checkout(self, user_profile_id):
    """Assign instances and sync attributes to Keycloak after checkout.

    Combines instance assignment + Keycloak attribute sync into one task
    to avoid redundant Stripe API calls (both read subscription data).
    """
    from dashboard.models import UserProfile

    try:
        profile = UserProfile.objects.get(pk=user_profile_id)
    except UserProfile.DoesNotExist:
        logger.error(
            "sync_user_post_checkout: UserProfile %s does not exist",
            user_profile_id,
        )
        return

    logger.info(
        "sync_user_post_checkout: starting for user %s (profile %s)",
        profile.user.username,
        user_profile_id,
    )

    profile.sync_instance_assignments()
    profile.sync_to_keycloak()

    logger.info(
        "sync_user_post_checkout: completed for user %s (profile %s)",
        profile.user.username,
        user_profile_id,
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def notify_subscription_canceled(self, email, first_name):
    """Notify a user that their subscription has been canceled."""
    name = first_name or "there"
    send_mailgun_email(
        to=email,
        subject="Your Skylantix subscription has been canceled",
        text=(
            f"Hi {name},\n\n"
            "Your Skylantix subscription has been canceled and your access "
            "has been suspended.\n\n"
            "If this was a mistake or you'd like to resubscribe, visit "
            "https://dash.skylantix.com to get started again.\n\n"
            "If you have any questions, reply to this email and we'll help.\n\n"
            "— The Skylantix Team"
        ),
        html=(
            '<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">'
            '<h2 style="color: #6366f1;">Skylantix</h2>'
            f"<p>Hi {name},</p>"
            "<p>Your Skylantix subscription has been canceled and your "
            "access has been suspended.</p>"
            "<p>If this was a mistake or you'd like to resubscribe, "
            '<a href="https://dash.skylantix.com" style="color: #6366f1;">'
            "visit your dashboard</a> to get started again.</p>"
            "<p>If you have any questions, just reply to this email.</p>"
            '<p style="color: #64748b; font-size: 14px; margin-top: 24px;">'
            "&mdash; The Skylantix Team</p>"
            "</div>"
        ),
    )
    logger.info("Sent subscription canceled email to %s", email)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def notify_payment_failed(self, email, first_name):
    """Notify a user that their payment failed."""
    name = first_name or "there"
    send_mailgun_email(
        to=email,
        subject="Action required: Skylantix payment failed",
        text=(
            f"Hi {name},\n\n"
            "We were unable to process your latest payment. Your access has "
            "been temporarily suspended until this is resolved.\n\n"
            "Please update your payment method at "
            "https://dash.skylantix.com to restore access.\n\n"
            "If you believe this is an error, reply to this email and "
            "we'll sort it out.\n\n"
            "— The Skylantix Team"
        ),
        html=(
            '<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">'
            '<h2 style="color: #6366f1;">Skylantix</h2>'
            f"<p>Hi {name},</p>"
            "<p>We were unable to process your latest payment. Your access "
            "has been temporarily suspended until this is resolved.</p>"
            "<p>Please <a href=\"https://dash.skylantix.com\" "
            'style="color: #6366f1;">update your payment method</a> '
            "to restore access.</p>"
            "<p>If you believe this is an error, just reply to this email.</p>"
            '<p style="color: #64748b; font-size: 14px; margin-top: 24px;">'
            "&mdash; The Skylantix Team</p>"
            "</div>"
        ),
    )
    logger.info("Sent payment failed email to %s", email)
