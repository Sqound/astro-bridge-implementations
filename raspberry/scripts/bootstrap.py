"""Initialize the documented default credential; preserve existing installations."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wifi_bridge.config import atomic_json

config_path, access_path = map(Path, sys.argv[1:])
if config_path.exists():
    value = json.loads(config_path.read_text())
else:
    value = {"password": "12345678"}
    atomic_json(config_path, value)
payload = ("SMART TELESCOPE / WI-FI BRIDGE — KEEP THIS ACCESS FILE\n\n"
           "1. Power on the Pi and allow up to three minutes on first boot.\n"
           "2. Join AstroBridge-Setup-xxxxxx (Pi MAC suffix).\n"
           "3. Open http://192.168.77.1 in your browser.\n"
           "4. Hotspot and initial web access code: " + value["password"] + "\n"
           "5. Configure networks. Change default passwords in Settings.\n\n"
           "Default for new images: 12345678 (Linux bridge, web and hotspot).\n"
           "Change all three independently in the web Settings before normal use.\n"
           "Local Linux console: user bridge, password = code above (built image only).\n"
           "SSH starts automatically in the built image (user bridge).\n"
           "Existing installations keep their credentials during upgrades.\n")
access_path.parent.mkdir(parents=True, exist_ok=True)
access_path.write_text(payload)
try:
    os.chmod(access_path, 0o600)
except PermissionError:
    pass  # FAT boot partition has no POSIX file permissions.
