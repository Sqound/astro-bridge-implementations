# Raspberry Pi Zero W

[Documentation](README.md) / Raspberry

The Raspberry download is a **complete Alpine Linux operating-system image** with Astro Bridge, drivers and system services already installed. Astro Bridge starts automatically as a service at boot; no separate Core installation is needed.

Flash the `.img.xz` to an SD card for the original Raspberry Pi Zero W. For an existing computer, compare [Core and Docker installation](deployment-options.md) instead.

## Hardware and network roles

Use an original Pi Zero W with a suitable SD card, power supply, hub and Linux-supported Wi-Fi adapters. The built-in radio can serve one role; add adapters for simultaneous private Wi-Fi, hotspot and telescope uplinks.

![Network roles and selected connection mode](images/networks.png)

The implementation uses diskless Alpine, OpenRC and an **ASTRO_DATA** persistent partition. The core is staged from the exact locked wheel; drivers, services and recovery helpers belong to the image.

## Download and flash

Use the [native flasher](flasher.md), or download the release `.img.xz` and `ALPINE-STANDARD-SHA256SUMS` for Raspberry Pi Imager. Exact OS source archives and a package/recipe manifest accompany the image.

## Build a local SD image

```sh
python3 tools/core-artifact.py fetch
python3 tools/build-raspberry.py \
  --wheel build/core/astro_bridge_core-1.1.0-py3-none-any.whl \
  --stage build/pi-release --build
```

Use a new stage directory. The builder needs Docker, Internet and at least 8 GiB free. It uses a pinned base/aports commit, records package closure and checks the compressed image, boot files, application identity and partition geometry.

Output is under `build/pi-release/dist/standard-1.1.0/`. The builder checks geometry, filesystems, package signatures, firmware/modules and exact application identity. Fresh physical boot/radio acceptance is reported separately.

## Flash and first access

1. Verify the image against `ALPINE-STANDARD-SHA256SUMS`.
2. Use the staged flasher's help/inspection workflow and explicitly select the removable target. Flashing erases that target.
3. Keep the generated access information private. Boot the board and allow initial provisioning to finish.
4. Join the configured hotspot and open **http://192.168.77.1**.
5. Change initial access codes, select adapters, and follow the [Core setup guides](https://github.com/Sqound/astro-bridge-core/tree/main/doc).

The current image uses the documented initial code **12345678**; change web, hotspot and system-account credentials independently before normal use. Existing installations retain their credentials during an application update.

## Raspberry Pi 5 first boot

In Astro Bridge Setup, the optional private Wi-Fi step stores your network, its password and your country on the card for the first boot only: the first boot moves them to the data partition (the password as a derived WPA key) and erases them from the card's reserved area. The onboard radio then joins your network and a free USB Wi-Fi adapter carries the hotspot.

Without private Wi-Fi, the onboard radio carries the hotspot. If it stops, the bridge restarts it, and moves it to a free USB Wi-Fi adapter when the onboard radio keeps failing. The welcome screen shows two codes (Wi-Fi, then web interface) while the hotspot works, and otherwise says why in plain words. An Ethernet cable to your router also gives access: the welcome screen then shows the bridge's address.

To check from the local console (`root`): `rc-service wifi-bridge-ap status`, `tail /var/log/wifi-bridge.log /var/log/wifi-bridge-ap.log`, `iw dev`, `nmcli device`.

## Local console and SSH

No image contains a password or password hash: accounts receive the documented initial password `12345678` on the first boot, and every new password is kept on the data partition.

- **Raspberry Pi Zero W:** sign in as `bridge` (initial password `12345678`, administrator through `sudo`), on the HDMI console or by SSH. `root` is locked at every boot. Change the password from the web interface (Settings) or with `passwd`, then keep it with `sudo /usr/bin/python3 /usr/libexec/astro-bridge-persist save-password`.
- **Raspberry Pi 5:** the console administrator is `root` (initial password `12345678`). SSH (port 22) starts at every boot with a host key kept on the data partition. It accepts `root` with a public key placed in `/var/lib/wifi-bridge/ssh/authorized_keys`, and with the `root` password only once you have replaced the initial one: the initial password never opens a network login. `ssh-copy-id root@<bridge address>` adds a key: `/root/.ssh/authorized_keys` points to that kept file, so the key stays after a restart. Change it with `passwd`, then keep it with `rc-service astro-console-account save` (also done at a clean shutdown). If its saved state is unusable, `root` stays locked.

Change the initial password on the first sign-in, and keep the board physically secure: the local console is the recovery path.

## Upgrade an existing installation

An application-only OTA does **not** require reflashing. It preserves configuration and uses the separately installed OS updater for health checks and rollback. An upgrade from an existing 0.6.35 diskless installation to the locked 1.0.0 application was verified; that does not prove booting this newly assembled SD image.

See [updates and recovery](updates.md). OS, kernel, firmware or missing helper prerequisites require separate platform work.
