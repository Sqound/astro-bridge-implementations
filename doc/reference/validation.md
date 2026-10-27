# Validation matrix

[Documentation](../README.md) / Validation

| Scope | Evidence | Remaining boundary |
| --- | --- | --- |
| Core | Python/JS suites, browser scenarios, fresh installed wheel | Does not establish every hardware/app combination |
| Helpers | 92 tests for overlay, persistence, HTTPS, credentials, artifact checks and OTA rollback contracts | Synthetic roots/certificates are not a new physical boot |
| Docker demo | Authentication, save, clean stop and volume persistence after recreation | No physical radios |
| Linux packets | Six installed-core suites: profiles, routing/NAT, shared hotspot/DNS, activity, ZWO and SynScan | No full host D-Bus/systemd/radio acceptance |
| Web Push crypto | 20 tests in the final Linux image | No guarantee of visible phone delivery |
| Raspberry SD | Complete local build, compressed-image/overlay/geometry/package inspection | Fresh SD boot and exact binary source-distribution packaging pending |
| Existing Pi OTA | App-only 0.6.35 → 1.0.0; configuration, SSH/TLS/push identity and network roles preserved | No deliberate physical rollback or kernel reboot |
| Physical management UI | Exact HTTPS assets/shell/SW/OpenAPI and restored configuration; mobile/desktop layouts | Native telescope apps/phone Internet are separate tests |

The existing-Pi OTA transaction used the unchanged OS updater, checksum verification and repeated real version/build/network health responses. The final release adds documentation/screenshots and a bundled MIT notice; runtime protocol code is unchanged from the tested application candidate.

The Docker base is pinned Debian 13. Private-prefix conntrack 1.4.9/libnetfilter_conntrack 1.1.1 satisfy the collector contract; native mode checks nftables >=1.1.3. The older Debian 12 tools were not accepted after an unsupported collector version and a classifier-replacement client crash appeared in validation.

Before claiming a new deployment/model, verify actual adapters, native control/preview, selection in both directions, viewing protection/release, persistence, HTTPS/PWA trust and notification display/click. Guiding and autonomous acquisition need their own explicit evidence.

No exact device identifiers, personal networks, credentials or private captures are part of the public test record.
