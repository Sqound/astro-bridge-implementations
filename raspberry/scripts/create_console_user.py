"""Executed inside the offline image chroot, with no secret in command arguments."""
import json
import pwd
from pathlib import Path
import subprocess

try:
    pwd.getpwnam("bridge")
except KeyError:
    subprocess.run(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo", "bridge"], check=True)
password = json.loads(Path("/etc/wifi-bridge/bootstrap.json").read_text())["password"]
subprocess.run(["chpasswd"], input="bridge:" + password + "\n", text=True, check=True)
