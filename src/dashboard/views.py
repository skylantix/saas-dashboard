from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def home(request):
    """Redirect to dashboard if logged in, otherwise to OIDC login."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('oidc_authentication_init')


@login_required
def dashboard(request):
    """Main dashboard view for authenticated users."""
    from dashboard.models import ADMIN_GROUP_NAME, Instance, Product, UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Get user's groups
    user_group_ids = set(request.user.groups.values_list('id', flat=True))
    user_group_names = set(request.user.groups.values_list('name', flat=True))

    # Check if user is admin (gets access to all instances)
    is_admin = ADMIN_GROUP_NAME in user_group_names

    # Get products user is subscribed to
    subscribed_products = profile.get_subscribed_products()
    subscribed_product_ids = set(subscribed_products.values_list('id', flat=True))

    # Build service cards
    services = []

    # For products that require instances, find accessible instances
    if is_admin:
        # Admins see all active instances
        instances = Instance.objects.filter(
            is_active=True
        ).select_related('product').prefetch_related('groups')
    else:
        # Regular users see instances they have group access to
        instances = Instance.objects.filter(
            groups__id__in=user_group_ids,
            is_active=True
        ).select_related('product').prefetch_related('groups').distinct()

    for instance in instances:
        services.append({
            'type': instance.product.slug,
            'name': instance.product.name,
            'instance_name': instance.name.title(),
            'url': instance.base_url,
            'description': instance.product.description,
            'icon': instance.product.icon,
        })

    # For standalone products (no instances), add directly
    standalone_products = subscribed_products.filter(
        requires_instance=False,
        is_addon=False
    )
    for product in standalone_products:
        if product.standalone_url:
            services.append({
                'type': product.slug,
                'name': product.name,
                'instance_name': product.standalone_url.replace('https://', '').split('/')[0],
                'url': product.standalone_url,
                'description': product.description,
                'icon': product.icon,
            })

    return render(request, 'dashboard/index.html', {
        'user': request.user,
        'profile': profile,
        'services': services,
        'is_admin': is_admin,
    })


def logout_view(request):
    """Logout from Django and Keycloak."""
    logout(request)

    # Build Keycloak logout URL with redirect to main site
    keycloak_logout_url = settings.OIDC_OP_LOGOUT_ENDPOINT
    params = {
        'client_id': settings.OIDC_RP_CLIENT_ID,
        'post_logout_redirect_uri': 'https://skylantix.com',
    }

    return redirect(f'{keycloak_logout_url}?{urlencode(params)}')
