#!/usr/bin/env python3
"""Linux host contract preflight and app-owned service cleanup."""
import os
from pathlib import Path
import signal
import subprocess
import sys


def main():
    required=('/run/dbus/system_bus_socket','/run/systemd/private','/etc/wifi-bridge/bootstrap.json','/dev/rfkill')
    missing=[p for p in required if not Path(p).exists()]
    if os.geteuid()!=0 or missing:raise SystemExit('Linux host integration missing: '+', '.join(missing))
    if not any((p/'wireless').exists() or (p/'phy80211').exists() for p in Path('/sys/class/net').iterdir()):raise SystemExit('No physical Wi-Fi radio visible; use the demo target on Docker Desktop')
    child=subprocess.Popen([sys.executable,'-m','wifi_bridge',*sys.argv[1:]])
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda number,_frame:child.send_signal(number))
    try:return child.wait()
    finally:
        subprocess.run(['systemctl','stop','wifi-bridge-ap','wifi-bridge-dhcp'],timeout=30,check=False)
        subprocess.run(['nft','delete','table','ip','wifi_bridge'],timeout=5,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':raise SystemExit(main())
