# Astro Bridge Implementations

**Run Astro Bridge on a Raspberry Pi, a Linux computer, or Docker.**

[![Implementation checks](https://github.com/Sqound/astro-bridge-implementations/actions/workflows/checks.yml/badge.svg)](https://github.com/Sqound/astro-bridge-implementations/actions/workflows/checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-d6a253.svg)](LICENSE)

This repository provides deployment recipes for [Astro Bridge Core](https://github.com/Sqound/astro-bridge-core). The application is consumed as an exact, SHA-256-pinned release; implementations do not maintain a second copy of its source.

![Astro Bridge network settings](doc/images/networks.png)

*Actual application in demo mode, using synthetic networks and equipment.*

## Choose your deployment

All three options run the same application:

| Option | Included | Installation |
| --- | --- | --- |
| **Complete Raspberry Pi image** | Alpine Linux, Astro Bridge, drivers and system services | Flash an SD card for the original Raspberry Pi Zero W; Astro Bridge starts automatically at boot |
| **Core only** | The application, web interface, API and hardware plugins | Install on an existing system; real Wi-Fi control also needs Linux host integration |
| **Docker** | Core and its container dependencies | Run on an existing Docker host: demo on macOS/Windows, or real networking on a compatible Linux host |

The Raspberry download is a complete operating-system image. Core and Docker install the application on a computer that already has an operating system. See [installation options](doc/deployment-options.md) for the differences, or the [validation matrix](doc/reference/validation.md) for hardware evidence.

One adapter is needed per simultaneous Wi-Fi role. A private Wi-Fi uplink, hotspot and one telescope uplink normally use three adapters. Read [platform requirements](doc/platform-contract.md) before assigning existing host interfaces.

## Download ready-to-use files

Use the [1.0.1 release](https://github.com/Sqound/astro-bridge-implementations/releases/tag/v1.0.1).

| Goal | Download |
| --- | --- |
| Complete Raspberry Pi Zero W system: Alpine Linux + Astro Bridge | [Native flasher for Windows, macOS or Linux](doc/flasher.md), or the `.img.xz` image for Raspberry Pi Imager |
| Deploy Docker on a Linux PC | `install-docker.sh`; plans first, installs dependencies and starts the chosen deployment with `--apply` |
| Run the Docker demo on macOS/Windows | `install-docker.py`; Docker Desktop setup may require a first-run action or OS reboot |
| Update an existing compatible appliance | Alpine armhf application-only OTA and its manifest |

Verify downloaded files with the release's `SHA256SUMS`. The [Raspberry guide](doc/raspberry.md) and [Docker installer guide](doc/docker.md) give the exact steps.

## Try Docker without hardware

Install Docker, clone this repository, then run:

```sh
python3 tools/core-artifact.py fetch
python3 docker/build.py --wheel build/core/astro_bridge_core-1.0.1-py3-none-any.whl --target demo
docker compose -f docker/compose.demo.yaml up -d
```

Open **http://127.0.0.1:8787**. Password: **`demo-telescope-2026`**.

The demo runs without host network capabilities and preserves settings in a named volume. `docker compose -f docker/compose.demo.yaml down` stops it without deleting that volume.

Standard Docker Desktop setups on Windows/macOS support the demo. Physical Wi-Fi control additionally requires adapters and host services inside the Linux VM; USB forwarding is not configured or validated by this installer. See [Docker deployment](doc/docker.md).

## Deploy on a dedicated host

| Task | Guide |
| --- | --- |
| Prepare/build a Pi Zero W appliance | [Raspberry](doc/raspberry.md) |
| Use Docker with physical Linux Wi-Fi | [Docker Linux](doc/docker.md#real-networking-on-linux) |
| Install native Linux host integration | [Native Linux](doc/linux.md) |
| Upgrade and preserve settings | [Updates and recovery](doc/updates.md) |
| Add a new platform | [Platform contract](doc/platform-contract.md) |

The Linux installer prints a plan unless `--apply` is supplied. Real networking controls host services and interfaces: use a dedicated, trusted network host with a recovery path.

## Use Astro Bridge

<img src="doc/images/dashboard-mobile.png" alt="Mobile Astro Bridge dashboard" width="360">

Configure private Wi-Fi and the hotspot in **Networks**. Scan and add equipment in **Settings → Telescopes**. Manufacturer-app paths depend on the selected family; Vespera may require a router route when the phone remains on private Wi-Fi.

The [Core user documentation](https://github.com/Sqound/astro-bridge-core/tree/main/doc) covers network modes, app-triggered switching, protection, PWA installation and notifications. To add hardware, follow the [declarative plugin tutorial](https://github.com/Sqound/astro-bridge-core/blob/main/doc/plugins/README.md).

## Releases and reproducibility

`core.lock.json` records the core wheel and source-archive version, URLs and SHA-256 digests. Builders verify the artifact before extraction. Releases include implementation source, the application-only Alpine OTA, a Raspberry Pi Zero W SD image, native flashers, Docker installers, manifests and checksums.

The SD image is accompanied by exact signed-package recipe revisions and checksum-verified upstream source archives. Image structure, package signatures and application identity are checked during construction. A freshly flashed physical board remains a separate acceptance step. OS packages and firmware retain their own licenses.

Core wheel and source exports are deterministic. The SD builder records base/aports inputs, package closure, geometry and build identity; ephemeral index signing and distribution timestamps prevent a byte-reproducibility claim for the complete image.

## Contribute

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [platform contract](doc/platform-contract.md). Application/protocol changes belong in Core. A deployment PR should pin a tested core, provide installation/start/stop/upgrade/recovery steps and state its actual hardware evidence.

## License

**Astro Bridge deployment code and documentation are licensed under the [MIT License](LICENSE).** Operating-system packages, firmware, Netfilter libraries and optional crypto dependencies retain their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
