import logging

from django.conf import settings
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
    description = models.TextField(blank=True)
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

        Args:
            stripe_line_items: Iterable of Stripe SubscriptionItem objects or
                dicts.  Each must contain a ``price`` with an ``id``.
                Pass an empty list to clear all cached items.
        """
        # Extract (price_id, quantity) from various Stripe payload shapes.
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
            elif isinstance(price, str):
                price_id = price
            else:
                price_id = getattr(price, "id", None)

            if price_id:
                price_data.append((price_id, quantity))

        # Resolve price IDs → product IDs in a single query.
        all_price_ids = [p[0] for p in price_data]
        price_to_product = dict(
            ProductPrice.objects.filter(
                stripe_price_id__in=all_price_ids
            ).values_list("stripe_price_id", "product_id")
        )

        with transaction.atomic():
            self.subscription_items.all().delete()

            for price_id, quantity in price_data:
                product_id = price_to_product.get(price_id)
                if product_id:
                    # Import here to avoid circular ref at class-body level.
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
        from django.conf import settings as _settings

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
        Ensure user has an instance assigned for a product that requires one.
        Adds user to instance group and syncs to Keycloak.

        Args:
            product: The product to assign an instance for

        Returns:
            bool: True if assigned/updated, False if no change or no capacity
        """
        if not product.requires_instance:
            return False

        # Check if user is already in an instance group for this product
        user_group_ids = set(self.user.groups.values_list("id", flat=True))
        existing_instance = Instance.objects.filter(
            product=product, groups__id__in=user_group_ids, is_active=True
        ).first()

        if existing_instance:
            return False  # Already assigned

        # Allocate a new seat atomically
        with transaction.atomic():
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

            # Add user to all instance groups
            for group in instance.groups.all():
                self.user.groups.add(group)

                # Sync to Keycloak group
                if self.keycloak_id:
                    from skylantix_dash.keycloak import keycloak_admin

                    kc_group = keycloak_admin.get_group_by_name(group.name)
                    if kc_group:
                        keycloak_admin.add_user_to_group(
                            self.keycloak_id, kc_group["id"]
                        )
                        logger.info(
                            "Added user %s to Keycloak group %s",
                            self.user.username,
                            group.name,
                        )

            return True

    def remove_instance_access(self, product: Product) -> bool:
        """
        Remove user's instance access for a product.
        Decrements instance allocated_seats when user is removed from an instance.

        Args:
            product: The product to remove instance access for

        Returns:
            bool: True if something changed
        """
        changed = False

        user_group_ids = list(self.user.groups.values_list("id", flat=True))
        instances = (
            Instance.objects.filter(product=product, groups__id__in=user_group_ids)
            .prefetch_related("groups")
            .distinct()
        )

        for instance in instances:
            user_was_in_instance = False
            for group in instance.groups.all():
                if group in self.user.groups.all():
                    self.user.groups.remove(group)
                    changed = True
                    user_was_in_instance = True

                    if self.keycloak_id:
                        from skylantix_dash.keycloak import keycloak_admin

                        kc_group = keycloak_admin.get_group_by_name(group.name)
                        if kc_group:
                            keycloak_admin.remove_user_from_group(
                                self.keycloak_id, kc_group["id"]
                            )
                            logger.info(
                                "Removed user %s from Keycloak group %s",
                                self.user.username,
                                group.name,
                            )

            if user_was_in_instance:
                Instance.objects.filter(pk=instance.pk).update(
                    allocated_seats=Greatest(Value(0), F("allocated_seats") - 1)
                )

        return changed

    def sync_instance_assignments(self):
        """
        Sync instance assignments based on subscribed products.
        Assigns instances for new products, removes access for canceled ones.
        """
        subscribed_products = self.get_subscribed_products()

        # Assign instances for products that need them
        for product in subscribed_products.filter(requires_instance=True):
            self.ensure_instance_assignment(product)

        # Remove access for products no longer subscribed
        # Get all products user has instance access to
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
