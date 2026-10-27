"""Credential independence, fixed Linux account and private persistence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_bridge import Fixture
from wifi_bridge.config import Invalid, password_matches, validate_config
from wifi_bridge.network import LinuxNetwork


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
