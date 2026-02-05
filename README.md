# Skylantix Dashboard

This is a Django-based dashboard meant to unify user access and control. It is a dedicated backend service responsible for **onboarding, subscriptions, and access control**. It is intentionally **separate** from the marketing website and **delegates identity and billing** to external systems. It is used in combination with Keycloak for SSO and user, Stripe for billing, and n8n for other tasks. 

The vision is this: There are two main flows, creating a new user and logging in an existing user. 

Creating a new user invovles taking them to a page where they select their products and complete payment via Stripe, in which a new user is created in Keycloak with their credentials. This flow cannot be accessed outside of creating a new subscription.

For an existing user, they will be able to log in using Keycloak as SSO and be able to see a dashboard linking to their products as well as a tab to manage or modify their subscription, change their password, etc. 

This will still need a database, though user auth will be handled by keycloak, and it should use postgres. It should be dockerized and have up to date production secuirty standards.

