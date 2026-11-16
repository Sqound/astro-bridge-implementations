# Alpine vendor camera SDK compatibility

**ZWO ASI SDK 1.41 loaded on Alpine 3.24 under ARM1176 (ARMv6) and Cortex-A53
(AArch64) emulation.** Direct `ctypes.CDLL` returned SDK version
`1, 41, 0, 0` and zero cameras on both. Use direct, eager, local loading with
gcompat installed. This proof covers two no-device API calls. USB transfers and
served-camera operation require a physical Pi 3B bench.

QHY execution remains gated by unresolved licence terms. The inspected ARM32
SDK requires ARMv7. Its minimum ISA rules out using that build for ARM1176
validation. The metadata-only check found ELF32 ARM, CPU arch 10 (ARMv7) and VFP arguments
1 for `libqhyccd.so.26.7.28.15`, and ELF64 AArch64 for
`libqhyccd.so.26.7.28.10`. The ARM64 binary also remains unexecuted. Archive identities,
permitted uses and licence excerpts are in the dependency review.

## Reproducible no-device evidence

Manual workflow run `41937630793`, commit
`be9002c42f1b2a329b9839adf583fcbc330d1e28`, used Ubuntu 24.04 runners,
QEMU user emulation with explicit CPU models, and the Alpine 3.24 multi-platform
image at `sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a`.
Each container verified the same 112,224,701-byte ZIP with SHA-256
`c6d59bf2de5b807f13d60d10b035e10dff31aeaa43391c6cd4782242126775ec`
before copying its reviewed regular member into disposable storage. No USB
device nodes were mapped. USB sysfs was masked and core dumps disabled. Neither
SDK installers nor udev rules were executed. The workflow retains no vendor
files in Git, artifacts or caches.

The workflow runs only on manual dispatch at the reviewed ref. The
standard-library ELF inspector reads ARM attributes, NEEDED and versioned
symbols without executing code. For runtime evidence, the runner starts one
timeout-bounded child per strategy in a USB-isolated container, suppresses
native output and emits structured fields. It calls only `ASIGetSDKVersion()`
and `ASIGetNumOfConnectedCameras()`. The QHY probe has fake-backed tests for
`InitQHYCCDResource()`, `ScanQHYCCD()` and `ReleaseQHYCCDResource()`. Its
manifest gate prevents vendor downloads and execution in CI.

| Target and inspected member | Strategy | Version | Cameras | Load ms | RSS delta, bytes |
|---|---|---|---:|---:|---:|
| ARM1176, `lib/armv6/libASICamera2.so.1.41` | Direct | 1, 41, 0, 0 | 0 | 24.527 | +2,682,880 |
| ARM1176 | Preload libgcompat | 1, 41, 0, 0 | 0 | 26.105 | +2,670,592 |
| ARM1176 | gcompat loader starts Python | Child exited 1 | — | — | — |
| Cortex-A53, `lib/armv8/libASICamera2.so.1.41` | Direct | 1, 41, 0, 0 | 0 | 19.326 | +3,571,712 |
| Cortex-A53 | Preload libgcompat | 1, 41, 0, 0 | 0 | 20.604 | +3,604,480 |
| Cortex-A53 | gcompat loader starts Python | Child exited 1 | — | — | — |

The ARMv6 job reported `armv6l` and ELF32 ARM, with CPU arch 7 (ARMv6KZ) and
VFP arguments 1 (VFP registers). The ARM64 job reported `aarch64` and ELF64
AArch64. The third strategy tried starting musl Python through the glibc loader
name. The log contains its exit code only. Direct loading needs no process-wide
preload, wrapper or change to the service environment. The measurements are
single emulator samples, unsuitable for estimating Pi boot time, memory or CPU.

Earlier run `41937102376` reached the SDK API stage after eager loading with
direct and preload on both targets. Its harness rejected comma-separated version
strings. Commit `ea5fcedb229ae1bc592f32ec272e297fa3f3e4bb` corrected the
bounded reader and validated the documented numeric format. Only the second run
produced the version and zero-device results.

## Provider and symbol analysis

The SDK requests `libc.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libstdc++.so.6`
and `libusb-1.0.so.0`. All five were available. The static check covered the
recursive NEEDED closure of the SDK and Python interpreter plus gcompat. It also
included musl, libucontext and libobstack. Every enumerated provider ELF was
inspectable. There were zero missing strong names. The full per-symbol lists are
in the external ARMv6 and AArch64 probe result files.

The following are all the undefined names without a provider. Each is weak and
unversioned. Direct eager loading and both no-device calls succeeded in their
absence. **No shim is needed for those calls.** The feasibility judgments below
distinguish real implementations from a stub that would merely make a weak
symbol appear present.

| Missing name | Target | Role and shim assessment |
|---|---|---|
| `_ITM_registerTMCloneTable` | Both | Optional GCC transactional-memory registration hook. A no-op cannot register clone tables. Leave absent. |
| `_ITM_deregisterTMCloneTable` | Both | Paired deregistration hook. A no-op cannot maintain the registration lifetime. Leave absent. |
| `_Jv_RegisterClasses` | Both | Optional legacy Java runtime class-registration hook. A tiny stub cannot provide class registration. Leave absent. |
| `__gmon_start__` | Both | Optional profiling startup. A stub would skip profiler initialization and cleanup. Leave absent. |
| `__pthread_key_create` | ARM64 only | GCC can use its presence to detect threading. A TLS-key wrapper may be possible after an ABI and concurrency review. Even a callable alias changes the optional-feature test. Leave absent and exercise real threaded capture on Pi 3B. |

GCC's startup source describes the conditional registration calls. The glibc
profiling entry point initializes the profiler and registers cleanup. GCC's POSIX
thread support tests an internal glibc symbol address. These explain plausible
roles for the weak references without claiming knowledge of every proprietary SDK
code path. [GCC startup](https://github.com/gcc-mirror/gcc/blob/releases/gcc-5.5.0/libgcc/crtstuff.c)
· [glibc profiling startup](https://raw.githubusercontent.com/bminor/glibc/glibc-2.39/csu/gmon-start.c)
· [GCC thread detection](https://github.com/gcc-mirror/gcc/blob/releases/gcc-12.4.0/libgcc/gthr-posix.h)

A further 237 versioned imports on ARMv6 and 232 on AArch64 had a provider by
name without a matching exported version label. These include GLIBC, GLIBCXX
and CXXABI labels. Musl resolves the default version where available. The
observed eager loads and API calls succeeded with these label differences.
Device I/O, error paths and ABI behavior beyond these calls still require
testing. [Musl dynamic-linker behavior](https://wiki.musl-libc.org/functional-differences-from-glibc.html#Symbol_versioning)

## Proposed package change

The recorded image baseline already contains libstdc++ and libgcc
`15.2.0-r5`, libusb `1.0.30-r0` and libcrypto3 `3.5.9-r0`. The probe container
used those versions, musl `1.2.6-r3` and Python `3.14.10-r1`.
Installing **gcompat 1.1.0-r4** added **musl-obstack 1.2.3-r2** and
**libucontext 1.5.1-r0** to the container. Only `gcompat` needs to be requested
explicitly. APK resolves its dependencies.

| Target | /lib before → after (KiB) | /usr/lib before → after (KiB) | Combined increase | Rounded APK total |
|---|---:|---:|---:|---|
| ARMv6 | 788 → 868 | 46,536 → 46,560 | 104 KiB | 44.5 → 44.6 MiB |
| AArch64 | 852 → 992 | 52,512 → 52,716 | 344 KiB | 50.6 → 50.9 MiB |

The columns report allocated directory blocks from `du -sk` around the actual
installation. The run did not measure compressed media growth or assembled-image
size. The proposed media profile and boot overlay world both request `gcompat`
so diskless boot can install it. The common recipe also serves Zero W, whose
connections/telemetry-only policy continues to apply. Review this package scope
before accepting or building an image. The recipe includes no vendor
SDK. Alpine remains the target and no technical fallback is required.

## Platform integration and Pi 3B procedure

The trusted platform declares a single absolute path to the owner-installed,
versioned SDK, after checking its integrity, ABI, permissions and tier.
The generic `load_platform_sdk(path)` applies `RTLD_NOW | RTLD_LOCAL` and
returns the handle to a reviewed camera driver in its own process.
The [ASI adapter and owner installer](CAMERA-ZWO.md) implement this boundary. The implementation and probe have
no path-selection input from clients. See the [USB contract](CAMERA-USB.md).

1. After the package change and the owner-run installer are approved, record the Pi 3B model,
   kernel, userland ABI, image/core identity, installed APK versions and SDK hash.
   Select the SDK using the installed userland ABI. Use the ELF inspector to
   confirm class, machine, ARM attributes where present, and NEEDED libraries.
2. With no camera attached and the reviewed ASI adapter, repeat direct version
   and zero-camera enumeration. Capture real load time, RSS and CPU, and record
   the exact version. Run this through the approved driver diagnostic. Keep the
   disposable CI harness's no-device safeguards in place.
3. Attach the owner's camera to an adequately powered USB path. Check device-node
   permissions, detection, version and a bounded exposure with the adapter.
   Verify frame format, binning, abort/reconnect, unplug and power-fault recovery.
   Record real RSS/CPU over repeated threaded capture and concurrent network
   telemetry. Keep raw camera payloads out of reports.
4. Record pass/fail with the actual model, image and SDK identity. USB timing,
   physical camera behavior, long-session stability and real resource use remain
   **documented, not verified on hardware** until this bench runs. Zero W stays a
   connections/telemetry appliance. Pi 4 needs its own owner decision.
