# Add another implementation

[Documentation](README.md) / Platform contract

An implementation consumes Core as a versioned artifact. It supplies host capabilities and an operational lifecycle, not a fork of the web app.

## Required contract

| Area | Declare and test |
| --- | --- |
| Runtime | Host OS/architecture, Python and optional crypto libraries |
| Radios | Driver, bands, AP/station capabilities, allocation and recovery |
| Network services | NetworkManager interface, AP/DHCP hooks, owned routing/firewall resources |
| Observation | Counter/conntrack tooling, process identity and unknown-coverage behavior |
| State | Private persistent directories, ownership, atomic writes and backups |
| Identity and HTTPS | Local name resolution, clock evidence, certificate helper and trust procedure |
| Updates | Artifact integrity, safe maintenance, start/stop, health and rollback |
| UI capabilities | Unsupported host features stay unavailable |

`core.lock.json` pins wheel/source versions, HTTPS URLs and SHA-256. `tools/core-artifact.py` verifies before staging. Modified wheels and changed staged sources are rejected; builds must not silently fetch an unpinned branch or a sibling source checkout.

## Contribute a platform

1. Add a platform directory and a plan-first installer.
2. Document start, stop, logs, backup, update and recovery.
3. Declare the exact core artifact and required hardware/privileges.
4. Add helper, package and isolated networking tests.
5. Run a physical acceptance matrix and state remaining limits.

The reference Linux backend is shared Core code. Another OS needs a compatible backend/observation contract; a Docker wrapper alone does not provide Windows or macOS radio management.

Choose `ASTRO_BRIDGE_PLATFORM=native`, `docker` or `raspberry` as appropriate. `ASTRO_BRIDGE_CONNTRACK` can select a deployment-supplied absolute executable path; its version is still checked. Keep credentials and raw captures out of Git.
