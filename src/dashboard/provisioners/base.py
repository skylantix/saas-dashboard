import logging

logger = logging.getLogger(__name__)


class BaseProvisioner:
    """Base class for product-specific user provisioning.

    Each product type (Nextcloud, Immich, Bitwarden, etc.) can have a
    different strategy for creating and removing user accounts on the
    underlying service.  Subclasses implement the service-specific logic
    while the generic instance-selection and capacity management stays in
    :class:`~dashboard.models.UserProfile`.

    Args:
        product: The :class:`~dashboard.models.Product` being provisioned.
        instance: The :class:`~dashboard.models.Instance` assigned to the
            user, or ``None`` for standalone products.
    """

    def __init__(self, product, instance=None):
        self.product = product
        self.instance = instance

    def provision_user(self, profile) -> bool:
        """Create or enable the user's account on the service.

        Called after instance selection and seat allocation (if applicable).

        Args:
            profile: The :class:`~dashboard.models.UserProfile` to provision.

        Returns:
            ``True`` if the user was successfully provisioned.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement provision_user()"
        )

    def deprovision_user(self, profile) -> bool:
        """Remove or disable the user's account on the service.

        Called before seat deallocation (if applicable).

        Args:
            profile: The :class:`~dashboard.models.UserProfile` to deprovision.

        Returns:
            ``True`` if the user was successfully deprovisioned.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement deprovision_user()"
        )
