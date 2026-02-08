import logging

from .base import BaseProvisioner

logger = logging.getLogger(__name__)


class StandaloneProvisioner(BaseProvisioner):
    """Provisioner for standalone services that need no per-instance setup.

    Used by products like Bitwarden where Keycloak attributes (synced by
    :meth:`~dashboard.models.UserProfile.sync_to_keycloak`) are sufficient
    for access control.  Both ``provision_user`` and ``deprovision_user``
    are no-ops.
    """

    def provision_user(self, profile) -> bool:
        logger.info(
            "StandaloneProvisioner: no-op provision for user %s, product %s",
            profile.user.username,
            self.product.name,
        )
        return True

    def deprovision_user(self, profile) -> bool:
        logger.info(
            "StandaloneProvisioner: no-op deprovision for user %s, product %s",
            profile.user.username,
            self.product.name,
        )
        return True
