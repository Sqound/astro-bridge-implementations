"""Inspect the generated diskless overlay, not a QEMU-only boot approximation."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


@unittest.skipUnless(shutil.which("sh") and shutil.which("install"), "POSIX image tools required")
class AlpineOverlayTests(unittest.TestCase):
    def test_generated_overlay_root_is_traversable_and_private_paths_stay_private(self):
        project = Path(__file__).resolve().parents[1]
        generator = project / "image/alpine/standard/genapkovl-astrobridge.sh"
        with tempfile.TemporaryDirectory() as directory:
            # Exercise the actual image generator under a restrictive caller umask.
            # A source-string assertion would miss the mode stored by tar for '.'.
            subprocess.run(
                ["sh", "-c", 'umask 077\nexec sh "$1" "$2"', "sh", str(generator), "test-appliance"],
                cwd=directory, env=dict(os.environ, ASTRO_BRIDGE_PROJECT=str(project)),
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            with tarfile.open(Path(directory) / "test-appliance.apkovl.tar.gz") as archive:
                for name in (
                    ".", "./etc", "./etc/NetworkManager", "./etc/NetworkManager/conf.d",
                    "./etc/avahi", "./etc/avahi/services", "./etc/init.d", "./etc/runlevels",
                    "./etc/runlevels/sysinit", "./etc/runlevels/boot", "./etc/runlevels/default",
                    "./etc/runlevels/shutdown", "./opt", "./opt/wifi-bridge",
                    "./opt/wifi-bridge/wifi_bridge", "./opt/wifi-bridge/wifi_bridge/static",
                    "./var", "./var/lib", "./usr", "./usr/libexec", "./etc/conf.d",
                    "./etc/periodic", "./etc/periodic/hourly", "./etc/periodic/daily", "./etc/ssh", "./etc/ssh/sshd_config.d",
                ):
                    with self.subTest(public_directory=name):
                        member = archive.getmember(name)
                        self.assertTrue(member.isdir())
                        self.assertEqual(member.mode, 0o755)
                for name in (
                    "./etc/wifi-bridge", "./etc/NetworkManager/system-connections",
                    "./var/lib/wifi-bridge",
                ):
                    with self.subTest(private_directory=name):
                        self.assertEqual(archive.getmember(name).mode, 0o700)
                world = archive.extractfile("./etc/apk/world").read().decode().splitlines()
                self.assertIn("openssl", world)
                self.assertIn("conntrack-tools", world)
                self.assertIn("usb-modeswitch", world)
                self.assertIn("usb-modeswitch-udev", world)
                # The udev subpackage installs the official dispatcher/rules;
                # udev-trigger must run at boot to apply it to attached dongles.
                self.assertEqual(archive.getmember("./etc/runlevels/sysinit/udev-trigger").linkname, "/etc/init.d/udev-trigger")
                self.assertEqual(archive.getmember("./etc/avahi/services/wifi-bridge-http.service").mode, 0o644)
                service = archive.extractfile("./etc/avahi/services/wifi-bridge-http.service").read()
                self.assertIn(b"_astrobridge._tcp", service)
                self.assertEqual(archive.extractfile("./etc/wifi-bridge/tls-helper-version").read().strip(), b"2")
                self.assertEqual(archive.getmember("./usr/libexec/astro-bridge-tls").mode, 0o755)
                self.assertEqual(archive.extractfile("./usr/libexec/astro-bridge-tls").read(), (project / "scripts/tls.py").read_bytes())
                self.assertFalse(any(name.endswith(("ca.key", "server.key", "ca.pem", "server.pem")) or "/tls/" in name for name in archive.getnames()))
                self.assertEqual(archive.getmember("./usr/libexec/astro-bridge-persist").mode, 0o755)
                self.assertEqual(archive.extractfile("./usr/libexec/astro-bridge-persist").read(),
                                 (project / "image/alpine/persistence.py").read_bytes())
                self.assertEqual(archive.getmember("./etc/periodic/hourly/astro-bridge-clock").mode, 0o755)
                self.assertEqual(archive.getmember("./usr/libexec/astro-bridge-ota").mode, 0o755)
                self.assertEqual(archive.extractfile("./usr/libexec/astro-bridge-ota").read(), (project/"scripts/ota.py").read_bytes())
                self.assertEqual(archive.getmember("./etc/periodic/daily/astro-bridge-updates").mode, 0o755)
                self.assertEqual(archive.extractfile("./etc/wifi-bridge/updater-version").read().strip(), b"2")

                self.assertNotIn("./etc/runlevels/boot/hwclock", archive.getnames())
                self.assertIn(b"ipv6", archive.extractfile("./etc/modules").read().splitlines())
                self.assertIn(b'rc_need="modules"', archive.extractfile("./etc/conf.d/sysctl").read())
                self.assertIn(b'rc_need="astro-bridge-provision"', archive.extractfile("./etc/conf.d/sshd").read())
                self.assertIn(b'sshd_disable_keygen="yes"', archive.extractfile("./etc/conf.d/sshd").read())
                self.assertIn(b'PermitRootLogin no', archive.extractfile("./etc/ssh/sshd_config.d/astro-bridge.conf").read())
                self.assertFalse(any("/identity/" in name or "ssh_host_" in name for name in archive.getnames()))
                self.assertEqual(archive.getmember("./etc/wifi-bridge/bootstrap.json").mode, 0o600)
                self.assertEqual(archive.getmember("./etc/init.d/astro-bridge-provision").mode, 0o755)
                self.assertEqual(archive.getmember("./etc/init.d/wifi-bridge").mode, 0o755)
                self.assertIn("./opt/wifi-bridge/wifi_bridge/static/i18n.js", archive.getnames())

    def test_package_is_available_in_media_as_well_as_overlay_world(self):
        project = Path(__file__).resolve().parents[1]
        profile = project / "image/alpine/standard/profile-astrobridge.sh"
        output = subprocess.run(
            ["sh", "-c", 'profile_rpi() { apks="base"; }\n. "$1"\nprofile_astrobridge\nprintf "%s\\n" "$apks"', "sh", str(profile)],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.split()
        self.assertIn("openssl", output)
        self.assertIn("conntrack-tools", output)
        self.assertIn("usb-modeswitch", output)
        self.assertIn("usb-modeswitch-udev", output)


if __name__ == "__main__":
    unittest.main()
