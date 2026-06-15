"""Tests for the default admin bootstrap password behaviour.

The bootstrap admin user is created with the fixed default password "admin"
for first-login convenience; the startup log instructs operators to change it
after the first login.
"""

import inspect


def test_bootstrap_uses_fixed_admin_password():
    """create_default_admin_user must seed the documented default password "admin"."""
    from miramedia.auth.users import create_default_admin_user

    source = inspect.getsource(create_default_admin_user)
    assert 'default_password = "admin"' in source, (
        'Expected the fixed default bootstrap password "admin" in '
        "create_default_admin_user source."
    )
