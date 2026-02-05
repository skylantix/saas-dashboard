import logging

import stripe
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.utils.html import format_html

from .models import Instance, Product, ProductPrice, UserProfile

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


# Inline for ProductPrice in ProductAdmin
class ProductPriceInline(admin.TabularInline):
    model = ProductPrice
    extra = 1
    fields = ["stripe_price_id", "billing_period", "amount", "currency", "is_active"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "page",
        "display_order",
        "is_coming_soon",
        "is_featured",
        "is_active",
    ]
    list_filter = ["page", "is_coming_soon", "is_featured", "is_active", "is_addon"]
    list_editable = ["display_order", "is_coming_soon", "is_featured"]
    search_fields = ["name", "slug", "description"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["page", "display_order"]
    inlines = [ProductPriceInline]
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "icon")}),
        (
            "Display Settings",
            {
                "fields": (
                    "page",
                    "display_order",
                    "is_featured",
                    "is_coming_soon",
                    "tagline",
                    "footer_text",
                )
            },
        ),
        (
            "Features",
            {"fields": ("features",), "description": "Enter one feature per line"},
        ),
        (
            "Product Type",
            {"fields": ("parent", "is_addon", "requires_instance", "standalone_url")},
        ),
        ("Status", {"fields": ("is_active",)}),
    )


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = [
        "product",
        "stripe_price_id",
        "billing_period",
        "amount",
        "currency",
        "is_active",
    ]
    list_filter = ["product", "billing_period", "is_active"]
    search_fields = ["stripe_price_id", "product__name"]


# Enhance Group admin with search for autocomplete
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    search_fields = ["name"]
    ordering = ["name"]
    filter_horizontal = ["permissions"]


@admin.register(Instance)
class InstanceAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "product",
        "base_url",
        "display_groups",
        "allocated_seats",
        "soft_cap",
        "allocation_cap",
        "is_active",
    ]
    list_filter = ["is_active", "product"]
    search_fields = ["name", "base_url", "product__name", "groups__name"]
    readonly_fields = ["allocated_seats", "created_at"]
    filter_horizontal = ["groups"]
    fieldsets = (
        (None, {"fields": ("product", "name", "base_url")}),
        ("Access Control", {"fields": ("groups",)}),
        (
            "Capacity",
            {"fields": ("soft_cap", "allocation_cap", "hard_cap", "allocated_seats")},
        ),
        ("Status", {"fields": ("is_active", "created_at")}),
    )

    def display_groups(self, obj):
        return ", ".join(obj.groups.values_list("name", flat=True))

    display_groups.short_description = "Groups"


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin interface for UserProfile."""

    list_display = [
        "user_email",
        "user_username",
        "user_groups",
        "subscription_status",
        "products_display",
        "stripe_customer_link",
        "keycloak_id_short",
    ]

    list_filter = [
        "subscription_status",
    ]

    search_fields = [
        "user__username",
        "user__email",
        "stripe_customer_id",
        "stripe_subscription_id",
        "keycloak_id",
    ]

    readonly_fields = [
        "user",
        "stripe_customer_id",
        "stripe_subscription_id",
        "keycloak_id",
        "stripe_subscription_link",
        "products_display_readonly",
    ]

    fieldsets = (
        ("User", {"fields": ("user",)}),
        ("Keycloak", {"fields": ("keycloak_id",)}),
        (
            "Stripe",
            {
                "fields": (
                    "stripe_customer_id",
                    "stripe_subscription_id",
                    "stripe_subscription_link",
                    "subscription_status",
                )
            },
        ),
        ("Products", {"fields": ("products_display_readonly",)}),
    )

    actions = [
        "sync_to_keycloak",
        "sync_instance_assignments",
        "refresh_subscription_status",
    ]

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = "Email"
    user_email.admin_order_field = "user__email"

    def user_username(self, obj):
        return obj.user.username

    user_username.short_description = "Username"
    user_username.admin_order_field = "user__username"

    def user_groups(self, obj):
        groups = obj.user.groups.values_list("name", flat=True)
        if groups:
            return ", ".join(groups)
        return "-"

    user_groups.short_description = "Groups"

    def products_display(self, obj):
        products = obj.get_subscribed_products()
        if not products:
            return format_html('<span style="color: #999;">No products</span>')
        return ", ".join(p.name for p in products)

    products_display.short_description = "Products"

    def products_display_readonly(self, obj):
        return self.products_display(obj)

    products_display_readonly.short_description = "Subscribed Products"

    def stripe_customer_link(self, obj):
        if not obj.stripe_customer_id:
            return "-"
        url = f"https://dashboard.stripe.com/customers/{obj.stripe_customer_id}"
        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            url,
            obj.stripe_customer_id[:20] + "...",
        )

    stripe_customer_link.short_description = "Stripe Customer"

    def stripe_subscription_link(self, obj):
        if not obj.stripe_subscription_id:
            return "-"
        url = f"https://dashboard.stripe.com/subscriptions/{obj.stripe_subscription_id}"
        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            url,
            obj.stripe_subscription_id[:20] + "...",
        )

    stripe_subscription_link.short_description = "Stripe Subscription"

    def keycloak_id_short(self, obj):
        if not obj.keycloak_id:
            return "-"
        return (
            obj.keycloak_id[:20] + "..."
            if len(obj.keycloak_id) > 20
            else obj.keycloak_id
        )

    keycloak_id_short.short_description = "Keycloak ID"

    def sync_to_keycloak(self, request, queryset):
        """Admin action to sync user data to Keycloak."""
        synced_count = 0
        failed_count = 0

        for profile in queryset:
            if profile.sync_to_keycloak():
                synced_count += 1
            else:
                failed_count += 1

        if synced_count > 0:
            self.message_user(
                request, f"Synced {synced_count} user(s) to Keycloak.", messages.SUCCESS
            )
        if failed_count > 0:
            self.message_user(
                request, f"Failed to sync {failed_count} user(s).", messages.WARNING
            )

    sync_to_keycloak.short_description = "Sync to Keycloak"

    def sync_instance_assignments(self, request, queryset):
        """Admin action to sync instance assignments."""
        for profile in queryset:
            profile.sync_instance_assignments()
        self.message_user(
            request,
            f"Synced instance assignments for {queryset.count()} user(s).",
            messages.SUCCESS,
        )

    sync_instance_assignments.short_description = "Sync instance assignments"

    def refresh_subscription_status(self, request, queryset):
        """Admin action to refresh subscription status from Stripe."""
        updated_count = 0
        failed_count = 0

        for profile in queryset:
            if not profile.stripe_subscription_id:
                continue

            try:
                subscription = stripe.Subscription.retrieve(
                    profile.stripe_subscription_id
                )
                profile.subscription_status = subscription.status
                profile.save(update_fields=["subscription_status"])
                updated_count += 1
            except stripe.error.StripeError as e:
                logger.error(
                    "Error refreshing subscription status for %s: %s",
                    profile.user.username,
                    e,
                )
                failed_count += 1

        if updated_count > 0:
            self.message_user(
                request,
                f"Refreshed status for {updated_count} user(s).",
                messages.SUCCESS,
            )
        if failed_count > 0:
            self.message_user(
                request, f"Failed to refresh {failed_count} user(s).", messages.WARNING
            )

    refresh_subscription_status.short_description = (
        "Refresh subscription status from Stripe"
    )


# Add UserProfile as inline to User admin
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    readonly_fields = [
        "keycloak_id",
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
    ]


# Unregister default User admin and register with inline
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return []
        return super().get_inline_instances(request, obj)
