"""Exercise real codesign/lipo rejection, rather than mocking subprocess success."""
import importlib.util
import platform
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macos_release", ROOT / "tauri-launcher/scripts/macos_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@unittest.skipUnless(platform.system() == "Darwin", "requires macOS signing tools")
class ReleaseIntegrityTests(unittest.TestCase):
    def test_unsigned_tampered_and_wrong_architecture_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "Example.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources/python").mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "example", "CFBundleIdentifier": "com.openjellyfish.test",
                "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
            }))
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n")
            binary = contents / "MacOS/example"
            release.run("cc", str(source), "-o", str(binary))
            nested = contents / "Resources/python/extension.so"
            nested.write_bytes(binary.read_bytes())
            resource = contents / "Resources/data.txt"
            resource.write_text("original")
            arch = "arm64" if platform.machine() == "arm64" else "x86_64"
            with self.assertRaises(subprocess.CalledProcessError):
                release.verify_signatures(app, arch)
            release.sign_resources(contents / "Resources")
            release.run("codesign", "--force", "--sign", "-", str(app))
            self.assertEqual(release.verify_signatures(app, arch), 2)
            wrong_arch = "x86_64" if arch == "arm64" else "arm64"
            with self.assertRaises(subprocess.CalledProcessError):
                release.verify_signatures(app, wrong_arch)
            # A real mounted DMG catches macOS /var -> /private/var aliases and
            # exercises detach-on-failure. Only the unrelated runtime smoke is replaced.
            dmg = root / "test.dmg"
            # Use a containing directory so the verifier sees an application.
            image_root = root / "image"
            image_root.mkdir()
            moved = image_root / app.name
            app.rename(moved)
            release.run("hdiutil", "create", "-srcfolder", str(image_root), "-volname", "ReleaseTest",
                        "-format", "UDZO", str(dmg))
            with patch.object(release, "verify_app", side_effect=release.verify_signatures):
                release.verify_dmg(dmg, arch)
            self.assertTrue(dmg.with_suffix(".dmg.sha256").is_file())
            with patch.object(release, "verify_app", side_effect=RuntimeError("simulated failure")):
                with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                    release.verify_dmg(dmg, arch)
            # It must be possible to mount again after either success or failure.
            with patch.object(release, "verify_app", side_effect=release.verify_signatures):
                release.verify_dmg(dmg, arch)
            moved.rename(app)
            resource.write_text("modified after signing")
            with self.assertRaises(subprocess.CalledProcessError):
                release.verify_signatures(app, arch)


if __name__ == "__main__":
    unittest.main()
