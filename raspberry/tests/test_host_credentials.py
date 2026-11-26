"""One bridge password: web interface, hotspot and Linux account, all or nothing, never in clear."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_bridge import Fixture
from wifi_bridge.api import ApiError
from wifi_bridge.config import Invalid, Store, hotspot_ssid, password_matches
from wifi_bridge.controller import Controller
from wifi_bridge.network import DemoNetwork, LinuxNetwork, NetworkError
from wifi_bridge.password import DEFAULT, wpa_psk

NEW = 'Orion-nebula-42'


class HostCredentialTests(Fixture):

    def test_helper_only_changes_bridge_and_removes_secret(self):
            from scripts import password as credentials
            pending=Path(self.tmp.name,'pending');pending.write_text('12345678')
            with patch.object(credentials,'PENDING',pending),patch.object(credentials.subprocess,'run') as command:
                credentials.apply_linux_password()
            self.assertEqual(command.call_args.args[0],['/usr/sbin/chpasswd'])
            self.assertEqual(command.call_args.kwargs['input'],'bridge:12345678\n')
            self.assertFalse(pending.exists())

    def test_bootstrap_default_preserves_existing_credentials(self):
            config=Path(self.tmp.name,'bootstrap.json');access=Path(self.tmp.name,'access.txt')
            subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/bootstrap.py'),str(config),str(access)],check=True)
            self.assertEqual(json.loads(config.read_text())['password'],'12345678')
            config.write_text('{"password":"existing-long-code"}')
            subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/bootstrap.py'),str(config),str(access)],check=True)
            self.assertEqual(json.loads(config.read_text())['password'],'existing-long-code')
