# Validation matrix

[Documentation](../README.md) / Validation

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
