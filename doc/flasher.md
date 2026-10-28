# Prepare an SD card

[Documentation](README.md) / Native flashers

The flasher writes the complete Alpine Linux + Astro Bridge system to an SD card. Astro Bridge is preconfigured to start automatically as a service at boot.

Download the matching flasher from [release 1.0.1](https://github.com/Sqound/astro-bridge-implementations/releases/tag/v1.0.1). Each archive contains its runtime; Python is not required.

| Computer | Archive suffix |
| --- | --- |
| Windows 10/11 x64 | `windows-x64.zip` |
| macOS Apple Silicon | `macos-arm64.app.zip` |
| macOS Intel | `macos-x64.app.zip` |
| Linux x64, glibc 2.35+ | `linux-x64.tar.gz` |
| Linux ARM64, glibc 2.39+ | `linux-arm64.tar.gz` |

macOS bundles are built on macOS 15; older versions are unverified. Builds have no Developer ID/notarization or Windows publisher signature. Review the published checksum and use the OS's normal app-approval procedure if required.

## Flash

1. Extract the complete archive. Keep the executable and its runtime folder together.
2. Open **Astro Bridge Flasher**. On macOS, open the `.app` inside the extracted folder.
3. Download the current stable **Alpine standard** image, or select a local `.img.xz` with its matching checksum file.
4. Select the removable SD card. Check its name and capacity.
5. Confirm writing. The official checksum-pinned Raspberry Pi Imager engine performs the write and read-back verification, with the OS authorization prompt.
6. Eject the card, insert it into the original Raspberry Pi Zero W, and boot it. Follow [initial setup](raspberry.md).

You can instead download `astro-bridge-1.0.1-alpine-armhf-standard.img.xz` and use Raspberry Pi Imager's **Use custom** option. Flashing erases the selected card.

The compiled launchers are smoke-tested on their actual OS/architecture for argument parsing, GUI/TLS startup and local checksum verification. They are never allowed to write a physical drive during CI. Image verification and a successful write do not establish physical Wi-Fi acceptance.

## Licenses and provenance

Original launcher code is MIT licensed. Runtime and dependency notices accompany each bundle. Per-platform JSON records identify the actual architecture, source revision, verification scope and archive checksum. The image's OS source archive/manifest covers the signed package versions; see [licensing](licensing.md).
