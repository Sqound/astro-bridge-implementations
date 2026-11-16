> Commands use the verified staged project described in [candidate preparation](candidate-preparation.md). Hardware steps require separate owner approval.

# Attached USB camera discovery

This design separates Linux inventory from the generic camera service. Inventory and
its optional monitor require an explicit supported-platform gate. Pi Zero W and
Zero 2 W remain connections/telemetry appliances; served cameras target the
approved Raspberry Pi 3/4 tier. Demo never enumerates host USB devices. Importing
the modules, constructing the inventory object, or a disabled coordinator reads
no sysfs data and opens no socket. Startup and lazy API access apply the platform
gate before constructing a service worker, listener or USB monitor. Unsupported
tiers retain inventory/settings access without starting a camera worker.
An unknown ARM tier fails closed when device-tree model evidence is absent or
unreadable, including ARM containers with a hidden device tree. Recognized Pi 3/4
and x86 Linux remain eligible. Another ARM64 mini-PC/Docker implementation needs
a reviewed platform eligibility declaration of its own; no environment
override bypasses this check. This is a declared deployment limitation.

`SysfsUSB` reads only bounded metadata under an injected `/sys/bus/usb/devices`
root: `idVendor`, `idProduct`, `bDeviceClass`, `bInterfaceClass`, `speed`, and the
numeric bus/port path. It reads no serial, manufacturer/product strings, image or
device payload. Each attribute is limited to 64 bytes, enumeration to 512 entries.
Unavailable, malformed or overflowing inventory is uncertain evidence: it cannot
remove a camera. A device disappearing while attributes are read is reconciled
as absent. The inventory itself opens neither USB device nodes nor video nodes.

The coordinator polls every five seconds. Optional nonblocking
`AF_NETLINK`/`NETLINK_KOBJECT_UEVENT` USB add/remove/change messages wake inventory
reconciliation, with a minimum half-second interval and at most 32 datagrams per
drain. Only kernel-origin messages are accepted. Messages are discarded after
classification; loss/overflow triggers reconciliation. Unsupported netlink or
permissions retain the five-second polling fallback.

Camera descriptors come from reviewed `cameras/*.json`. Exact vendor/product
rules outrank class hints, including interface classes of composite devices.
Unknown devices, hubs and Wi-Fi dongles do not become cameras. Network interface
classes rule out a generic camera class match. A reviewed exact vendor/product
rule may identify a future composite camera explicitly. The reviewed UVC
driver is installed, but a class hint alone does not promise a supported format
or the mandatory physical pixel pitch. Missing/invalid owner calibration or
unsupported capture properties produce `unsupported`, not `driver_missing`.
See [UVC acquisition and owner calibration](CAMERA-UVC.md). Discovering a device
does not install a kernel driver or vendor SDK.

The existing bounded camera worker alone calls factories, metadata and closes.
Each new attachment passes `detected` → `preparing` → `ready`, `driver_missing`
or `unsupported`. Discovery state is separate from the existing driver `state`.
Unserved records have `state:error`, `alpaca:null` and neutral `last_image` values;
they never enter Alpaca configured devices. A ready record still needs an actual
bound management listener before it receives an Alpaca endpoint. Removal releases
ownership/frame data, and removed Alpaca numbers never redirect to another
attachment key. Numbers are bounded to 0–7. The service reserves each assigned
number for its attachment key until restart; replug of that key reuses its number.
After eight distinct ready keys, another key remains `unsupported` with
`alpaca:null` until service restart, even if an earlier camera was removed. A
removed key cannot execute Alpaca operations. This fails closed instead of
recycling a removed number for another key.
Attachment IDs combine bus/port and model IDs without serials; they are not durable
physical-camera identity across port moves. A physically different camera of the
same model at the same port is indistinguishable from replug without serial
probing. A service restart resets numbering and reservations; clients must
rediscover configured devices after restart.

Three observed disconnects of the same attachment in a rolling ten-minute window
set `power_unstable` and emit one warning on reaching the threshold. History is
memory-only and bounded to 256 attachment keys and 1,201 timestamps per key. This
is evidence of unstable attachment, not a voltage measurement or proof of a bad
power supply. Polling can miss an unplug/replug completed entirely between scans.

## Linux and Alpine deployment permissions

The platform must supply read access to sysfs and, only for an installed driver,
the exact required `/dev/bus/usb/BBB/DDD` or `/dev/videoN` nodes. Do not grant
global world-write USB permissions. The current appliance service runs as root;
a non-root implementation should provision a dedicated group and narrowly scoped
reviewed rules. For example, replace the placeholders with an approved device ID
and the implementation's existing service group before installation:

```udev
# /etc/udev/rules.d/70-astro-bridge-camera.rules (template only)
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="vvvv", ATTR{idProduct}=="pppp", GROUP="bridge-camera", MODE="0660"
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="vvvv", ATTRS{idProduct}=="pppp", GROUP="bridge-camera", MODE="0660"
```

Alpine/eudev and other Linux implementations own group membership, udev rule
installation/reload, kernel driver access, optional SDK availability/licensing and
OS/architecture compatibility. An SDK is never downloaded automatically. Avoid
running usb-modeswitch against a candidate merely because the package is installed;
discovery performs no mode switch, rebind, reset or state-changing USB command.
A reviewed vendor driver may call `wifi_bridge.cameras.loader.load_platform_sdk(path)`
in its own process after the platform verifies the camera tier, owner-installed
SDK identity, file permissions and ABI. The platform supplies the absolute path
from trusted implementation code. The loader uses direct eager, local symbol
resolution and performs no SDK calls. A missing path or failed load becomes
`camera_driver_unavailable`. The [ASI adapter and owner installer](CAMERA-ZWO.md) implement this path for
reviewed ZWO USB camera IDs. Discovery and API requests do not select SDK paths.
See the [Alpine no-device compatibility results](ALPINE-GLIBC-COMPAT.md).

## Docker deployment notes

A Linux host/container implementation needs a read-only view of the corresponding
sysfs tree and explicit device access. `--device /dev/bus/usb` is the documented
USB-bus mapping option; it grants broad bus access and belongs only on a dedicated,
reviewed deployment. Prefer narrower device-node mappings when the driver and
hotplug policy support them. Map each reviewed video node separately, for example
`--device /dev/video0`; `/dev/video*` is documentation shorthand, not a literal
Docker argument. Device cgroup rules, group IDs and newly appearing nodes remain
implementation responsibilities. Readable sysfs alone does not provide driver
access. Docker Desktop USB forwarding is a separate platform requirement and is
not established by these options. No Docker image/container was built or changed.

## Installed tables and verification

`tools/build-cameras.py` validates root descriptors and generates the editor schema
and reviewed `wifi_bridge/cameras/tables/*.json` bundle. Its `--check` rejects stale
or unexpected bundled files. Runtime defaults to the installed bundle; explicit
directories remain injectable. The shared source-copy helper admits only validated
camera table JSON with matching filenames, rejects symlinks, and excludes caches,
hidden files and arbitrary runtime JSON. Package data includes the same tables.
Existing source installers and Alpine overlays inherit this policy. The assembled
candidate snapshot and source identity include the same table bundle and apply
the shared private-artifact guard before copying or hashing files;
app-only/OTA delivery remains blocked on the separately tracked packaging work.

After sourcing the workspace `env.sh`, focused checks are:

```sh
python3 -m unittest discover -s tests -p 'test_camera*.py' -v
python3 -m unittest discover -s tests -p test_app_copy.py -v
python3 tools/build-cameras.py --check
python3 -m compileall -q wifi_bridge
```

Temporary fixture trees contain a hub, two Wi-Fi adapters, a composite camera
and unknown devices. Tests cover replug/power-window expiry, uncertain evidence,
bounded polling/netlink floods, one-worker driver calls and stable device numbers.
They do not enumerate the host USB bus. Real USB hotplug, over-current/power
behavior, Linux/eudev delivery, permissions, container device forwarding, camera
SDKs and Pi performance remain **documented, not verified on hardware**. The existing activity view gains three camera event labels. Six new English
source strings use English placeholders in the other nine catalogs pending translation.

The native paging helper preserves the frozen maximum of 24
instruments per page while accommodating 24 saved profiles and eight cameras.
Saved-profile ordering comes first, followed by cameras sorted by opaque ID.
Its bounded opaque cursor binds to ordered membership and the service boot ID;
membership/order changes or restart require a fresh traversal (`CATALOG_CHANGED`).
State-only changes do not invalidate it. Invalid cursors never silently restart.
The helper's seven tests run with
`python3 -m unittest discover -s tests -p test_instrument_pages.py -v`.
`GET /api/v1/instruments?after=...` applies this helper, with HTTP 400
`INVALID_CURSOR` for invalid cursors and HTTP 409 `CATALOG_CHANGED` on changed
membership/order or restart. `AstroBridgeClient.allInstruments()` collects at most
two pages per attempt, restarting at most twice after the first attempt.
