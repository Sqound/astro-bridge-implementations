> Commands use the verified staged project described in [candidate preparation](candidate-preparation.md). Hardware steps require separate owner approval.

# Attached UVC / V4L2 camera driver

The standard-library `cameras/uvc.py` driver acquires luminance from a physically
attached Linux UVC camera through `cameras/v4l2.py`. It uses `fcntl.ioctl`, ctypes
and shared `mmap`, with no SDK, subprocess, network, module load or saved image.
The reviewed registry installs a lazy `uvc` factory. Import and construction
perform no discovery or device I/O. Existing owner-serving and Pi-tier gates
still run before camera discovery/worker/listener creation; Pi Zero/Zero 2 remain
connections/telemetry appliances.

**Generic UVC class detection alone is not sufficient for an Alpaca camera.**
Alpaca requires positive physical pixel pitch, which this UVC interface does not
supply. `UVCCamera(pixel_um=(x, y), ...)` accepts reviewed platform metadata in
micrometres, not a guessed default. The owner can provision the small state-local
calibration file below; the existing camera service passes its validated pitch
to the generic factory. No physical model/pitch mapping is included,
and there is no owner pitch editor/API yet. Missing metadata leaves an attachment
`unsupported`, with no Alpaca device or endpoint, before probing a video node.
Automated tests supply fictional pitch solely with fake I/O. Unknown pitch is
never reported as a successful camera returning invented values or mandatory
property errors.

The [ASCOM camera contract](https://ascom-standards.org/newdocs/camera.html#Camera.PixelSizeX)
requires the physical unbinned pitch; its absence is not an optional capability.
This driver does not claim ConformU acceptance or physical camera support.

## Owner calibration provisioning

This is a documented owner procedure, not an action performed by the driver:

1. Find the camera's existing opaque `id` (`usb-` plus 24 hexadecimal characters)
   in authenticated `GET /api/v1/cameras`. Use the attachment `id`, not a
   collision-renamed `instrument_id`. Without calibration it has
   `discovery.state=unsupported`, `state=error`, `alpaca=null`.
2. Obtain the physical unbinned X/Y pixel pitch in micrometres from the exact
   sensor/camera manufacturer's documentation or a reviewed measurement. Do not
   infer it from image dimensions or choose a placeholder to enable the device.
3. Prepare `camera-calibration.json` next to the existing `cameras.json` in the
   configured bridge state directory (default `/var/lib/wifi-bridge`). Preserve
   other calibrated entries. Validate the JSON structure below, write a regular
   file with mode 0600 owned by the service administrator, and atomically rename
   it into place. No change to the application source or installed tables is needed.
4. Stop any active camera exposure/client use, then unplug/replug the camera or
   restart the bridge service deliberately. The service reads calibration only
   for a newly prepared attachment; changing the file does not alter an active
   device or automatically retry an existing unsupported record.
5. Check inventory again. `ready` means supplied metadata and camera negotiation
   succeeded; an endpoint still requires a bound management listener. A changed
   port produces a different attachment ID, so calibration must be reviewed for
   that ID. Replacement with an identical model on the same port is not a durable
   physical identity; the owner must keep its calibration correct.

Example structure (fictional attachment and pixel pitch; do not install verbatim):

```json
{
  "schema_version": 1,
  "cameras": {
    "usb-111111111111111111111111": {"pixel_um": [3.75, 3.75]}
  }
}
```

The parser accepts at most 8 KiB and 32 attachments. Both values must be finite,
positive numbers in 0.01–1000 µm; booleans, duplicate/unknown fields, invalid IDs,
oversized documents, symlinks and nonregular files fail closed. It reads on the
existing camera worker after the serving/platform gates. No key, serial, file
path, SDK configuration or arbitrary device setting is accepted. Malformed
metadata leaves preparation unsupported with no camera-node probe; a previously
active camera is unaffected until explicit reattachment/restart.

## Negotiation and acquisition

An eligible, calibrated instance resolves only video nodes under its already
identified USB bus/port in sysfs. It checks vendor/product IDs, the node's kernel
device number and character-device type. Paths and IDs never come from client
addresses or executable manifest strings. Resolution reads at most 512 video
class entries and accepts at most eight associated nodes. Each candidate is
opened separately with `O_NONBLOCK`, `O_NOFOLLOW` and `O_CLOEXEC`.

`QUERYCAP` must identify `uvcvideo` with single-planar capture and streaming.
Metadata-only nodes, output devices, other drivers, compressed-only and
multi-planar-only formats are unsupported. Enumeration is limited to 64 format
and 64 frame-size entries, and eight candidate sizes per accepted format.
Negotiation prefers GREY, then Y16, then YUYV; it tries supported sizes near
640×480, bounded by 1280×960. It handles discrete, continuous and stepwise sizes,
with one bounded S_FMT fallback if frame-size enumeration is unavailable.
Negotiated dimensions, progressive field, stride and image size are validated;
no compressed substitution is accepted. Reopen must retain published geometry,
format and capabilities. The temporary info probe closes its fd without starting
capture; an Alpaca connection opens the actual capture fd on the existing worker.

GREY and YUYV luminance produce `Frame.element_type=Byte` and MaxADU 255.
Y16 remains little-endian UInt16/MaxADU 65535. YUYV takes each Y byte unchanged,
without a colour reconstruction or a full-range rescale. Row padding is removed;
short frames, odd-width YUYV and invalid/error buffers are rejected. ROI is a
software crop in unbinned pixels; binning is limited to 1. As required by
[Camera V3 StartExposure](https://ascom-standards.org/library/html/M_ASCOM_Common_DeviceInterfaces_ICameraV3_StartExposure.htm),
HasShutter=False means Light=False is ignored: it still captures actual incoming
luminance, without manufacturing dark pixels. The request must still contain a
valid boolean. Monochrome BayerOffsetX/Y return Alpaca NotImplemented (0x400),
while colour offsets retain their sensor-relative values, as specified by the
[Camera V3 property contract](https://ascom-standards.org/library/html/P_ASCOM_Common_DeviceInterfaces_ICameraV3_BayerOffsetX.htm).

The existing Alpaca ImageArray contract retains logical **ImageElementType
Int32 (2)** and JSON Type 2. For 8-bit frames the compact **TransmissionElementType
is Byte (6)**; for Y16 it is UInt16 (8). Thus the brief's Byte requirement applies
to the actual frame/wire representation, without changing the logical array type.
[Alpaca API reference v12, sections 8.3 and 8.10.1](https://ascom-standards.org/AlpacaDeveloper/ASCOMAlpacaAPIReference.html)
distinguishes logical image type from compact transmission type. Tests compare
both representations' types, pixel ordering and values.

## Exposure and stacked frames

G_PARM must provide a finite positive frame interval. When available, the driver
queries exposure/gain bounds and steps, switches exposure to manual and disables
autogain, then writes and reads back `V4L2_CID_EXPOSURE_ABSOLUTE` and
`V4L2_CID_GAIN`. Exposure units are 100 microseconds; a frame's maximum is capped
by the reported interval and rounded down to a supported control step. A refused
control or different readback fails capture. Missing exposure control uses the
reported video interval as **nominal frame duration**, not measured sensor
integration. Missing gain supports only neutral gain 0.

Requests exceeding one frame use **stacked frames**: choose N frames, quantize
the individual exposure, accumulate luminance in a bounded unsigned 32-bit array,
and return the integer arithmetic mean. This reduces noise; it is **not a true
long exposure**, not a sum, and not photometric equivalence to one continuous
integration. Frames need not be contiguous because the service cooperatively
polls the queue. Firmware pipeline latency/control application and actual exposure
limits require device testing. Internal normalized metadata records
`stacked_frames`, `frame_seconds` and their product `exposure_seconds`; bridge
inventory/Alpaca's existing last-request duration remains the requested duration.
No new bridge API fields or client routes are introduced.

Limits per exposure: at most 256 frames, at most 128 Mi pixel visits (using full
capture dimensions), at most 3,600 requested seconds, and a 7,200-second absolute
capture deadline. The advertised exposure maximum is reduced to the frame/work
budget. Each state call attempts one nonblocking DQBUF and processes at most one
bounded frame. EAGAIN/EINTR yield immediately. A missing frame times out after
max(2 seconds, three reported frame periods); an additional total deadline bounds
repeated progress. Abort/disconnect discard the partial average immediately on
the owning worker's next turn. No internal capture thread is spawned.

The driver requests two MMAP buffers and accepts one to four, each at most 4 MiB
(16 MiB mappings total). QUERYBUF offsets/lengths are used verbatim after bounds
checks. Completed/error/aborted streams execute STREAMOFF, unmap all buffers and
release REQBUFS; STREAMOFF failure closes the fd before unmapping. Repeated close
is safe. OS ioctl hangs cannot be preempted by Python; the existing service stalls
without spawning a second worker and retains bounded caller/shutdown waits.
CPU deadlines bound turns between operations, not a hard-real-time kernel call.

Linux references: [exposure controls](https://docs.kernel.org/userspace-api/media/v4l/ext-ctrls-camera.html),
[MMAP capture](https://docs.kernel.org/userspace-api/media/v4l/mmap.html),
[nonblocking queue operations](https://docs.kernel.org/userspace-api/media/v4l/vidioc-qbuf.html).

## ABI: armhf time64 is not LP64

Small bindings are derived from factual layouts in the pinned
[Linux v6.12 UAPI](https://raw.githubusercontent.com/torvalds/linux/v6.12/include/uapi/linux/videodev2.h),
not vendored headers. Pointer width and timestamp width are independent inputs.
Only little-endian ARM EABI/AArch64 and x86-64, using asm-generic ioctl numbers,
are eligible. The runtime explicitly uses two signed 64-bit timestamp fields;
it does not infer libc `time_t` from Python's pointer or C-long size.

| Layout | Buffer size | Timestamp offset | Memory offset | mmap-offset offset | Length offset | S_FMT size |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| armhf legacy time32 (test reference only) | 68 | 20 | 48 | 52 | 56 | 204 |
| armhf time64 (runtime 32-bit) | 80 | 24 | 60 | 64 | 68 | 204 |
| LP64 time64 (runtime 64-bit) | 88 | 24 | 60 | 64 | 72 | 208 |

Corresponding DQBUF commands are `0xc0445611`, `0xc0505611`, `0xc0585611`.
The armhf structure aligns its 64-bit timestamps at eight bytes while retaining
four-byte pointers. Kernel v6.12 explicitly defines a time64 V4L2 timestamp.
[Kernel time32 conversion](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/media/v4l2-core/v4l2-ioctl.c)
handles the old request separately when configured. The
[musl 1.2.5 ioctl implementation](https://git.musl-libc.org/cgit/musl/tree/src/misc/ioctl.c?h=v1.2.5)
first issues the supplied command and, on ENOTTY, maps its time64 V4L2 requests
to the legacy 68-byte ABI. The explicit runtime layout matches its armhf time64
request; no libc timestamp declaration is reconstructed. This reasoning and
host-side byte fixtures do not verify the appliance's libc/kernel configuration.
Old kernels/other ABIs are outside the support claim.

## Validation and remaining work

After sourcing the workspace `env.sh`:

```sh
python3 -m unittest discover -s tests -p test_camera_uvc.py -v
python3 -m unittest discover -s tests -p test_camera_calibration.py -v
python3 -m unittest discover -s tests -p 'test_camera*.py' -v
python3 -m unittest discover -s tests -p 'test_alpaca*.py' -v
python3 -m compileall -q wifi_bridge
python3 tools/build-cameras.py --check
```

All UVC tests inject fake ioctl/mmap/fd operations and clocks, or use temporary
sysfs fixtures. There is no host USB/sysfs/device scan, real ioctl or module load.
Golden sizes/offsets/request numbers cover all three ABI layouts. Tests cover
format fallback/stride/Y16/YUYV/Byte, controls, stack/ROI means, enum/mapping bounds,
nonblocking timeouts, abort/reopen/unplug and partial-resource failure cleanup,
plus service readiness and missing physical metadata. No browser interaction or
localized product copy changes; the brief specifies no browser scenario.

**Documented, not verified on hardware:** actual cameras and their pitch
metadata, exposure-control limits/latency, armhf structure alignment and ioctl
acceptance, Alpine/musl time64 behavior, Pi 3/4 timing/memory, permissions, real
hotplug and sustained imaging. Alpine linux-rpi modloop is not pruned; `uvcvideo`
presence is plausible but has not been inspected or verified. No hardware,
Raspberry Pi, Docker demo/context, OTA, SDK, image, release or public export was
changed. Real deployment and ConformU acceptance remain separate owner decisions.
