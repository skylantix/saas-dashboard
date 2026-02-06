# Skylantix Dashboard

A Django backend service for **user onboarding, subscription management, and access control**. It acts as the central orchestrator between external systems — delegating identity to **Keycloak**, billing to **Stripe**, email to **Mailgun**, and provisioning product instances while keeping its own database for subscription state and instance assignments.

## Core Flows

**New User Onboarding**: A multi-step signup flow where users select products, choose a plan, pick add-ons, and complete payment via Stripe. On successful checkout, a Keycloak account is created, product instances are assigned, and entitlements are synced.

**Existing User Dashboard**: Authenticated users log in via Keycloak SSO and see a dashboard linking to their provisioned products. They can manage their subscription, reset their password, and view account details.

**Account Recovery**: Users who lost access (e.g., after a cancelled subscription) can verify their identity via email code and re-subscribe to restore their account before a final expiry date.

## Architecture

```
src/
├── skylantix_dash/          # Django project config, auth, Keycloak client
│   ├── settings.py          # Configuration (DB, OIDC, Stripe, Celery, etc.)
│   ├── auth.py              # Custom Keycloak OIDC authentication backend
│   ├── keycloak.py          # Keycloak Admin API client
│   ├── celery.py            # Celery app configuration
│   ├── urls.py              # Root URL routing
│   └── templates/partials/  # Shared template partials
├── dashboard/               # Core app: models, user profile, instance assignment
│   ├── models.py            # Product, ProductPrice, Instance, UserProfile, UserSubscriptionItem
│   ├── views.py             # Dashboard views, password reset
│   ├── admin.py             # Django admin with custom actions
│   └── entitlements.py      # Stripe price → entitlement mapping
└── onboarding/              # Multi-step signup, Stripe checkout, webhooks
    ├── views.py             # Onboarding steps, webhook handler, recovery
    ├── tasks.py             # Celery tasks (email, sync, provisioning)
    ├── urls.py              # Onboarding URL patterns
    └── urls_recovery.py     # Account recovery URL patterns
```

## Data Model

| Model | Purpose |
|---|---|
| `Product` | Service definitions (Nextcloud, Bitwarden, etc.) with display config, pricing page assignment, and parent/add-on relationships |
| `ProductPrice` | Maps Stripe price IDs to products with billing period and amount |
| `Instance` | Product deployments with capacity limits (`soft_cap`, `allocation_cap`, `hard_cap`) and seat tracking |
| `UserProfile` | Extends Django User with Keycloak ID, Stripe customer/subscription IDs, and subscription status |
| `UserSubscriptionItem` | Local cache of Stripe subscription line items to avoid API calls at request time |

## Data Flow

```
Stripe Webhooks ──► Django ──► Updates UserProfile & subscription items
                       │
                       ├──► Keycloak: Syncs entitlements as user attributes
                       │                Manages groups, password resets
                       │
                       └──► Instances: Assigns users to product instances
                                       (e.g., Nextcloud) with capacity-aware allocation
```

### Instance Assignment

Users are assigned to product instances using a capacity-aware allocation strategy:

- Uses `SELECT FOR UPDATE` row locking to prevent race conditions
- Assigns to the instance with the lowest `allocated_seats` count
- Respects capacity thresholds: `soft_cap` < `allocation_cap` < `hard_cap`
- Syncs Django groups to corresponding Keycloak groups for access control

### Stripe Webhook Events

| Event | Action |
|---|---|
| `checkout.session.completed` | Creates Keycloak user, provisions account, dispatches async sync tasks |
| `customer.subscription.updated` | Updates subscription status, syncs line items, re-syncs instance assignments |
| `customer.subscription.deleted` | Disables Keycloak user, clears sessions, sends cancellation email |
| `invoice.payment_failed` | Disables Keycloak user, sends payment failure notification |

## URL Routes

### Pages

| Route | Description |
|---|---|
| `/` | Redirects to dashboard or OIDC login |
| `/dashboard/` | Main dashboard (login required) |
| `/onboarding/` | Step 1: Welcome, collect first name |
| `/onboarding/plan/` | Step 2: Plan selection + general add-ons |
| `/onboarding/addons/` | Step 3: Storage add-ons |
| `/onboarding/checkout/` | Step 4: Account info + payment |
| `/onboarding/success/` | Post-payment success page |
| `/onboarding/cancel/` | Cancelled checkout page |
| `/onboarding/waitlist/` | Public waitlist signup |
| `/recovery/` | Account recovery page |

### JSON APIs

| Method | Route | Description |
|---|---|---|
| `POST` | `/onboarding/checkout/validate/` | Username/email availability check |
| `POST` | `/onboarding/checkout/session/` | Create Stripe checkout session |
| `POST` | `/onboarding/checkout/send-code/` | Send email verification code |
| `POST` | `/onboarding/checkout/verify-code/` | Verify email code |
| `POST` | `/onboarding/success/resend-password/` | Resend password setup email |
| `POST` | `/onboarding/waitlist/submit/` | Waitlist signup submission |
| `POST` | `/onboarding/webhook/` | Stripe webhook endpoint (CSRF-exempt) |
| `POST` | `/dashboard/reset-password/` | Request Keycloak password reset |
| `POST` | `/recovery/send-code/` | Send account recovery code |
| `POST` | `/recovery/verify-code/` | Verify recovery code, re-enable account |
| `POST` | `/recovery/checkout-session/` | Create re-subscription checkout |

### Monitoring

| Method | Route | Description |
|---|---|---|
| `GET` | `/health/` | Health check (200 OK / 503) |
| `GET` | `/metrics` | Prometheus metrics (Bearer token auth) |

## Celery Tasks

| Task | Description |
|---|---|
| `send_keycloak_password_reset_email` | Sends password reset email via Keycloak Admin API |
| `sync_user_post_checkout` | Assigns instances and syncs Keycloak attributes after checkout |
| `notify_subscription_canceled` | Sends cancellation email via Mailgun |
| `notify_payment_failed` | Sends payment failure email via Mailgun |

All tasks use automatic retries (3 attempts, 30s delay, exponential backoff).

## Tech Stack

- **Framework**: Django 5.2 / Python 3.13
- **Database**: PostgreSQL 18
- **Auth**: mozilla-django-oidc + Keycloak
- **Payments**: Stripe SDK
- **Task Queue**: Celery + Redis
- **Monitoring**: django-prometheus
- **Static Files**: WhiteNoise
- **WSGI Server**: Gunicorn
- **Email**: Mailgun API

## Development

### Prerequisites

- Docker and Docker Compose
- A `.env` file (see [Environment Variables](#environment-variables))

### Quick Start

```bash
# Start all services (Django, Celery, PostgreSQL, Redis)
docker compose up --build
```

The app will be available at `http://localhost:8000`.

### Running Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Apply migrations
python src/manage.py migrate

# Create a superuser
python src/manage.py createsuperuser

# Start the dev server
python src/manage.py runserver
```

### Management Commands

```bash
python src/manage.py migrate          # Apply database migrations
python src/manage.py makemigrations   # Create new migrations
python src/manage.py createsuperuser  # Create admin user
python src/manage.py shell            # Django interactive shell
python src/manage.py test             # Run tests
python src/manage.py test dashboard   # Run tests for a single app
python src/manage.py test onboarding  # Run tests for a single app
```

## Docker Services

| Service | Image | Purpose |
|---|---|---|
| `skylantix_dash` | Custom (Dockerfile) | Django app served by Gunicorn (3 workers) |
| `celery_worker` | Same as above | Celery worker for async tasks |
| `postgres` | `postgres:18-alpine` | PostgreSQL database |
| `redis` | `redis:8-alpine` | Celery message broker and result backend |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Django secret key |
| `DEBUG` | No | Debug mode (default: `False`) |
| `POSTGRES_DB` | Yes | Database name |
| `POSTGRES_USER` | Yes | Database user |
| `POSTGRES_PASSWORD` | Yes | Database password |
| `POSTGRES_HOST` | Yes | Database host |
| `KEYCLOAK_SERVER_URL` | Yes | Keycloak server base URL |
| `KEYCLOAK_REALM` | Yes | Keycloak realm name |
| `OIDC_RP_CLIENT_ID` | Yes | OIDC client ID |
| `OIDC_RP_CLIENT_SECRET` | Yes | OIDC client secret |
| `KEYCLOAK_ADMIN_CLIENT_ID` | Yes | Keycloak admin API client ID |
| `KEYCLOAK_ADMIN_CLIENT_SECRET` | Yes | Keycloak admin API client secret |
| `STRIPE_SECRET_KEY` | Yes | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | Yes | Stripe publishable key |
| `STRIPE_WEBHOOK_SECRET` | Yes | Stripe webhook signing secret |
| `MAILGUN_API_KEY` | Yes | Mailgun API key |
| `MAILGUN_DOMAIN` | No | Mailgun domain |
| `MAILGUN_WAITLIST_ADDRESS` | No | Waitlist email |
| `CELERY_BROKER_URL` | No | Redis broker URL (default: `redis://redis:6379/0`) |
| `CELERY_RESULT_BACKEND` | No | Redis result backend (default: `redis://redis:6379/0`) |
| `PROMETHEUS_METRICS_API_KEY` | Yes | Bearer token for `/metrics` endpoint |
| `TAILSCALE_IP` | No | IP address for Docker port binding |

## License

This project's source code is licensed under the [GNU Affero General Public License v3.0](LICENSE).

**Trademark Notice:** The "Skylantix" name, logos, and associated branding assets (including but not limited to graphic files in `src/dashboard/static/`) are the exclusive property of Skylantix and are **not** licensed under the AGPL-3.0. No permission is granted to use, reproduce, or distribute these trademarks or branding materials without prior written consent from Skylantix. Any fork or derivative work must remove or replace all Skylantix trademarks and branding before distribution.
