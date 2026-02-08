import logging

from .base import BaseProvisioner

logger = logging.getLogger(__name__)


class GroupBasedProvisioner(BaseProvisioner):
    """Provisioner for services that use Keycloak group membership for access.

    Used by products like Nextcloud where adding the user to the correct
    Keycloak group is sufficient -- the service itself accepts any
    authenticated user that belongs to the right group.
    """

    def provision_user(self, profile) -> bool:
        """Add the user to the instance's Django and Keycloak groups."""
        if not self.instance:
            logger.warning(
                "GroupBasedProvisioner.provision_user called without an instance "
                "for product %s, user %s",
                self.product.name,
                profile.user.username,
            )
            return False

        for group in self.instance.groups.all():
            profile.user.groups.add(group)

            if profile.keycloak_id:
                from skylantix_dash.keycloak import keycloak_admin

                kc_group = keycloak_admin.get_group_by_name(group.name)
                if kc_group:
                    keycloak_admin.add_user_to_group(
                        profile.keycloak_id, kc_group["id"]
                    )
                    logger.info(
                        "Added user %s to Keycloak group %s",
                        profile.user.username,
                        group.name,
                    )

        return True

    def deprovision_user(self, profile) -> bool:
        """Remove the user from the instance's Django and Keycloak groups."""
        if not self.instance:
            logger.warning(
                "GroupBasedProvisioner.deprovision_user called without an "
                "instance for product %s, user %s",
                self.product.name,
                profile.user.username,
            )
            return False

        changed = False

        for group in self.instance.groups.all():
            if group in profile.user.groups.all():
                profile.user.groups.remove(group)
                changed = True

                if profile.keycloak_id:
                    from skylantix_dash.keycloak import keycloak_admin

                    kc_group = keycloak_admin.get_group_by_name(group.name)
                    if kc_group:
                        keycloak_admin.remove_user_from_group(
                            profile.keycloak_id, kc_group["id"]
                        )
                        logger.info(
                            "Removed user %s from Keycloak group %s",
                            profile.user.username,
                            group.name,
                        )

        return changed
