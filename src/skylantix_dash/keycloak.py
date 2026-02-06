import requests
from django.conf import settings


class KeycloakError(Exception):
    """Raised when a Keycloak API call fails."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class KeycloakAdmin:
    """Keycloak Admin API client using service account credentials."""

    def __init__(self):
        self.server_url = settings.KEYCLOAK_SERVER_URL
        self.realm = settings.KEYCLOAK_REALM
        self.client_id = settings.KEYCLOAK_ADMIN_CLIENT_ID
        self.client_secret = settings.KEYCLOAK_ADMIN_CLIENT_SECRET
        self._access_token = None

    def _get_token(self):
        """Get access token using client credentials grant.
        Raises KeycloakError on failure (e.g. bad credentials, server unreachable).
        """
        url = f'{self.server_url}/realms/{self.realm}/protocol/openid-connect/token'
        try:
            response = requests.post(url, data={
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
            })
            response.raise_for_status()
            self._access_token = response.json()['access_token']
            return self._access_token
        except requests.RequestException as e:
            resp = getattr(e, 'response', None)
            status_code = getattr(resp, 'status_code', None) if resp is not None else None
            message = (resp.text if resp is not None else None) or str(e)
            raise KeycloakError(
                f'Keycloak token request failed: {message}', status_code=status_code
            ) from e

    def _headers(self):
        """Get authorization headers."""
        if not self._access_token:
            self._get_token()
        return {
            'Authorization': f'Bearer {self._access_token}',
            'Content-Type': 'application/json',
        }

    def _request(self, method, endpoint, **kwargs):
        """Make authenticated request, refreshing token on 401."""
        url = f'{self.server_url}/admin/realms/{self.realm}{endpoint}'
        response = requests.request(method, url, headers=self._headers(), **kwargs)

        if response.status_code == 401:
            # Token expired, refresh and retry
            self._get_token()
            response = requests.request(method, url, headers=self._headers(), **kwargs)

        return response

    def create_user(self, email, username=None, first_name='', last_name='',
                    email_verified=True, enabled=True, temporary_password=None):
        """
        Create a new user in Keycloak.

        Returns tuple: (success: bool, user_id: str or None, error: str or None)
        """
        if not username:
            username = email

        user_data = {
            'username': username,
            'email': email,
            'firstName': first_name,
            'lastName': last_name,
            'emailVerified': email_verified,
            'enabled': enabled,
        }

        if temporary_password:
            user_data['credentials'] = [{
                'type': 'password',
                'value': temporary_password,
                'temporary': True,
            }]

        response = self._request('POST', '/users', json=user_data)

        if response.status_code == 201:
            # User created, extract ID from Location header
            location = response.headers.get('Location', '')
            user_id = location.split('/')[-1] if location else None
            return True, user_id, None
        elif response.status_code == 409:
            return False, None, 'User already exists'
        else:
            return False, None, response.text

    def get_user_by_email(self, email):
        """Get user by email. Returns user dict or None if not found.

        Raises KeycloakError if the API call fails.
        """
        response = self._request('GET', '/users', params={'email': email, 'exact': 'true'})
        if response.status_code == 200:
            users = response.json()
            return users[0] if users else None
        raise KeycloakError(f'Failed to check email: {response.text}', status_code=response.status_code)

    def get_user_by_username(self, username):
        """Get user by username. Returns user dict or None if not found.

        Raises KeycloakError if the API call fails.
        """
        response = self._request('GET', '/users', params={'username': username, 'exact': 'true'})
        if response.status_code == 200:
            users = response.json()
            return users[0] if users else None
        raise KeycloakError(f'Failed to check username: {response.text}', status_code=response.status_code)

    def get_user_by_id(self, user_id):
        """Get user by ID. Returns user dict or None."""
        response = self._request('GET', f'/users/{user_id}')
        if response.status_code == 200:
            return response.json()
        return None

    def send_verify_email(self, user_id):
        """Send email verification to user."""
        response = self._request('PUT', f'/users/{user_id}/send-verify-email')
        return response.status_code == 204

    def send_reset_password_email(self, user_id):
        """Send password reset email to user."""
        response = self._request('PUT', f'/users/{user_id}/execute-actions-email',
                                  json=['UPDATE_PASSWORD'])
        return response.status_code == 204

    def set_user_enabled(self, user_id, enabled):
        """Enable or disable a Keycloak user.

        Args:
            user_id: Keycloak user ID
            enabled: True to enable, False to disable

        Returns:
            bool: True if the update was successful
        """
        user = self.get_user_by_id(user_id)
        if not user:
            return False

        user['enabled'] = enabled
        response = self._request('PUT', f'/users/{user_id}', json=user)
        return response.status_code == 204

    def logout_user_sessions(self, user_id):
        """Terminate all active sessions for a user.

        Args:
            user_id: Keycloak user ID

        Returns:
            bool: True if successful
        """
        response = self._request('POST', f'/users/{user_id}/logout')
        return response.status_code == 204

    def delete_user(self, user_id):
        """Delete a user."""
        response = self._request('DELETE', f'/users/{user_id}')
        return response.status_code == 204

    def get_user_attributes(self, user_id):
        """
        Retrieve current user attributes from Keycloak.

        Args:
            user_id: Keycloak user ID

        Returns:
            dict: User attributes dictionary, or None if user not found
        """
        user = self.get_user_by_id(user_id)
        if user:
            return user.get('attributes', {})
        return None

    def update_user_attributes(self, user_id, attributes_dict):
        """
        Update Keycloak user attributes.

        Keycloak's PUT /users/{id} requires the full user representation;
        sending only attributes can wipe other fields in some versions.
        We GET the user, merge attributes, then PUT the full representation.

        Args:
            user_id: Keycloak user ID
            attributes_dict: Dictionary of attribute key-value pairs
                           Values should be strings or lists of strings

        Returns:
            bool: True if update was successful, False otherwise
        """
        # Keycloak expects attributes as lists of strings
        formatted_attributes = {}
        for key, value in attributes_dict.items():
            if value == '':
                formatted_attributes[key] = []
            elif isinstance(value, list):
                formatted_attributes[key] = value
            else:
                formatted_attributes[key] = [str(value)]

        user = self.get_user_by_id(user_id)
        if not user:
            return False

        existing_attributes = dict(user.get('attributes') or {})
        existing_attributes.update(formatted_attributes)
        existing_attributes = {k: v for k, v in existing_attributes.items() if v}

        # Build full user representation for PUT (avoid wiping username, email, etc.)
        # Strip fields that can trigger side effects (emails, credential resets)
        # when echoed back -- we only intend to modify custom attributes here.
        side_effect_fields = {'attributes', 'requiredActions', 'credentials'}
        user_payload = {k: v for k, v in user.items() if k not in side_effect_fields}
        user_payload['attributes'] = existing_attributes

        response = self._request('PUT', f'/users/{user_id}', json=user_payload)
        return response.status_code == 204

    def get_group_by_name(self, group_name):
        """
        Get group by name. Returns group dict or None if not found.

        Args:
            group_name: Name of the Keycloak group

        Returns:
            dict: Group data including 'id', or None
        """
        response = self._request('GET', '/groups', params={'search': group_name, 'exact': 'true'})
        if response.status_code == 200:
            groups = response.json()
            # Find exact match (search can be fuzzy in some Keycloak versions)
            for group in groups:
                if group.get('name') == group_name:
                    return group
        return None

    def add_user_to_group(self, user_id, group_id):
        """
        Add a user to a Keycloak group.

        Args:
            user_id: Keycloak user ID
            group_id: Keycloak group ID

        Returns:
            bool: True if successful, False otherwise
        """
        response = self._request('PUT', f'/users/{user_id}/groups/{group_id}')
        return response.status_code == 204

    def remove_user_from_group(self, user_id, group_id):
        """
        Remove a user from a Keycloak group.

        Args:
            user_id: Keycloak user ID
            group_id: Keycloak group ID

        Returns:
            bool: True if successful, False otherwise
        """
        response = self._request('DELETE', f'/users/{user_id}/groups/{group_id}')
        return response.status_code == 204


# Singleton instance
keycloak_admin = KeycloakAdmin()
