# Validation matrix

[Documentation](../README.md) / Validation

## Release 1.1.0

Release 1.1.0 carries the Raspberry Pi Zero W and Raspberry Pi 5 images and Astro Bridge Setup. Hardware evidence by family:

| Family | Implemented | Tested in simulation | Verified on hardware |
| --- | --- | --- | --- |
| Raspberry Pi 5 host, hotspot, two USB Wi-Fi adapters | Yes (image in implementations 1.1.0) | Yes | **Yes, partly:** automatic adapter roles in under 6 s, IPv4 forwarding, hotspot DHCP, DNS and Internet, Setup Launch found the bridge. Offline fallback hotspot not yet checked |
| Raspberry Pi Zero W host | Yes (image in implementations 1.1.0) | Yes | **Partly:** 600 s idle run and a TCP relay test (147/147 replies). Camera hosting is not supported on this board. A full telescope session, hotspot loss/recovery and phone discovery are not yet checked |
| Raspberry Pi 3 B, Pi 4 | Yes | Yes | No |
| Vespera (Wi-Fi, routed original address) | Yes | Yes | **Partly:** switching and the manufacturer-app path. Two isolated Vespera, per-tab switching and night reservation: no |
| Seestar (relays, station profile) | Yes | Yes | **Partly:** automatic switching to and from another telescope (about 40 s) with notifications. Bridge `scan_iscope` probe and station mode: no |
| DWARF (relays, operation state) | Yes | Yes | **Partly:** manufacturer-app discovery observed. Operation-state reading during a capture: no |
| ZWO Wi-Fi mounts (TCP 4030) | Yes (read-only identification, relay, protection) | Yes | **Partly:** model read through the relay. Control, guiding, automatic switching: no |
| SynScan Wi-Fi mounts (UDP 11880) | Yes | Yes | No |
| Celestron NexStar over USB serial (TCP relay) | Yes | Pseudo-terminal tests | No |
| USB cameras over Alpaca (ZWO ASI) | Yes | Yes | **Partly:** ASI220MM, two 0.2 s exposures, 16-bit FITS (1920 × 1080 direct, 640 × 480 through a hub); full frame through a hub failed with USB resets |
| USB cameras (UVC, gPhoto) | Yes | Yes | No |
| Native client contract 1.2 (pairing, leases, events, telemetry) | Yes | Replay scenarios and loopback mock | No third-party client verified against a physical bridge |
| Astro Bridge Setup 1.1.0 (macOS, Windows, Linux) | Yes | Unit, Rust and browser tests with mocks | **Partly:** Launch found a Pi 5 bridge on a private network (macOS). Writing a card from the published 1.1.0 images: not yet checked; Windows and Linux builds not checked on a computer |
| Docker demo | Yes | Yes | Demo verified in a Linux VM on macOS. Physical USB Wi-Fi inside Docker: no |

**Documented is not verified.** Only the last column is hardware evidence; "partly" lists exactly what was observed. A matching Wi-Fi name is a hint, not proof of compatibility. Live USB images and Celestron Origin are not part of this release. A freshly written 1.1.0 image still needs its first-boot check on each board.

## Release 1.0.1

| Scope | Evidence | Remaining boundary |
| --- | --- | --- |
| Core | 392 Python tests (two optional crypto skips), 18 JavaScript tests and a fresh installed wheel | Does not establish every hardware/app combination |
| Platform helpers | 97 tests for overlay, persistence, HTTPS, credentials, artifacts, OTA rollback and Docker installer guards | Synthetic roots are not a physical boot |
| Docker installer | Verified release archive, actual Compose build/start, authentication, saved configuration and project cleanup | Desktop Docker provides demonstration mode only |
| Linux packets | Six installed-core suites: profiles, routing/NAT, shared hotspot/DNS, activity, ZWO and SynScan | No full host D-Bus/systemd/radio acceptance |
| Compiled flashers | Native Windows x64, macOS x64/ARM64 and Linux x64/ARM64 builds; GUI/TLS self-test and image-checksum smoke test on each runner | No physical SD write; no publisher signing or notarization |
| Raspberry image | Complete ARM image build, compressed-image/overlay/geometry/package inspection and corresponding-source collection | Fresh SD boot and physical radio behavior require hardware validation |

The flashers bundle Python and their dependencies. Both macOS applications also pass local ad-hoc signature verification. An ad-hoc signature does not establish publisher identity or Apple notarization.

The Core credential correction blocks repeated authentication attempts until credentials change or the operating system reboots. Automated coverage distinguishes confirmed authentication failures from missing networks and timeouts. A new physical invalid-password test has not been performed for this release.

## Earlier hardware evidence

The existing Raspberry Pi received the app-only 0.6.35 to 1.0.0 OTA update. Configuration, SSH/TLS/push identity and network roles were preserved and checked using repeated version/build/network responses. That evidence applies to the earlier OTA transaction, not a fresh boot of the 1.0.1 SD image. No physical 1.0.1 OTA update or SD write is claimed here.

Earlier validation also covered 20 Web Push crypto tests in the Linux image and management-interface checks. Visible phone delivery, notification clicks and native telescope applications remain separate end-to-end checks.

## Deployment acceptance

The Docker base is pinned Debian 13. Private-prefix conntrack 1.4.9/libnetfilter_conntrack 1.1.1 satisfy the collector contract; native mode checks nftables >=1.1.3.

Before claiming a new deployment/model, verify actual adapters, native control/preview, selection in both directions, viewing protection/release, persistence, HTTPS/PWA trust and notification display/click. Guiding and autonomous acquisition need their own explicit evidence.

No exact device identifiers, personal networks, credentials or private captures are part of the public test record.
