# Raspberry Pi 5 image recipe

These recipes assemble the Alpine Linux image for the Raspberry Pi 5: Astro Bridge as a system service, automatic roles for two USB Wi-Fi adapters, a shared hotspot, a boot display, local DNS-SD announcement and the attached-camera profile.

`build-public.py` builds the release image from public inputs only, all pinned by SHA-256: the application from the Core wheel pinned by `core.lock.json`, the official Alpine Raspberry Pi base image and signed Alpine packages from the official CDN (`public-inputs.json`). It needs Python 3.11+, xz, mtools and e2fsprogs and writes a regular file, never a disk:

```sh
python3 raspberry/image/pi5/build-public.py --wheel build/core/astro_bridge_core-1.2.0-py3-none-any.whl \
  --core-lock core.lock.json --project raspberry --work build/pi5-work --output dist-pi5
python3 tools/verify-image-content.py dist-pi5/astro-bridge-1.2.0-alpine-aarch64-pi5.img.xz
```

The manual `release-builds.yml` workflow runs the same commands. Alpine removes superseded package versions from its CDN: when a pinned package disappears, the build stops and `public-inputs.json` must be refreshed after review.

No camera manufacturer file is part of the image: no library, no licence text, no access rule, ZWO included. Each manufacturer's driver is downloaded from its own site when the owner asks, either by Astro Bridge Setup on the computer before the card is written, or with Install in Configuration, Cameras. The build downloads nothing from a manufacturer. The image keeps time with chrony as soon as it has an uplink. No SSH key and no Wi-Fi profile are included; diagnostic SSH (`prepare-gphoto.py`) is opt-in with a public key that you supply. The first web and hotspot password is `12345678`; change it after the first sign-in. The local console account is `root`: it is locked from early boot, then receives the same initial password on the first boot (`console-account.py`, hash kept on the data partition, never in the image). Change it with `passwd`, then `rc-service astro-console-account save`. The boot splash renderer (`image/splash/graphic-splash.c`) is compiled statically by `image/splash/build-renderer.py` with gcc and musl from signed Alpine packages; it shows "Astro Bridge is booting" before the welcome screen.

Release 1.1.0 attaches `astro-bridge-1.1.0-alpine-aarch64-pi5.img.xz` with `pi5-manifest.json` and `PI5-SHA256SUMS`, written most easily with Astro Bridge Setup. A bench installation was checked for adapter switching, hotspot DHCP and Internet sharing; an image built by this recipe still needs a physical flash and first-boot check. See the [validation matrix](../../../doc/reference/validation.md).
