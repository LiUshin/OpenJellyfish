"""Exercise the operator CLI without backend dependencies or real credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SuperadminLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'first'
        self.copy_cli(self.root)
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('JELLYFISH_SUPERADMIN_KEY_FILE', None)

    def copy_cli(self, target):
        for name in ('launcher.py', 'app/__init__.py', 'app/core/__init__.py', 'app/core/host_auth.py'):
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, out)
        # Deliberately omit settings.py and third-party packages.

    def run_cli(self, *args, root=None, env=None):
        result = subprocess.run([sys.executable, '-S', str((root or self.root) / 'launcher.py'), *args],
                                env=env or self.env, cwd=self.tmp.name, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_help_needs_no_backend_imports_and_creates_no_state(self):
        self.assertIn('--superadmin-key', self.run_cli('--help'))
        for name in ('config', 'data', 'logs'):
            self.assertFalse((self.root / name).exists())

    def test_key_commands_work_without_site_packages_and_keep_existing_key(self):
        first = self.run_cli('--superadmin-key')
        self.assertTrue(first.startswith('jf_host_') and len(first) == 72)
        self.assertEqual(first, self.run_cli('--superadmin-key'))
        replacement = self.run_cli('--rotate-superadmin-key')
        self.assertNotEqual(first, replacement)
        self.assertEqual(replacement, self.run_cli('--superadmin-key'))
        file = self.root / 'config/superadmin.key'
        self.assertEqual(file.read_text().strip(), replacement)
        if os.name != 'nt':
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'data').exists())
        self.assertFalse((self.root / 'logs').exists())

    def test_deployment_key_path_survives_replacing_application_directory(self):
        keyfile = Path(self.tmp.name) / 'persistent/superadmin.key'
        env = dict(self.env, JELLYFISH_SUPERADMIN_KEY_FILE=str(keyfile))
        first = self.run_cli('--superadmin-key', env=env)
        second_root = Path(self.tmp.name) / 'replacement'
        self.copy_cli(second_root)
        self.assertEqual(first, self.run_cli('--superadmin-key', env=env, root=second_root))
        self.assertFalse((second_root / 'config').exists())

    def test_relative_override_is_resolved_from_project_not_cwd(self):
        env = dict(self.env, JELLYFISH_SUPERADMIN_KEY_FILE='private/management.key')
        key = self.run_cli('--superadmin-key', env=env)
        self.assertEqual((self.root / 'private/management.key').read_text().strip(), key)
        self.assertFalse((Path(self.tmp.name) / 'private').exists())


if __name__ == '__main__':
    unittest.main()
