# Licensing and binary distribution

[Documentation](README.md) / Licensing

## Project license

The original Astro Bridge deployment source, documentation and synthetic screenshots are **MIT licensed**. The root [LICENSE](../LICENSE) contains the full terms and contributor copyright notice.

## Third-party components

Linux distributions, kernels, firmware and downloaded libraries are separate works. Their licenses and corresponding-source obligations are not changed by the project's MIT license.

| Distribution | License handling |
| --- | --- |
| Project source archive | Full MIT text included |
| Core wheel | MIT text included in distribution metadata and app notice |
| App-only OTA | App MIT notice included in the payload; no OS/crypto runtime bundled |
| Local Docker build | Upstream package notices retained; pinned Netfilter source archives/licenses included in the private prefix |
| Complete SD image | Exact signed-package commits, APKBUILD/patch/install files and checksum-verified upstream sources accompany the release |

The SD manifest records package provenance and licensing metadata; that record alone is not a substitute for distributing required corresponding source. The release pairs the compiled SD image with this exact source closure. Common license texts are also installed in the image. Individual component licenses remain authoritative.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Preserve dependency notices when creating downstream images or installers.

## Downloading the image sources

Download every asset listed in `OS-SOURCE-SHA256SUMS` and verify those checksums first. If the source archive is split into numbered parts, concatenate them in order before extracting:

```sh
sha256sum -c OS-SOURCE-SHA256SUMS
cat astro-bridge-1.0.1-os-sources.tar.gz.part* > os-sources.tar.gz
tar -xzf os-sources.tar.gz
```

An unsplit `.tar.gz` can be extracted directly. `OS-SOURCE-MANIFEST.json` links each package origin to its exact signed-package recipe commit and archive checksum. Image assembly scripts and the pinned Core dependency are in the implementation source archive from the same release.
