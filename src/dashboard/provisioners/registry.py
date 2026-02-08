# Maps product slugs to their provisioner backend dotted paths.
# This is the single source of truth for which provisioner each product uses.
# Adding a new product's provisioner means adding one line here.

PRODUCT_PROVISIONERS = {
    "nextcloud": "dashboard.provisioners.group_based.GroupBasedProvisioner",
    "dedicated": "dashboard.provisioners.group_based.GroupBasedProvisioner",
    "managed": "dashboard.provisioners.group_based.GroupBasedProvisioner",
    "bitwarden": "dashboard.provisioners.standalone.StandaloneProvisioner",
    "immich": "dashboard.provisioners.standalone.StandaloneProvisioner",
    "extra_storage": "dashboard.provisioners.standalone.StandaloneProvisioner",
}

DEFAULT_PROVISIONER = "dashboard.provisioners.standalone.StandaloneProvisioner"
