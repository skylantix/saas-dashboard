import json
import logging

import requests
import stripe
from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


def _get_stripe_prices():
    """
    Build price lookup from ProductPrice model.
    Returns dict like: {'nextcloud': {'monthly': 'price_xxx', 'annual': 'price_yyy'}, ...}
    """
    from dashboard.models import ProductPrice

    prices = {}
    for pp in ProductPrice.objects.filter(is_active=True).select_related("product"):
        slug = pp.product.slug
        if slug not in prices:
            prices[slug] = {}
        prices[slug][pp.billing_period] = pp.stripe_price_id
    return prices


def _get_display_prices():
    """
    Build display price lookup from ProductPrice model.
    Returns dict like: {'nextcloud': {'monthly': 12.00, 'annual': 120.00}, ...}
    """
    from dashboard.models import ProductPrice

    prices = {}
    for pp in ProductPrice.objects.filter(is_active=True).select_related("product"):
        slug = pp.product.slug
        if slug not in prices:
            prices[slug] = {}
        prices[slug][pp.billing_period] = float(pp.amount)
    return prices


def start(request):
    """Step 1: Welcome - ask for first name."""
    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        request.session["onboarding"] = {"first_name": first_name}
        return redirect("onboarding:plan")

    return render(request, "onboarding/start.html")


def plan(request):
    """Step 2: Choose a subscription plan and optional general add-ons."""
    from dashboard.models import Product

    onboarding_data = request.session.get("onboarding")
    if not onboarding_data:
        return redirect("onboarding:start")

    if request.method == "POST":
        plan_id = request.POST.get("plan_id", "").strip()
        billing_cycle = request.POST.get("billing_cycle", "monthly").strip()
        general_addons = request.POST.getlist("general_addons")

        onboarding_data["plan_id"] = plan_id
        onboarding_data["billing_cycle"] = billing_cycle
        onboarding_data["general_addons"] = general_addons
        request.session["onboarding"] = onboarding_data
        return redirect("onboarding:addons")

    # Get featured products for plan selection (large cards - select ONE)
    products = Product.objects.filter(page="plan", is_active=True).prefetch_related(
        "prices"
    )

    # Get general add-ons (Bitwarden, Immich - shown below plan cards)
    general_addons = Product.objects.filter(
        page="addon", is_active=True
    ).prefetch_related("prices")

    return render(
        request,
        "onboarding/plan.html",
        {
            "first_name": onboarding_data.get("first_name"),
            "products": products,
            "general_addons": general_addons,
        },
    )


def addons(request):
    """Step 3: Select storage add-ons for your plan."""
    from dashboard.models import Product

    onboarding_data = request.session.get("onboarding")
    if not onboarding_data or not onboarding_data.get("plan_id"):
        return redirect("onboarding:plan")

    # Get selected plan (redirect if invalid or missing)
    plan_slug = onboarding_data.get("plan_id")
    selected_plan = Product.objects.filter(slug=plan_slug).first()
    if not selected_plan:
        return redirect("onboarding:plan")

    # Get storage add-ons that belong to the selected product
    storage_addons = Product.objects.filter(
        page="storage", parent=selected_plan, is_active=True
    ).prefetch_related("prices")

    # If no storage addons available for this product, skip to checkout
    if not storage_addons.exists():
        # Combine general addons into final addons list
        onboarding_data["addons"] = onboarding_data.get("general_addons", [])
        request.session["onboarding"] = onboarding_data
        return redirect("onboarding:checkout")

    if request.method == "POST":
        # Get selected storage addons from form
        selected_storage = request.POST.getlist("storage_addons")
        # Combine general addons + storage addons
        onboarding_data["addons"] = (
            onboarding_data.get("general_addons", []) + selected_storage
        )
        request.session["onboarding"] = onboarding_data
        return redirect("onboarding:checkout")

    # Get general addons that were selected (for summary display)
    general_addon_slugs = onboarding_data.get("general_addons", [])
    selected_general_addons = Product.objects.filter(
        slug__in=general_addon_slugs, is_active=True
    ).prefetch_related("prices")

    return render(
        request,
        "onboarding/addons.html",
        {
            "first_name": onboarding_data.get("first_name"),
            "selected_plan": selected_plan,
            "billing_cycle": onboarding_data.get("billing_cycle", "monthly"),
            "storage_addons": storage_addons,
            "selected_general_addons": selected_general_addons,
        },
    )


def checkout(request):
    """Step 4: Collect account info and payment."""
    from dashboard.models import Product

    onboarding_data = request.session.get("onboarding")
    if not onboarding_data or not onboarding_data.get("plan_id"):
        return redirect("onboarding:plan")

    billing_cycle = onboarding_data.get("billing_cycle", "monthly")
    plan_id = onboarding_data.get("plan_id")
    addon_slugs = onboarding_data.get("addons", [])

    prices = _get_display_prices()

    # Plan: name and price from DB
    plan_product = Product.objects.filter(slug=plan_id).first()
    plan_name = plan_product.name if plan_product else plan_id.replace("_", " ").title()
    plan_price = float(prices.get(plan_id, {}).get(billing_cycle, 0))

    # Add-ons: list of {name, slug, price} from DB
    addon_products = (
        Product.objects.filter(slug__in=addon_slugs, is_active=True)
        if addon_slugs
        else []
    )
    addon_list = []
    for p in addon_products:
        addon_list.append(
            {
                "name": p.name,
                "slug": p.slug,
                "price": float(prices.get(p.slug, {}).get(billing_cycle, 0)),
            }
        )
    addon_total = sum(a["price"] for a in addon_list)
    total = plan_price + addon_total

    return render(
        request,
        "onboarding/checkout.html",
        {
            "first_name": onboarding_data.get("first_name"),
            "plan_name": plan_name,
            "plan_id": plan_id,
            "billing_cycle": billing_cycle,
            "addon_list": addon_list,
            "plan_price": plan_price,
            "addon_total": addon_total,
            "total": total,
            "stripe_publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
        },
    )


@require_POST
def validate_account(request):
    """Check if username and email are available in Keycloak."""
    try:
        from skylantix_dash.keycloak import KeycloakError, keycloak_admin

        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()
        username = data.get("username", "").strip().lower()

        errors = {}

        if email:
            existing_email = keycloak_admin.get_user_by_email(email)
            if existing_email:
                errors["email"] = "This email is already registered"

        if username:
            existing_username = keycloak_admin.get_user_by_username(username)
            if existing_username:
                errors["username"] = "This username is already taken"

        if errors:
            return JsonResponse({"valid": False, "errors": errors})

        return JsonResponse({"valid": True})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    except KeycloakError as e:
        import logging

        logging.error(f"Keycloak validation failed: {e}")
        error_code = e.status_code or "unknown"
        return JsonResponse(
            {
                "error": f"Unable to verify account availability. Please try again later. ({error_code})"
            },
            status=503,
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def send_verification_code(request):
    """Send a 6-digit verification code to the provided email via Mailgun."""
    import random

    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()

        if not email or "@" not in email:
            return JsonResponse({"error": "Valid email is required"}, status=400)

        # Generate 6-digit code
        code = f"{random.randint(0, 999999):06d}"

        # Store in session with timestamp
        import time

        request.session["email_verification"] = {
            "email": email,
            "code": code,
            "timestamp": time.time(),
        }

        # Send via Mailgun
        response = requests.post(
            f"https://api.mailgun.net/v3/{settings.MAILGUN_DOMAIN}/messages",
            auth=("api", settings.MAILGUN_API_KEY),
            data={
                "from": f"Skylantix <no-reply@{settings.MAILGUN_DOMAIN}>",
                "to": email,
                "subject": "Your Skylantix verification code",
                "text": f"Your verification code is: {code}\n\nThis code expires in 10 minutes.",
                "html": f"""
                    <div style="font-family: sans-serif; max-width: 400px; margin: 0 auto;">
                        <h2 style="color: #6366f1;">Skylantix</h2>
                        <p>Your verification code is:</p>
                        <p style="font-size: 32px; font-weight: bold; letter-spacing: 4px; color: #1e293b;">{code}</p>
                        <p style="color: #64748b; font-size: 14px;">This code expires in 10 minutes.</p>
                    </div>
                """,
            },
        )

        if response.status_code in (200, 201):
            return JsonResponse({"sent": True})
        else:
            logger.error(
                f"Mailgun send failed: {response.status_code} - {response.text}"
            )
            return JsonResponse(
                {"error": "Failed to send verification email"}, status=500
            )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    except Exception as e:
        logger.error(f"Email verification error: {e}")
        return JsonResponse({"error": "Something went wrong"}, status=500)


@require_POST
def verify_email_code(request):
    """Verify the 6-digit code entered by the user."""
    import time

    try:
        data = json.loads(request.body)
        code = data.get("code", "").strip()
        email = data.get("email", "").strip().lower()

        if not code or not email:
            return JsonResponse({"error": "Code and email are required"}, status=400)

        verification = request.session.get("email_verification")

        if not verification:
            return JsonResponse(
                {
                    "verified": False,
                    "error": "No verification in progress. Please request a new code.",
                }
            )

        # Check email matches
        if verification["email"] != email:
            return JsonResponse(
                {
                    "verified": False,
                    "error": "Email mismatch. Please request a new code.",
                }
            )

        # Check expiry (10 minutes)
        if time.time() - verification["timestamp"] > 600:
            return JsonResponse(
                {"verified": False, "error": "Code expired. Please request a new code."}
            )

        # Check code
        if verification["code"] != code:
            return JsonResponse({"verified": False, "error": "Incorrect code"})

        # Mark as verified
        request.session["email_verified"] = email
        del request.session["email_verification"]

        return JsonResponse({"verified": True})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    except Exception as e:
        logger.error(f"Code verification error: {e}")
        return JsonResponse({"error": "Something went wrong"}, status=500)


@require_POST
def create_checkout_session(request):
    """Create a Stripe Checkout Session for embedded checkout."""
    try:
        data = json.loads(request.body)

        # Get account info from request
        email = data.get("email", "").strip()
        last_name = data.get("last_name", "").strip()
        username = data.get("username", "").strip()

        if not email or not last_name or not username:
            return JsonResponse({"error": "All fields are required"}, status=400)

        # Require email to have been verified in this session
        verified_email = request.session.get("email_verified", "").strip().lower()
        if verified_email != email.strip().lower():
            return JsonResponse(
                {"error": "Please verify your email before continuing to payment."},
                status=403,
            )

        # Get onboarding data from session
        onboarding_data = request.session.get("onboarding", {})
        first_name = onboarding_data.get("first_name", "")
        plan_id = onboarding_data.get("plan_id", "").strip()
        billing_cycle = onboarding_data.get("billing_cycle", "monthly")
        addons = onboarding_data.get("addons", [])

        if not plan_id:
            return JsonResponse(
                {"error": "No plan selected. Please start from the plan step."},
                status=400,
            )

        # Store account info in session for later
        onboarding_data["email"] = email
        onboarding_data["last_name"] = last_name
        onboarding_data["username"] = username
        request.session["onboarding"] = onboarding_data

        # Build line items from database prices
        line_items = []
        stripe_prices = _get_stripe_prices()

        if plan_id in stripe_prices and billing_cycle in stripe_prices[plan_id]:
            line_items.append(
                {
                    "price": stripe_prices[plan_id][billing_cycle],
                    "quantity": 1,
                }
            )

        for addon in addons:
            if addon in stripe_prices and billing_cycle in stripe_prices[addon]:
                line_items.append(
                    {
                        "price": stripe_prices[addon][billing_cycle],
                        "quantity": 1,
                    }
                )

        if not line_items:
            return JsonResponse(
                {
                    "error": "No valid plan or add-ons. Please go back and select a plan."
                },
                status=400,
            )

        # Create Stripe Checkout Session in embedded mode
        checkout_session = stripe.checkout.Session.create(
            ui_mode="embedded",
            mode="subscription",
            line_items=line_items,
            customer_email=email,
            return_url=request.build_absolute_uri("/onboarding/success/")
            + "?session_id={CHECKOUT_SESSION_ID}",
            metadata={
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
            },
        )

        return JsonResponse(
            {
                "clientSecret": checkout_session.client_secret,
            }
        )

    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def success(request):
    """
    Handle successful checkout - display only.

    Account provisioning is handled by the Stripe webhook (checkout.session.completed).
    This view just verifies payment completed and shows a confirmation page.
    """
    session_id = request.GET.get("session_id")

    if not session_id:
        return redirect("onboarding:checkout")

    try:
        # Retrieve the checkout session to verify payment
        session = stripe.checkout.Session.retrieve(session_id)

        if session.status != "complete":
            return redirect("onboarding:checkout")

        # Get display info from session metadata and customer details
        first_name = session.metadata.get("first_name", "")
        email = session.customer_details.email if session.customer_details else ""

        # Clear onboarding session data
        if "onboarding" in request.session:
            del request.session["onboarding"]

        return render(
            request,
            "onboarding/success.html",
            {
                "email": email,
                "first_name": first_name,
            },
        )

    except stripe.error.StripeError as e:
        return render(
            request,
            "onboarding/error.html",
            {"error": f"Payment verification failed: {str(e)}"},
        )


def cancel(request):
    """Handle cancelled checkout."""
    return render(request, "onboarding/cancel.html")


def waitlist(request):
    """Public waitlist signup page."""
    return render(request, "onboarding/waitlist.html")


@require_POST
def waitlist_submit(request):
    """Handle waitlist form submission via Mailgun."""
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip()

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        # Add to Mailgun mailing list
        response = requests.post(
            f"https://api.mailgun.net/v3/lists/{settings.MAILGUN_WAITLIST_ADDRESS}/members",
            auth=("api", settings.MAILGUN_API_KEY),
            data={
                "address": email,
                "subscribed": True,
            },
        )

        if response.status_code in (200, 201):
            return JsonResponse(
                {"message": "You're on the list! We'll be in touch soon."}
            )
        elif response.status_code == 400 and "already exists" in response.text.lower():
            return JsonResponse({"message": "You're already on the waitlist!"})
        else:
            return JsonResponse(
                {"error": f"Failed to join waitlist. Status: {response.status_code}"},
                status=500,
            )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request"}, status=400)
    except Exception:
        return JsonResponse(
            {"error": "Something went wrong. Please try again."}, status=500
        )


@csrf_exempt
def stripe_webhook(request):
    """
    Handle Stripe webhook events.

    Events handled:
    - checkout.session.completed: Grant entitlements, create user if needed
    - customer.subscription.updated: Update entitlements (plan changes, addons)
    - customer.subscription.deleted: Revoke entitlements
    - invoice.payment_failed: Handle payment issues
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return HttpResponse(status=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        logger.error(f"Invalid payload in webhook: {e}")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature in webhook: {e}")
        return HttpResponse(status=400)

    event_type = event["type"]
    data = event["data"]["object"]

    event_id = event.get("id")
    # Use WARNING so these show up even when INFO is suppressed
    logger.warning("Stripe webhook received: id=%s type=%s", event_id, event_type)

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data)
        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(data)
        else:
            logger.warning("Unhandled webhook event type: %s", event_type)

    except Exception as e:
        logger.error(
            "Error processing webhook %s: %s", event_type, e, exc_info=True
        )
        return HttpResponse(status=500)

    return HttpResponse(status=200)


def _extract_subscription_items(subscription_data):
    """Extract line items from a Stripe subscription object or dict.

    Works with both expanded Stripe objects (from API calls) and raw dicts
    (from webhook payloads).

    Returns:
        list: Subscription item objects/dicts, or empty list.
    """
    if isinstance(subscription_data, str):
        return []  # Unexpanded subscription ID
    try:
        return subscription_data["items"]["data"]
    except (KeyError, TypeError):
        return []


def _extract_session_email(session, metadata):
    """Try multiple sources for the customer email.

    Stripe payloads differ between real checkouts, CLI triggers, and
    expanded-vs-unexpanded objects, so we check several locations.

    Returns:
        str or None: The customer email if found.
    """
    if session.customer_details and session.customer_details.email:
        return session.customer_details.email
    if getattr(session, "customer_email", None):
        return session.customer_email
    if isinstance(session.customer, dict) and session.customer.get("email"):
        return session.customer.get("email")
    if session.customer and hasattr(session.customer, "email"):
        return session.customer.email
    if metadata.get("email"):
        return metadata.get("email")
    return None


def _get_or_create_keycloak_user(email, username, first_name, last_name):
    """Find an existing Keycloak user or create a new one.

    Returns:
        tuple: (keycloak_user_id: str, is_new_user: bool) on success.

    Raises:
        RuntimeError: If user creation fails.
    """
    from skylantix_dash.keycloak import keycloak_admin

    existing_user = keycloak_admin.get_user_by_email(email)
    if existing_user:
        logger.info(
            "Keycloak: found existing user %s for email %s",
            existing_user["id"],
            email,
        )
        return existing_user["id"], False

    if not username:
        username = email.split("@")[0]

    success, keycloak_user_id, error = keycloak_admin.create_user(
        email=email,
        username=username,
        first_name=first_name,
        last_name=last_name,
        email_verified=True,
        enabled=True,
    )

    if not success:
        raise RuntimeError(
            f"Failed to create Keycloak user for {email}: {error}"
        )

    logger.info(
        "Keycloak: created user %s for email %s", keycloak_user_id, email
    )
    return keycloak_user_id, True


def _provision_django_user(
    email, username, first_name, last_name, keycloak_user_id, session
):
    """Create or update the Django User and UserProfile.

    Returns:
        UserProfile: The provisioned profile.
    """
    from dashboard.models import UserProfile

    django_user, _ = User.objects.get_or_create(
        username=username or email.split("@")[0],
        defaults={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )

    profile, _ = UserProfile.objects.get_or_create(user=django_user)
    profile.keycloak_id = keycloak_user_id
    profile.stripe_customer_id = (
        session.customer
        if isinstance(session.customer, str)
        else session.customer.id
    )
    profile.stripe_subscription_id = (
        session.subscription
        if isinstance(session.subscription, str)
        else session.subscription.id
    )
    profile.subscription_status = "active"
    profile.save()

    return profile


def _handle_checkout_completed(session):
    """Handle checkout.session.completed event.

    Orchestrates the critical-path provisioning steps synchronously:
      1. Retrieve expanded Stripe session
      2. Resolve or create the Keycloak user
      3. Provision the Django User + UserProfile

    Non-critical follow-up work is dispatched to Celery:
      - Sending the Keycloak password-reset email
      - Syncing instance assignments and Keycloak attributes
    """
    from onboarding.tasks import (
        send_keycloak_password_reset_email,
        sync_user_post_checkout,
    )

    session = stripe.checkout.Session.retrieve(
        session["id"], expand=["subscription", "customer"]
    )

    if session.mode != "subscription":
        logger.info(
            "Webhook checkout.session.completed: skipping non-subscription session %s",
            session.id,
        )
        return

    metadata = session.metadata or {}
    first_name = metadata.get("first_name", "")
    last_name = metadata.get("last_name", "")
    username = metadata.get("username", "")

    email = _extract_session_email(session, metadata)
    if not email:
        logger.warning(
            "Webhook checkout.session.completed: no email for session %s",
            session.id,
        )
        return

    logger.info(
        "Webhook checkout.session.completed: session=%s email=%s username=%s",
        session.id,
        email,
        username,
    )

    # --- Critical path: user creation + profile setup ---
    try:
        keycloak_user_id, is_new_user = _get_or_create_keycloak_user(
            email, username, first_name, last_name
        )
    except RuntimeError:
        logger.error(
            "Webhook checkout.session.completed: Keycloak user creation failed for %s",
            email,
            exc_info=True,
        )
        return

    profile = _provision_django_user(
        email, username, first_name, last_name, keycloak_user_id, session
    )

    # Populate local subscription-item cache so the Celery task
    # (and all future get_subscribed_products calls) never hits Stripe.
    profile.update_subscription_items(
        _extract_subscription_items(session.subscription)
    )

    # --- Non-critical work: dispatch to Celery ---
    if is_new_user:
        send_keycloak_password_reset_email.delay(keycloak_user_id)

    sync_user_post_checkout.delay(profile.pk)

    logger.info(
        "Webhook checkout.session.completed: processed user=%s keycloak_id=%s",
        profile.user.username,
        profile.keycloak_id,
    )


def _handle_subscription_updated(subscription):
    """Handle customer.subscription.updated event."""
    from dashboard.models import UserProfile
    from skylantix_dash.keycloak import keycloak_admin

    subscription_id = subscription["id"]
    status = subscription["status"]

    # Find user profile by subscription ID
    try:
        profile = UserProfile.objects.get(stripe_subscription_id=subscription_id)
    except UserProfile.DoesNotExist:
        logger.warning(f"UserProfile not found for subscription {subscription_id}")
        return

    # Update subscription status
    profile.subscription_status = status
    profile.save(update_fields=["subscription_status"])

    # Refresh local subscription-item cache from the webhook payload.
    profile.update_subscription_items(_extract_subscription_items(subscription))

    # Sync instance assignments and Keycloak attributes.
    profile.sync_instance_assignments()
    profile.sync_to_keycloak()

    # Re-enable the Keycloak user if the subscription is back in good standing
    # (e.g., payment method updated after a failure).
    if profile.keycloak_id and status in ("active", "trialing"):
        if keycloak_admin.set_user_enabled(profile.keycloak_id, True):
            logger.info(
                "Re-enabled Keycloak user %s (subscription %s)",
                profile.keycloak_id,
                status,
            )

    logger.info(
        f"Updated subscription {subscription_id} for user {profile.user.username}"
    )


def _handle_subscription_deleted(subscription):
    """Handle customer.subscription.deleted event.

    Disables the Keycloak user and kills active sessions.  Entitlements and
    instance assignments are intentionally left intact so that re-enabling
    the user (e.g. after resubscribing) doesn't require re-provisioning.
    """
    from dashboard.models import UserProfile
    from skylantix_dash.keycloak import keycloak_admin

    subscription_id = subscription["id"]

    try:
        profile = UserProfile.objects.get(stripe_subscription_id=subscription_id)
    except UserProfile.DoesNotExist:
        logger.warning(f"UserProfile not found for subscription {subscription_id}")
        return

    profile.subscription_status = "canceled"
    profile.save(update_fields=["subscription_status"])

    # Clear the local subscription-item cache.
    profile.update_subscription_items([])

    # Disable user and terminate all active sessions.
    if profile.keycloak_id:
        keycloak_admin.set_user_enabled(profile.keycloak_id, False)
        keycloak_admin.logout_user_sessions(profile.keycloak_id)
        logger.info(
            "Disabled Keycloak user %s and cleared sessions (subscription canceled)",
            profile.keycloak_id,
        )

    # Notify the user by email.
    from onboarding.tasks import notify_subscription_canceled

    notify_subscription_canceled.delay(
        profile.user.email, profile.user.first_name
    )

    logger.info(
        f"Canceled subscription {subscription_id} for user {profile.user.username}"
    )


def _handle_payment_failed(invoice):
    """Handle invoice.payment_failed event.

    Disables the Keycloak user and kills active sessions until payment is
    resolved.  Entitlements and instance assignments are left intact.
    """
    from dashboard.models import UserProfile
    from skylantix_dash.keycloak import keycloak_admin

    subscription_id = invoice.get("subscription")
    if not subscription_id:
        return

    try:
        profile = UserProfile.objects.get(stripe_subscription_id=subscription_id)
    except UserProfile.DoesNotExist:
        logger.warning(f"UserProfile not found for subscription {subscription_id}")
        return

    profile.subscription_status = "past_due"
    profile.save(update_fields=["subscription_status"])

    # Disable user and terminate all active sessions.
    if profile.keycloak_id:
        keycloak_admin.set_user_enabled(profile.keycloak_id, False)
        keycloak_admin.logout_user_sessions(profile.keycloak_id)
        logger.info(
            "Disabled Keycloak user %s and cleared sessions (payment failed)",
            profile.keycloak_id,
        )

    # Notify the user by email.
    from onboarding.tasks import notify_payment_failed

    notify_payment_failed.delay(
        profile.user.email, profile.user.first_name
    )

    logger.info(
        f"Payment failed for subscription {subscription_id}, user {profile.user.username}"
    )
