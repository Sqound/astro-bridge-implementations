> Commands use the verified staged project described in [candidate preparation](candidate-preparation.md). Hardware steps require separate owner approval.

# Attached ZWO ASI cameras

This is a USB camera driver for the owner-installed **ZWO ASI SDK 1.41**. The first physical bench is a Raspberry Pi 3B. The Zero W and Zero 2 W serve connections and telemetry only. Pi 4 camera acceptance requires a separate owner decision. The ASI adapter and installer have offline fixture coverage. The earlier Alpine probe verified library loading and zero-device enumeration under emulation. [Compatibility evidence](ALPINE-GLIBC-COMPAT.md)

## Install on the host

The implementation sources ship an interactive [installer](../tools/install-zwo.py) and its adjacent [archive helper](../tools/asi_archive.py). Run them from a trusted, root-controlled checkout or installation on the target host, after reviewing the dependency and hardware requirements. Production acquisition requires a private root-owned state directory, Python 3.11+, libusb 1.0, libstdc++, libgcc and the matching Linux userland. The installer checks for a writable local disk filesystem and 320 MiB of free temporary space on it. The temporary archive stays off the diskless RAM root. Licence and archive identity

Choose **one** command on the approved host:

```sh
# Alpine Pi 3B with the gcompat package and diskless rule link from this recipe
sudo python3 tools/install-zwo.py alpine

# Raspberry Pi OS on the Pi 3B
sudo python3 tools/install-zwo.py raspios

# Dedicated Linux host for native, rootful Docker Linux; example for an x64 container
sudo python3 tools/install-zwo.py docker-linux --container-abi x64
```

Alpine requires the mounted, writable ext4 volume labelled `ASTRO_DATA` at `/var/lib/wifi-bridge`, `/lib/libgcompat.so.0` and the matching persistent-rule link in the boot overlay. Installation refuses a RAM or wrong-volume fallback. The source change needs a separately authorized image build for appliances lacking those prerequisites. The installer cannot add OS packages or rebuild the image.

On Docker Linux, run the installer on the Linux host. Supply the independently known **container userland ABI** as `x64`, `x86`, `armv8` (AArch64), or `armv6` (32-bit hard float, including a 32-bit OS on a Pi 3B). The installer checks it against the host Python ELF rather than relying on the kernel machine name. Cross-ABI/emulated containers and ARM hosts outside the approved Pi 3B tier are excluded. Bind the existing host state at the identical container path. Provide the USB device node for the intended camera, readable host USB sysfs inventory, and the Pi model where applicable. Device mapping, cgroups and runtime dependencies require separate host preparation. Docker Desktop and the demo have no ASI acquisition path. [Linux USB permissions and container options](CAMERA-USB.md#docker-deployment-notes)

The installer downloads the pinned vendor ZIP, verifies its SHA-256, copies only the exact matching library and licence from the Linux archive, and checks the library ELF class, endianness, machine and ARM float ABI. It displays the complete licence, then requires the owner to type `ACCEPT` in a terminal. A refusal or digest mismatch leaves the driver uninstalled. The full licence, versioned library, narrow udev rule and path/digest declaration live together under `/var/lib/wifi-bridge/camera-drivers/zwo-1.41/`. Re-running with identical files can finish a failed udev reload. A conflicting installation or rule requires owner inspection. Normal and error exits clean the temporary directory. After an unclean power loss, the owner may need to remove a residual `.asi-setup-*` directory from the state volume.

The root service uses an exact root-only udev rule for each reviewed camera PID. The vendor's example grants broader access and changes a host-wide USB buffer parameter, so the installer supplies its own rule. Rule reload affects future device additions. Reconnect the camera after installation, and after resolving an ambiguous configuration, so discovery probes it again. On diskless boot the overlay's link resolves to the mounted state volume and provisioning reloads the rules without triggering devices. The installer never changes host-wide USB settings, the network, group membership or Docker configuration.

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

Missing library, untrusted or stale declaration, digest mismatch, loader dependency failure or missing entry point yield `driver_missing` during discovery. Unusable camera properties, ambiguity and device-open failures yield `unsupported`. Neither state advertises an Alpaca endpoint. The existing owner, release, paging and image-streaming contracts apply to ready cameras.

## Hardware evidence still needed

**Documented, not verified on hardware:** actual ASI control and image bytes, pitch and Bayer orientation, binning, exposure timing, abort, unplug/replug and permission recovery, Pi 3B CPU/RSS/SD use, USB3 cameras on USB2 or powered hubs, Linux/Docker pass-through and diskless rule restoration. Those checks are still open. The preparatory Zero W candidate predates these runtime files and requires a later authorized rebuild. See the [physical bench procedure](ALPINE-GLIBC-COMPAT.md#platform-integration-and-pi-3b-procedure).
