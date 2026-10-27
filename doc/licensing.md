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
| Complete SD image | Review exact package/firmware licenses and provide required matching source/build material before redistribution |

The SD manifest records package provenance and licensing metadata; that record alone is not a substitute for distributing required corresponding source. This release provides the SD recipe for local builds, not a downloadable assembled image.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Preserve dependency notices when creating downstream images or installers.
