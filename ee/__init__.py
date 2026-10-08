# Agents Hub enterprise edition: kept apart from the core, see ee/__init__.py.
"""
The enterprise part of Agents Hub: single sign-on (OIDC) and SCIM
provisioning, kept apart so a separate edition can be built from the core
without them. Everything outside this directory runs without it; ``common/edition.py`` says whether it is present, and the backend mounts
its routes only when it is.
"""
