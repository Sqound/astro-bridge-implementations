"""Real Web Push encryption roundtrip; lifecycle tests never contact a push provider."""
import base64
import json
from pathlib import Path
import plistlib
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from wifi_bridge.api import ApiError
from wifi_bridge.events import EventStore
from wifi_bridge.push import ConnectionNotices, PushService, validate_subscription


def encoded(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def subscription(suffix='test'):
    return {'endpoint': 'https://web.push.apple.com/' + suffix,
            'keys': {'p256dh': encoded(b'\x04' + b'a' * 64), 'auth': encoded(b'b' * 16)}}
