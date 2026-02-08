import logging

from django.conf import settings  # pyright: ignore[reportMissingImports]
from django.contrib.auth.models import Group
from django.db import models, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest

logger = logging.getLogger(__name__)


ADMIN_GROUP_NAME = "Skylantix Admin"


class Product(models.Model):
    """
    A service type (e.g., Nextcloud, Bitwarden, Immich).

    Products can have sub-products (add-ons) via the parent field.
    """

    PAGE_CHOICES = [
        ("plan", "Plan Selection"),
        ("addon", "General Add-ons"),
        ("storage", "Storage Add-ons"),
    ]

    name = models.CharField(max_length=128, unique=True)
    slug = models.SlugField(
        max_length=128,
        unique=True,
        help_text='Used for entitlements (e.g., "nextcloud")',
    )
    stripe_product_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text='Stripe product ID (e.g., "prod_xxx"). Used to validate webhook payloads.',
    )
    description = models.TextField(blank=True)
    dashboard_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text='Display name on the user dashboard (falls back to name if blank)',
    )
    dashboard_description = models.TextField(
        blank=True,
        default="",
        help_text="Description shown on the user dashboard (falls back to description if blank)",
    )
    icon = models.CharField(
        max_length=64, blank=True, help_text="Icon identifier for frontend"
    )
    is_active = models.BooleanField(default=True)

    # Optional parent for sub-products (add-ons like "Extra Storage")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="addons"
    )
    is_addon = models.BooleanField(
        default=False, help_text="True for add-ons like Extra Storage"
    )
    requires_instance = models.BooleanField(
        default=True, help_text="False for standalone services like Bitwarden"
    )

    # Optional standalone URL for products without instances
    standalone_url = models.URLField(
        blank=True,
        default="",
        help_text="URL for products without instances (e.g., Bitwarden vault)",
    )

    # Display settings for onboarding pages
    page = models.CharField(
        max_length=20,
        choices=PAGE_CHOICES,
        default="addon",
        help_text="Which onboarding page this product appears on",
    )
    display_order = models.PositiveIntegerField(
        default=0, help_text="Order on the page (lower = first)"
    )
    is_coming_soon = models.BooleanField(
        default=False, help_text="Show as coming soon (no Stripe price required)"
    )
    is_featured = models.BooleanField(
        default=False, help_text="Highlight this product on the page"
    )
    tagline = models.CharField(
        max_length=128,
        blank=True,
        help_text='Short tagline (e.g., "For a single user")',
    )
    features = models.TextField(blank=True, help_text="Features list, one per line")
    footer_text = models.CharField(
        max_length=256,
        blank=True,
        help_text='Text shown above the button (e.g., "Like Google Drive, but privacy-first")',
    )

    objects: models.Manager["Product"]

    class Meta:
        ordering = ["page", "display_order", "name"]

    def __str__(self):
        if self.parent:
            return f"{self.name} (addon for {self.parent.name})"
        return self.name

    @property
    def features_list(self):
        """Return features as a list (split by newlines)."""
        if not self.features:
            return []
        return [f.strip() for f in self.features.split("\n") if f.strip()]

    @property
    def monthly_price(self):
        """Get monthly price amount."""
        price = self.prices.filter(billing_period="monthly", is_active=True).first()
        return price.amount if price else None

    @property
    def annual_price(self):
        """Get annual price amount."""
        price = self.prices.filter(billing_period="annual", is_active=True).first()
        return price.amount if price else None

    def get_provisioner(self, instance=None):
        """Load and return the provisioner backend for this product.

        Looks up the provisioner class from the registry in
        :mod:`dashboard.provisioners.registry` using the product's slug.

        Args:
            instance: Optional :class:`Instance` to pass to the provisioner.

        Returns:
            A :class:`~dashboard.provisioners.base.BaseProvisioner` instance.
        """
        from django.utils.module_loading import import_string

        from dashboard.provisioners.registry import (
            DEFAULT_PROVISIONER,
            PRODUCT_PROVISIONERS,
        )

        backend_path = PRODUCT_PROVISIONERS.get(self.slug, DEFAULT_PROVISIONER)
        klass = import_string(backend_path)
        return klass(product=self, instance=instance)


class ProductPrice(models.Model):
    """
    Stripe price IDs for products.
    Maps Stripe subscriptions to products.
    """

    BILLING_PERIOD_CHOICES = [
        ("monthly", "Monthly"),
        ("annual", "Annual"),
    ]

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="prices"
    )
    stripe_price_id = models.CharField(max_length=255, unique=True)
    billing_period = models.CharField(max_length=20, choices=BILLING_PERIOD_CHOICES)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Price for display purposes"
    )
    currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)

    objects: models.Manager["ProductPrice"]

    class Meta:
        ordering = ["product", "billing_period"]

    def __str__(self):
        return f"{self.product.name} - {self.billing_period} (${self.amount})"


class Instance(models.Model):
    """
    A deployment of a product (e.g., Foggy, Cirrus for Nextcloud).

    Instances have capacity limits and group-based access control.
    """

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="instances"
    )
    name = models.CharField(max_length=128)
    base_url = models.URLField()
    groups = models.ManyToManyField(
        Group,
        related_name="instances",
        help_text="Groups that grant access to this instance",
    )

    # Capacity management
    soft_cap = models.PositiveIntegerField(
        default=70, help_text="Preferred max active seats"
    )
    allocation_cap = models.PositiveIntegerField(
        default=90, help_text="Max seats to allocate"
    )
    hard_cap = models.PositiveIntegerField(
        default=100, help_text="Absolute maximum capacity"
    )
    allocated_seats = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    auto_allocate = models.BooleanField(
        default=True,
        help_text="Include in automatic seat allocation. Disable for beta/testing instances.",
    )
    api_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="API key for provisioner backends that need one (URL comes from base_url above)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    objects: models.Manager["Instance"]

    class Meta:
        ordering = ["product", "allocated_seats", "name"]
        unique_together = ["product", "name"]

    def __str__(self):
        return f"{self.product.name}: {self.name} ({self.allocated_seats}/{self.allocation_cap})"

    def user_has_access(self, user):
        """Check if user has access to this instance (via groups or admin)."""
        user_group_names = set(user.groups.values_list("name", flat=True))
        instance_group_names = set(self.groups.values_list("name", flat=True))
        return (
            bool(user_group_names & instance_group_names)
            or ADMIN_GROUP_NAME in user_group_names
        )

    def get_group_names(self):
        """Return list of group names for Keycloak sync."""
        return list(self.groups.values_list("name", flat=True))


class UserProfile(models.Model):
    """User subscription and profile data."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )

    # Keycloak
    keycloak_id = models.CharField(max_length=255, blank=True, default="")

    # Stripe
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    subscription_status = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="active, canceled, past_due, etc.",
    )

    objects: models.Manager["UserProfile"]

    def __str__(self):
        return f"{self.user.username} profile"

    @property
    def is_active_subscriber(self):
        return self.subscription_status == "active"

    def get_subscribed_products(self):
        """Get products the user is subscribed to from the local cache.

        The cache is populated by webhook handlers via update_subscription_items().

        Returns:
            QuerySet[Product]: Products the user has access to
        """
        if self.subscription_status not in ("active", "trialing", "past_due"):
            return Product.objects.none()

        return Product.objects.filter(
            subscription_items__profile=self, is_active=True
        ).distinct()

    def update_subscription_items(self, stripe_line_items):
        """Replace the local subscription-item cache from Stripe line items.

        Called by webhook handlers which already have the subscription data,
        avoiding an extra Stripe API round-trip.

        Validates that the Stripe product ID on each line item matches the
        ``stripe_product_id`` configured on the Django Product (if set).

        Args:
            stripe_line_items: Iterable of Stripe SubscriptionItem objects or
                dicts.  Each must contain a ``price`` with an ``id``.
                Pass an empty list to clear all cached items.
        """
        # Extract (price_id, stripe_product_id, quantity) from various
        # Stripe payload shapes.
        price_data = []
        for item in stripe_line_items:
            if isinstance(item, dict):
                price = item.get("price")
                quantity = item.get("quantity", 1)
            else:
                price = getattr(item, "price", None)
                quantity = getattr(item, "quantity", 1)

            if price is None:
                continue

            if isinstance(price, dict):
                price_id = price.get("id")
                stripe_product_id = price.get("product", "")
            elif isinstance(price, str):
                price_id = price
                stripe_product_id = ""
            else:
                price_id = getattr(price, "id", None)
                stripe_product_id = getattr(price, "product", "")

            if price_id:
                price_data.append((price_id, stripe_product_id, quantity))

        # Resolve price IDs → Products in a single query.
        all_price_ids = [p[0] for p in price_data]
        price_to_product = dict(
            ProductPrice.objects.filter(
                stripe_price_id__in=all_price_ids
            ).select_related("product").values_list(
                "stripe_price_id", "product_id"
            )
        )

        # Build a product_id → stripe_product_id lookup for validation.
        product_ids = set(price_to_product.values())
        product_stripe_ids = dict(
            Product.objects.filter(
                pk__in=product_ids
            ).exclude(
                stripe_product_id=""
            ).values_list("pk", "stripe_product_id")
        )

        with transaction.atomic():
            self.subscription_items.all().delete()

            for price_id, stripe_prod_id, quantity in price_data:
                product_id = price_to_product.get(price_id)
                if product_id:
                    # Validate: if the Django Product has a stripe_product_id
                    # configured, check it matches what Stripe sent.
                    expected = product_stripe_ids.get(product_id)
                    if expected and stripe_prod_id and expected != stripe_prod_id:
                        logger.warning(
                            "Stripe product mismatch for price %s (user %s): "
                            "expected %s, got %s",
                            price_id,
                            self.user.username,
                            expected,
                            stripe_prod_id,
                        )

                    UserSubscriptionItem.objects.create(
                        profile=self,
                        product_id=product_id,
                        stripe_price_id=price_id,
                        quantity=quantity,
                    )
                else:
                    logger.warning(
                        "No product found for Stripe price %s (user %s)",
                        price_id,
                        self.user.username,
                    )

    def refresh_subscription_items_from_stripe(self):
        """Fetch current subscription items from the Stripe API and update
        the local cache.

        Useful for back-filling existing profiles or as a manual recovery
        tool.  Normal operation should rely on webhook-driven updates.
        """
        if not self.stripe_subscription_id:
            self.subscription_items.all().delete()
            return

        import stripe as _stripe
        from django.conf import settings as _settings  # pyright: ignore[reportMissingImports]

        try:
            _stripe.api_key = _settings.STRIPE_SECRET_KEY
            subscription = _stripe.Subscription.retrieve(
                self.stripe_subscription_id, expand=["items.data.price"]
            )
            self.subscription_status = subscription.status
            self.save(update_fields=["subscription_status"])
            self.update_subscription_items(subscription["items"]["data"])
        except Exception as e:
            logger.error(
                "Error refreshing subscription items from Stripe for %s: %s",
                self.user.username,
                e,
            )

    def get_product_slugs(self):
        """Get list of product slugs user is subscribed to."""
        return list(self.get_subscribed_products().values_list("slug", flat=True))

    def has_product(self, product_slug):
        """Check if user has a specific product."""
        return self.get_subscribed_products().filter(slug=product_slug).exists()

    def sync_to_keycloak(self):
        """
        Sync user's product access to Keycloak attributes.

        Returns:
            bool: True if sync was successful
        """
        if not self.keycloak_id:
            logger.warning(
                "No Keycloak ID for user %s, cannot sync", self.user.username
            )
            return False

        from skylantix_dash.keycloak import keycloak_admin

        try:
            products = self.get_subscribed_products()

            # Build attributes based on subscribed products
            attributes = {}
            for product in products:
                attributes[f"has_{product.slug}"] = "true"

            # Find user's assigned instance for products that require one
            user_group_ids = set(self.user.groups.values_list("id", flat=True))
            for product in products.filter(requires_instance=True):
                instance = Instance.objects.filter(
                    product=product, groups__id__in=user_group_ids, is_active=True
                ).first()
                if instance:
                    attributes[f"{product.slug}_instance"] = instance.base_url

            success = keycloak_admin.update_user_attributes(
                self.keycloak_id, attributes
            )

            if success:
                logger.info(
                    "Synced products to Keycloak for user %s", self.user.username
                )
            else:
                logger.error(
                    "Failed to sync products to Keycloak for user %s",
                    self.user.username,
                )

            return success

        except Exception as e:
            logger.exception(
                "Error syncing to Keycloak for user %s: %s", self.user.username, e
            )
            return False

    def ensure_instance_assignment(self, product: Product) -> bool:
        """
        Ensure user is provisioned for a product.

        For products that require an instance, this selects an instance with
        available capacity, increments its seat count, and delegates to the
        product's provisioner backend to set up user access.

        For standalone products, the provisioner is called directly without
        instance assignment.

        The entire check-then-allocate sequence runs inside a single atomic
        block with a ``SELECT FOR UPDATE`` on the profile row.  This
        serializes concurrent webhook calls for the same user and prevents
        double-allocation.

        Args:
            product: The product to provision the user for

        Returns:
            bool: True if provisioned, False if no change or no capacity
        """
        if not product.requires_instance:
            # Standalone product -- delegate directly to provisioner
            provisioner = product.get_provisioner()
            return provisioner.provision_user(self)

        with transaction.atomic():
            # Lock the profile row to serialize per-user assignments.
            # This prevents two concurrent webhooks from both passing the
            # "already assigned" check and double-allocating.
            UserProfile.objects.select_for_update().get(pk=self.pk)

            # Check if user is already in an instance group for this product
            user_group_ids = set(self.user.groups.values_list("id", flat=True))
            existing_instance = Instance.objects.filter(
                product=product, groups__id__in=user_group_ids, is_active=True
            ).first()

            if existing_instance:
                return False  # Already assigned

            instance = (
                Instance.objects.select_for_update(skip_locked=True)
                .filter(
                    product=product,
                    is_active=True,
                    auto_allocate=True,
                    allocated_seats__lt=F("allocation_cap"),
                )
                .prefetch_related("groups")
                .order_by("allocated_seats", "name")
                .first()
            )
            if not instance:
                logger.warning(
                    "No %s capacity available for new assignment", product.name
                )
                return False

            # Update seat count
            Instance.objects.filter(pk=instance.pk).update(
                allocated_seats=F("allocated_seats") + 1
            )

            # Delegate to the product's provisioner backend
            provisioner = product.get_provisioner(instance=instance)
            provisioner.provision_user(self)

            return True

    def remove_instance_access(self, product: Product) -> bool:
        """
        Remove user's access for a product.

        For instance-based products, delegates to the provisioner backend
        for each instance the user is assigned to and decrements the seat
        count.  For standalone products, calls the provisioner directly.

        Args:
            product: The product to remove access for

        Returns:
            bool: True if something changed
        """
        if not product.requires_instance:
            provisioner = product.get_provisioner()
            return provisioner.deprovision_user(self)

        changed = False

        user_group_ids = list(self.user.groups.values_list("id", flat=True))
        instances = (
            Instance.objects.filter(product=product, groups__id__in=user_group_ids)
            .prefetch_related("groups")
            .distinct()
        )

        for instance in instances:
            provisioner = product.get_provisioner(instance=instance)
            if provisioner.deprovision_user(self):
                changed = True
                Instance.objects.filter(pk=instance.pk).update(
                    allocated_seats=Greatest(Value(0), F("allocated_seats") - 1)
                )

        return changed

    def sync_instance_assignments(self):
        """
        Sync product access based on subscribed products.

        Provisions all subscribed products (both instance-based and
        standalone) via their provisioner backends, and removes access
        for products no longer subscribed.
        """
        subscribed_products = self.get_subscribed_products()

        # Provision all subscribed products via their backends
        for product in subscribed_products:
            self.ensure_instance_assignment(product)

        # Remove access for instance-based products no longer subscribed
        user_group_ids = list(self.user.groups.values_list("id", flat=True))
        accessed_products = Product.objects.filter(
            instances__groups__id__in=user_group_ids, requires_instance=True
        ).distinct()

        for product in accessed_products:
            if product not in subscribed_products:
                self.remove_instance_access(product)


class UserSubscriptionItem(models.Model):
    """Local cache of a Stripe subscription line item.

    Updated by webhook handlers so that get_subscribed_products() never
    needs to hit the Stripe API at request time.
    """

    profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="subscription_items"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="subscription_items"
    )
    stripe_price_id = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)

    objects: models.Manager["UserSubscriptionItem"]

    class Meta:
        unique_together = ["profile", "stripe_price_id"]

    def __str__(self):
        return (
            f"{self.profile.user.username} \u2192 {self.product.name}"
            f" ({self.stripe_price_id})"
        )
