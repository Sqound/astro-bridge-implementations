# Changelog

All notable changes to Astro Bridge Implementations. Versions follow [Semantic Versioning](https://semver.org/).

## 1.1.0 — unreleased

### Added

- **Astro Bridge Setup 1.1.0** (`setup/`), versioned with this repository: device choice, release lookup with SHA-256 verification, guarded card writing through the pinned Raspberry Pi Imager engine, and Launch (DNS-SD or IPv4 discovery, web interface). macOS build signed and notarized; Windows and Linux builds unsigned.
- **Raspberry Pi 5 image** (two USB Wi-Fi adapters with automatic roles, shared hotspot, graphical boot splash compiled from source, QR welcome screen), built from the pinned Core wheel and public Alpine inputs, alongside the Raspberry Pi Zero W image; Pi 3/4 recipes as source.
- The local console is never left without a password: Pi 5 `root` receives the documented initial password on the first boot (kept on the data partition), Pi Zero W `root` is locked at every boot (administration through `bridge`).
- `astro-bridge-setup-manifest.json` in the release, so Setup offers the 1.1.0 images.
- Manual `release-builds.yml` workflow: unsigned Setup for Windows and Linux, the Pi Zero W image, OTA, Docker installers and OS sources as artifacts, without secrets; `tools/setup-manifest.py` and `tools/verify-image-content.py` (no SSH key, saved Wi-Fi secret or private key in an image).
- Camera host integration: USB/udev rules, optional ZWO SDK installer for the owner (no vendor binary is distributed), glibc compatibility notes and acceptance procedures.
- `SECURITY.md` and this changelog.

### Changed

- Pins Astro Bridge Core 1.1.0 (`core.lock.json`); Docker images and the Docker installer move to 1.1.0.
- The Setup helper resolves its private engine directory before the pre-launch check, so a symlinked cache or temporary directory no longer fails with "Invalid Imager engine location".
- README: verified-hardware table, downloads by goal and a Setup guide with screenshots.

## 1.0.1 — 2026-10-28

- Raspberry Pi Zero W SD image, native flashers, Docker installers, application-only OTA and corresponding OS sources. See the [1.0.1 release](https://github.com/Sqound/astro-bridge-implementations/releases/tag/v1.0.1).

## 1.0.0 — 2026-10-28

- First public release.
