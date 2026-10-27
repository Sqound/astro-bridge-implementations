import copy
import http.client
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from wifi_bridge.config import Invalid, Store, password_matches, public_config, validate_config
from wifi_bridge.controller import Controller
from wifi_bridge.network import DemoNetwork, LinuxNetwork, NetworkError, keyfile, split_nm, radio_bands
from wifi_bridge.web import WebServer


def payload(devices):
    return {"hostname": "wifi-bridge-test", "country": "JP", "telescope_ip": "10.0.0.1",
            "admin_password": "local-admin-test-password",
            "home": {"ssid": "Maison", "password": "maison-test-pass", "mac": devices[1]["mac"]},
            "telescope": {"ssid": "Vespera-ABC", "password": "vespera-test-pass", "mac": devices[0]["mac"]}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.net = DemoNetwork()
        self.devices = self.net.devices()
        self.store = Store(self.tmp.name, self.devices[0]["mac"])
        self.controller = Controller(self.store, self.net, {"password": "bootstrap-test-code"}, demo=True)
        self.controller.update(devices=self.devices)

    def configured(self, changes=None):
        data = payload(self.devices)
        if changes:
            changes(data)
        self.store.save(validate_config(data, self.store.config, self.devices))
