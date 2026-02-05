from django.contrib.auth.models import Group

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

ADMIN_GROUP = 'Skylantix Admin'


class KeycloakOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """Custom OIDC backend that uses Keycloak's preferred_username."""

    def create_user(self, claims):
        user = super().create_user(claims)
        user.username = claims.get('preferred_username', claims.get('sub'))
        user.email = claims.get('email', '')
        user.first_name = claims.get('given_name', '')
        user.last_name = claims.get('family_name', '')
        self._sync_admin_status(user, claims)
        user.save()

        # Sync Keycloak groups to Django Groups for new users only
        self._sync_groups_from_keycloak(user, claims)

        self._ensure_profile(user, claims)
        return user

    def _sync_groups_from_keycloak(self, user, claims):
        """
        Sync Keycloak groups to Django Groups.
        Only called on user creation, not on subsequent logins.
        """
        from dashboard.models import Instance

        keycloak_groups = claims.get('groups', [])

        # Find Instances where any of their groups match user's Keycloak groups
        instances = Instance.objects.filter(
            groups__name__in=keycloak_groups,
            is_active=True
        ).prefetch_related('groups').distinct()

        # Add user to all groups associated with matching instances
        for instance in instances:
            for group in instance.groups.all():
                if group.name in keycloak_groups:
                    user.groups.add(group)

        # Sync Skylantix Admin group if user is in it on Keycloak
        if ADMIN_GROUP in keycloak_groups:
            admin_group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
            user.groups.add(admin_group)

    def filter_users_by_claims(self, claims):
        username = claims.get('preferred_username')
        if not username:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(username=username)

    def update_user(self, user, claims):
        user.email = claims.get('email', user.email)
        user.first_name = claims.get('given_name', user.first_name)
        user.last_name = claims.get('family_name', user.last_name)
        self._sync_admin_status(user, claims)
        user.save()

        self._ensure_profile(user, claims)
        return user

    def _sync_admin_status(self, user, claims):
        """Set is_staff/is_superuser based on Keycloak group membership."""
        groups = claims.get('groups', [])
        is_admin = ADMIN_GROUP in groups
        user.is_staff = is_admin
        user.is_superuser = is_admin

    def _ensure_profile(self, user, claims):
        """Ensure UserProfile exists and sync Keycloak attributes."""
        from dashboard.models import UserProfile

        profile, _ = UserProfile.objects.get_or_create(user=user)

        needs_save = False

        # Sync Keycloak ID
        keycloak_id = claims.get('sub', '')
        if keycloak_id and not profile.keycloak_id:
            profile.keycloak_id = keycloak_id
            needs_save = True

        # Save if any changes were made
        if needs_save:
            profile.save()

        # Sync instance assignments based on subscriptions
        if profile.stripe_subscription_id:
            profile.sync_instance_assignments()
