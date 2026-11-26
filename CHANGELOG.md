# Changelog

All notable changes to Astro Bridge Implementations. Versions follow [Semantic Versioning](https://semver.org/).

## 1.2.0 — 2026-11-27

### Added

- **Astro Bridge Setup**: the same panels for every image that announces them (Raspberry Pi 5 and Pi Zero W): private Wi-Fi, an optional bridge password and optional camera drivers. The password is placed in the first-boot zone of the card and becomes, at the first boot, the web password, the hotspot key and the Linux password, then the zone is erased. Ticked camera drivers are downloaded from each manufacturer's official address before anything is written, checked against the bridge's own catalog, and placed after the image; the bridge installs them at its first boot. A driver that cannot be downloaded can be left out and added later from the bridge interface. On the Pi Zero W the camera panel says the hardware does not support it.
- **Raspberry Pi Zero W image**: reads the same first-boot zone as the Pi 5 (version 2): private Wi-Fi, bridge password and the SSH rule from the first boot.
- **Raspberry Pi 5 image**: gPhoto2 packages (free licences) on the data partition, off by default.
- **Camera drivers**: Player One, QHYCCD, Atik and Moravian cameras have adapters in Core 1.2.0 (documented, not verified on hardware). Setup keeps the "not verified on hardware" mention beside each of these manufacturers.
- `drivers-catalog.json` is published beside the images, taken from the Core wheel, and recorded in the new `astro-bridge-setup-manifest-2.json` (format 2, read first by Setup 1.2.0; format 1 stays published for earlier Setup releases).

### Fixed

- **Astro Bridge Setup, Launch**: the search for bridges runs continuously from the moment the page opens, with a spinner and Stop or Resume, and each bridge appears as soon as it is found. A bridge can also be added by its address in a dialog. Setup keeps the bridge session while its window is open and never writes the password, the step indicator lets you reach Launch from any step, and the header and the step indicator stay at the top of the window.
- **Astro Bridge Setup, Launch**: Add is offered for a mount only, and the cameras the bridge found are listed. An added Wi-Fi mount is connected on a free Wi-Fi adapter of the bridge right away, and Setup says when no adapter is free.
- **Raspberry Pi 5 image**: the system log keeps 2 MB per file and four older files, kernel messages included, and `/var/log` lives on the data partition. The logs of the current boot and of the three before it survive a restart or a power cut. With less than 256 MB free on that partition, the logs stay in memory as before.
- **Astro Bridge Setup**: the camera drivers panel is open from the start, and Back and the next step stay at the bottom of the window while the content scrolls.
- **Raspberry Pi 5 image**: the hotspot has the same name form as on every other image, `astro-bridge-` followed by six lowercase hexadecimal characters. 1.1.0 wrote `Astro-Bridge-` followed by six capitals. A bridge already set up keeps its name, a card written again with 1.2.0 gets the new form.
- **Astro Bridge Setup** and the guides name the hotspot as it is advertised, `astro-bridge-` followed by its suffix, and no longer mention an older network name that no current release uses.
- **Raspberry Pi 5 image, ZWO ASI cameras**: ZWO is offered like every other camera manufacturer. No manufacturer file, licence text or access rule is part of the image. The driver is downloaded from ZWO's site when you tick it in Astro Bridge Setup (on your computer, before the card is written) or press Install in Configuration, Cameras, and it stays subject to ZWO's terms. The image no longer carries the SDK installer script: 1.1.0 carried the generic one, which refused the Pi 5.
- **SDK installer**: the vendor now serves SDK 1.42 behind the same address, so the pinned 1.41 archive could no longer be installed on any host. The installer accepts a known build, identified by the library and licence it contains, and installs into a versionless folder. An earlier 1.41 installation keeps working.
- **Raspberry Pi 5 image**: the clock is set by network time (chrony) as soon as an uplink exists; before that, from the signed-in browser. Without it the clock stayed in 1970 and every HTTPS download failed. Neither the Wi-Fi nor the interface waits for the clock.
- **Astro Bridge Setup** offers the private Wi-Fi step (network, password, country) for the published Raspberry Pi 5 images, which already read it on their first boot. The country starts from the computer's region. The writing helper still checks the image's first-boot Wi-Fi slot before writing.
- **Raspberry Pi 5 image**: the welcome screen never gives up silently. When the bridge cannot be reached it says why in plain words and what to do, keeps checking, and shows the codes again when the hotspot returns. It shows the codes for a hotspot running on another free adapter and the address of a confirmed wired connection.
- **Raspberry Pi 5 image**: SSH starts at every boot (1.1.0 started no SSH server, and one started by hand was gone after a restart, with a new host key each time). The host key is kept on the data partition. `root` signs in with a public key placed in `/var/lib/wifi-bridge/ssh/authorized_keys`, or with its password once the owner has replaced the initial one; the documented initial password never opens a network login. The standard `ssh-copy-id` works: `/root/.ssh/authorized_keys` points to that kept file.
- **Appliance images** (Raspberry Pi 5, Pi Zero W, Raspberry Pi OS installer): each interface answers address resolution for its own address only (`net.ipv4.conf.all.arp_ignore=1`). A bridge on one network by cable and by Wi-Fi could be reached through the cable at its Wi-Fi address, where no relayed instrument port answers.
- **Raspberry Pi 5 image**: the onboard Ethernet port is initialized for NetworkManager at boot, so the wired profile can start; the image carries the appliance marker that enables its DNS-SD announcement.

## 1.1.0 — 2026-11-17

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
