> Commands use the verified staged project described in [candidate preparation](candidate-preparation.md). Hardware steps require separate owner approval.

# Attached ZWO ASI cameras

This is a USB camera driver for the **ZWO ASI SDK**. It finds the library by its base name, `libASICamera2.so.<version>`, and reads the version from the file name; no version is required. Versions that served a real camera through this driver are listed in `TESTED_VERSIONS` (`wifi_bridge/cameras/zwo_platform.py`, currently 1.41). Any other version loads with one warning in the service log. The 1.42 headers declare the same camera-information and control structures as 1.41. The Zero W and Zero 2 W serve connections and telemetry only. [Compatibility evidence](ALPINE-GLIBC-COMPAT.md)

## Installation on the Raspberry Pi 5

No ZWO file is part of the Raspberry Pi 5 image: no library, no licence text and no access rule. ZWO is a manufacturer of the common driver catalog like Player One, QHYCCD, Atik and Moravian. Its driver is downloaded from ZWO's site only when the owner asks, and the download stays subject to ZWO's terms (the 1.42 licence is an MIT-style grant, text and build identity). Two ways:

- **Ticked in Astro Bridge Setup.** Setup downloads the archive on the computer, checks its size and SHA-256 against the bridge's catalog and writes it on the card after the image. At the first boot the bridge installs it from the card, so the camera works without Internet on the bridge. A refused or incomplete file is reported on the ZWO line of the Cameras page.
- **Install in Configuration, Cameras, Camera drivers, line ZWO ASI.** After the owner accepts ZWO's terms, the bridge downloads the archive itself (it needs Internet and a plausible clock), checks it and installs it.

Both use the same installer (`wifi_bridge/cameras/drivers.py`): archive size and SHA-256, then only the library the catalog lists, with its own SHA-256, ELF class, machine and ABI, published in one rename. It writes the library, the path and digest declaration, the version record and the exact root-only udev rule for the reviewed camera PIDs in `/var/lib/wifi-bridge/camera-drivers/zwo/` (rule file `70-astro-bridge-zwo.rules`), and records the owner's acceptance (`camera-drivers/zwo-asi-license.json`: SDK version, library digest, time and clock status, terms page, catalog version and origin, `setup` or `web`). The image only carries a link at `/etc/udev/rules.d/70-astro-bridge-zwo.rules` to that rule: until the driver is installed, the link points nowhere and the camera has no device access. Reconnect the camera after installing so that discovery probes it again.

The library is loaded only while that record names the installed library. **Deactivate** removes the record: the library stays installed and its cameras stop being served at once. The line then offers **Reactivate** (the terms are asked again, nothing is downloaded) while the files are still exactly the catalog build, and **Install** otherwise. The routes exist only for the signed-in web interface. A camera card waiting for the driver points to Camera drivers.

The vendor replaces the archive behind its download address with each SDK release. The catalog pins the archive and the extracted library, so a new SDK release is refused (`archive_changed`) until a reviewed catalog update ships with a bridge release.

## Install on the host

Other hosts use the interactive [installer](../tools/install-zwo.py) and its adjacent [archive helper](../tools/asi_archive.py). The Raspberry Pi 5 image does not carry them (see above). Run it from a trusted, root-controlled checkout or installation on the target host. Production acquisition requires a private root-owned state directory, Python 3.11+, libusb 1.0, libstdc++, libgcc and the matching Linux userland. The installer checks for a writable local disk filesystem and 320 MiB of free temporary space on it. The temporary archive stays off the diskless RAM root.

Choose **one** command on the approved host:

```sh
# Alpine Pi 3B with the gcompat package and diskless rule link from this recipe
sudo python3 tools/install-zwo.py alpine

# Raspberry Pi OS on the Pi 3B
sudo python3 tools/install-zwo.py raspios

# Dedicated Linux host for native, rootful Docker Linux; example for an x64 container
sudo python3 tools/install-zwo.py docker-linux --container-abi x64
```

Alpine requires the mounted, writable ext4 volume labelled `ASTRO_DATA` at `/var/lib/wifi-bridge`, `/lib/libgcompat.so.0` and the matching persistent-rule link in the boot overlay. Installation refuses a RAM or wrong-volume fallback. The installer cannot add OS packages or rebuild the image.

On Docker Linux, run the installer on the Linux host. Supply the independently known **container userland ABI** as `x64`, `x86`, `armv8` (AArch64), or `armv6` (32-bit hard float, including a 32-bit OS on a Pi 3B). The installer checks it against the host Python ELF rather than relying on the kernel machine name. Cross-ABI/emulated containers and ARM hosts outside the approved Pi 3B tier are excluded. Bind the existing host state at the identical container path. Provide the USB device node for the intended camera, readable host USB sysfs inventory, and the Pi model where applicable. Device mapping, cgroups and runtime dependencies require separate host preparation. Docker Desktop and the demo have no ASI acquisition path. [Linux USB permissions and container options](CAMERA-USB.md#docker-deployment-notes)

The installer downloads the vendor ZIP from the vendor's hosts, finds the single Linux SDK archive in it and its version, and copies only the exact matching library and licence. It accepts a **known build** only: the library digest for the host ABI and the licence digest must match `KNOWN_BUILDS` in the archive helper (the 1.41 archive is also known by its own digest). The next SDK release is accepted by adding its digests there after review. It checks the library ELF class, endianness, machine and ARM float ABI, displays the complete licence, then requires the owner to type `ACCEPT` in a terminal. A refusal or unknown build leaves the driver uninstalled. The full licence, versioned library, narrow udev rule and path/digest declaration live together under `/var/lib/wifi-bridge/camera-drivers/zwo/`; an installation from an earlier release in `camera-drivers/zwo-1.41/` is still used. A newer accepted build replaces the previous one in one rename. Normal and error exits clean the temporary directory. After an unclean power loss, the owner may need to remove a residual `.asi-setup-*` directory from the state volume.

The root service uses an exact root-only udev rule for each reviewed camera PID. The vendor's example grants broader access and changes a host-wide USB buffer parameter, so the installers supply their own rule. Rule reload affects future device additions. Reconnect the camera after installation, and after resolving an ambiguous configuration, so discovery probes it again. On diskless boot the overlay's link resolves to the mounted state volume and provisioning reloads the rules without triggering devices. The installer never changes host-wide USB settings, the network, group membership or Docker configuration.

## Discovery and image format

USB vendor `03c3` also makes non-camera accessories. The table currently accepts the exact product IDs below. Additional models need a reviewed PID and the same capability checks. No claim of physical validation is attached to these entries.

| Product IDs | Reported camera family and source |
|---|---|
| `120a`, `120b` | [ASI120MM device report](https://github.com/aaronwmorris/indi-allsky/issues/2125), [ASI120MC USB descriptor](https://github.com/AllskyTeam/allsky/discussions/3905) |
| `120c`, `120e` | [ASI120MM Mini USB descriptor](https://github.com/indilib/indi/issues/950), [ASI120MC-S USB ID](https://github.com/usbids/usbids/blob/master/usb.ids) |
| `224a`, `294f` | [ASI224MC USB descriptor](https://github.com/aaronwmorris/indi-allsky/issues/2843), [ASI294MC Pro USB ID](https://github.com/usbids/usbids/blob/master/usb.ids) |
| `533f` | [ASI533MC Pro USB descriptor](https://forum.indilib.org/t/asi533mc-pro-and-ublox-7-gps-module/7179) |

The installed SDK must recognize the PID and report the corresponding model label. Immediately before opening and again before initialization, the SDK must see exactly one camera and fresh sysfs enumeration must identify exactly one SDK-recognized ASI on the expected port. A missing device, unknown inventory or ambiguity fails closed. The driver uses the property record's camera ID for operations, never its enumeration index. It cannot distinguish two physical units swapped at the same port between observations. A camera already opened keeps its handle. Reopening requires a new match and identical published capabilities. No serial, network instrument, firmware upload or guiding API is used.

The driver reads the model label, sensor size, pixel pitch, Bayer pattern, supported raw formats/bins and writable gain/exposure limits from the SDK. It uses the model label for SensorName because the SDK does not identify a separate sensor model. The service prefers RAW16 and falls back to RAW8. Pixel bytes are returned as received on little-endian Linux, with container bit depth and maximum representable code. Effective ADC precision can be lower. The driver rejects unsupported or oversized sensors (more than 16 Mi pixels), missing pitch/controls, flipped color output, mechanical shutters, and RGB-only formats. Oversized sensors are rejected before any ROI can be requested.

Exposure is rounded to the nearest microsecond. Manual gain, exposure, post-bin ROI dimensions and origin are read back before acquisition. Width must be a multiple of eight and height even after binning. The legacy USB2 ASI120 additionally requires an area divisible by 1,024. Color capture uses bin 1 and even origins to retain the advertised Bayer offsets. Mono cameras expose contiguous reported bin factors compatible with the full-frame geometry. The driver supports stop/abort via `ASIStopExposure`. The Alpaca facade requests real light frames for shutterless cameras. No synthetic dark frame or cooler control is provided.

The camera worker owns a separate child process for each open ASI. Only that process loads the trusted absolute library path with `RTLD_NOW | RTLD_LOCAL` after verifying the platform declaration, root-owned permissions and digest. Native stdout/stderr are discarded and core dumps disabled. Bounded Unix-socket messages carry metadata and at most one 32 MiB frame per read. The parent closes a timed-out/crashed helper. A child backstop also bounds native calls after parent loss. The service retains its existing one-second request timeout, the helper allows four seconds per call, and a polled exposure may take its requested duration plus ten seconds before timing out. A client timeout can precede completion of a worker call. Rediscover or check state before retrying. Real USB transfer times, process startup cost, peak RAM and driver behavior after an SDK crash or unplug require measurement.

Missing or deactivated library, untrusted or stale declaration, digest mismatch, loader dependency failure or missing entry point yield `driver_missing` during discovery. Unusable camera properties, ambiguity and device-open failures yield `unsupported`. Neither state advertises an Alpaca endpoint. The existing owner, release, paging and image-streaming contracts apply to ready cameras.

## Hardware evidence still needed

**Documented, not verified on hardware:** actual ASI control and image bytes, pitch and Bayer orientation, binning, exposure timing, abort, unplug/replug and permission recovery, Pi 3B CPU/RSS/SD use, USB3 cameras on USB2 or powered hubs, Linux/Docker pass-through and diskless rule restoration. Those checks are still open. The preparatory Zero W candidate predates these runtime files and requires a later authorized rebuild. See the [physical bench procedure](ALPINE-GLIBC-COMPAT.md#platform-integration-and-pi-3b-procedure).

**Verified with SDK 1.41, not verified with SDK 1.42.** The one hardware check so far (an ASI220MM, see the validation matrix) was made with SDK 1.41 supplied in an image. The driver code is unchanged. What changed is the SDK that is delivered (1.42, downloaded from ZWO) and how the library reaches the bridge (download and installation as above): neither has been replayed with a camera. The driver catalog holds one boolean per manufacturer, so `verified_on_hardware` is `false` for ZWO, and the Cameras page, the camera cards and Astro Bridge Setup show it as not verified on hardware. The bridge's own list of tested SDK versions (`TESTED_VERSIONS`, `VERIFIED_SDK_VERSIONS`) still names only 1.41 and 1.42 loads with a warning. The flag returns to `true` when the replay in the bench list passes with SDK 1.42 installed from the page and from Setup.

ZWO is one manufacturer of the common driver catalog; its line on the Cameras page sits with the others.
