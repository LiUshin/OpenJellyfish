from app.core.host_auth import HOST_ID
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException

from app.core.roles import is_owner, role_for
from app.runtime.policy import DeploymentPolicy
from app.services.script_runner import superadmin_script_unrestricted


class RuntimePolicyTests(unittest.TestCase):
    def test_owner_is_never_inferred_from_pilot_or_username(self):
        with patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': '', 'JELLYFISH_CODEX_ADMIN_ID': 'alice'}):
            self.assertFalse(is_owner('alice'))
            self.assertEqual(role_for('admin'), 'admin')
            DeploymentPolicy().ensure_supported()

    def test_role_and_backend_matrix_fails_closed(self):
        with patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': 'owner'}):
            DeploymentPolicy().require_connection_owner(HOST_ID)
            self.assertFalse(is_owner('owner'))
            self.assertEqual(role_for('owner'), 'admin')
            with self.assertRaises(HTTPException) as denial:
                DeploymentPolicy().require_connection_owner('admin')
            self.assertEqual(denial.exception.status_code, 403)
            for mode, backend in [('tenant_isolated', 'local'), ('tenant_isolated', 'docker'),
                                  ('tenant_isolated', 'cloudflare'), ('wrong', 'local')]:
                with self.assertRaises(HTTPException):
                    DeploymentPolicy(mode, backend).ensure_supported()

    def test_unrestricted_override_requires_actor_owner(self):
        with patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': 'owner', 'SUPERADMIN_SCRIPT_UNRESTRICTED': '1'}):
            self.assertTrue(superadmin_script_unrestricted(HOST_ID))
            self.assertFalse(superadmin_script_unrestricted('owner'))
            self.assertFalse(superadmin_script_unrestricted('admin'))
            self.assertFalse(superadmin_script_unrestricted())

    def test_disabled_capabilities_do_not_advertise_login(self):
        with patch.dict(os.environ, {'JELLYFISH_OWNER_USER_ID': 'owner', 'JELLYFISH_RUNTIME_ENABLED': '0'}):
            caps = DeploymentPolicy().public('owner')
            self.assertFalse(caps['available'])
            self.assertFalse(caps['can_manage_connections'])
