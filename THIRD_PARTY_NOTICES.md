# Third-party notices

Original Astro Bridge deployment code, documentation and demonstration screenshots are licensed under [MIT](LICENSE). Downloaded packages, firmware and libraries retain their own terms.

| Component | Use | License/source handling |
| --- | --- | --- |
| conntrack-tools 1.4.9 | Linux activity observation | GPL-2.0-or-later; exact upstream source, COPYING and build flags retained under the private prefix |
| libnetfilter_conntrack 1.1.1 | Netfilter library for that executable | GPL-2.0-or-later; exact upstream source and COPYING retained |
| py-vapid | Optional notification signing | MPL-2.0 |
| http-ece | Notification encryption | MIT |
| cryptography | Cryptographic primitives | Apache-2.0 OR BSD-3-Clause |
| Alpine / Debian packages | OS, services and networking | Individual upstream/distribution package licenses |
| Linux kernel / Raspberry firmware | Locally built SD appliance | Kernel and per-file firmware licenses; exact redistribution requirements must be reviewed |

Pinned Netfilter sources and checksums are in `linux/build-conntrack.py`; the build installs their source archives, licenses and recorded flags under `share/doc/`. It does not replace the host's packaged libraries. Upstream release information: [conntrack-tools](https://www.netfilter.org/projects/conntrack-tools/downloads.html), [libnetfilter_conntrack](https://www.netfilter.org/projects/libnetfilter_conntrack/downloads.html).

The generated SD manifest records package origin/version/license metadata. Full-image redistribution needs the matching source/build material required by those components; the source recipe and app-only OTA in this release do not claim the entire OS is MIT licensed.
