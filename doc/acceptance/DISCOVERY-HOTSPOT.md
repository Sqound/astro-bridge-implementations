> Commands use the verified staged project described in [candidate preparation](../candidate-preparation.md). Hardware steps require separate owner approval.

# Native discovery on home and hotspot acceptance

Status: **documented, not verified on hardware**. Portable tests validate XML,
TXT bounds, atomic publication, runtime capabilities, opaque identity, ingress
classification and command decisions. They do not validate client resolution,
multicast isolation or Avahi timing on the Raspberry Pi.

Use one iPhone and one Android phone, an authorized bridge, and a separate
home-network client. Record app/OS versions, bridge version, radio count and test
conditions without network names, addresses, device identifiers or raw payloads.
Obtain the owner's approval before a home-network change that interrupts access.
No telescope commands or manufacturer apps are needed.

1. Enable the bridge hotspot. On each phone disable mobile data, VPN and Wi-Fi
   auto-switch/Internet fallback. Join **only** the bridge hotspot, accept the OS
   warning about no Internet, and verify it remains joined. Disconnect other
   network paths. Enable the app's local-network permission when requested.
2. Begin a monotonic stopwatch at hotspot start. Browse `_astrobridge._tcp.local`
   with an iOS DNS-SD browser (for example Discovery) and an Android NSD browser.
   An Android terminal with Avahi can use `avahi-browse -rt _astrobridge._tcp`.
   Record seconds until the service first appears, seconds until resolution,
   and whether the resolved HTTP endpoint responds with public `/api/v1/info`.
   A service listing alone is insufficient.
3. Check TXT contains `api`, `api-version`, `contract`, `product`, `version`, `id`,
   `name`, `caps`, and `https` only with a ready HTTPS listener. Check
   `contract=1`, `id` has 16 lowercase hex digits, name is at most 63 UTF-8 bytes,
   each string at most 255 bytes and the total with length octets at most 1300.
   Record completeness and equality of TXT id/name/caps with `/info`, **not the
   values**. `/info.request_path` must be `hotspot`. Compare the runtime-enabled set from `/info.capabilities` with TXT `caps`.
   Optional services depend on this platform and its configuration.
4. Keep both phones joined only to the hotspot. From a separate authorized
   management path, change the home connection (join, leave, then rejoin) while
   the hotspot remains on its dedicated radio. Time withdrawal/reappearance and
   resolution after each change. Confirm the hotspot service stays usable and
   complete, `/info.request_path` remains `hotspot`, the opaque id stays equal,
   and the journal records `announcement_updated` with role names only. Record
   missing/duplicate/stale entries, client cache behavior and any timeout.
5. Stop/start the hotspot using a separate management connection; repeat timing
   on both phones. If a transition fails to preserve access, stop and retain
   only redacted results. Do not bypass radio reservations or occupied-AP guards.
6. Join both phones **only** to the home network, still without mobile fallback;
   repeat browse, resolve, TXT comparison, hotspot start/stop and home reconnect.
   `/info.request_path` must be `home`. Confirm `_http._tcp` and
   `_smartbridge._tcp` remain available on management networks. Confirm no
   Astro Bridge service is visible on an instrument-only network using a
   separate isolated test client; do not connect/control an instrument.
Known hostname-renaming limit: changing only the configured hostname reloads
its new instance/name TXT immediately, but Avahi `--reload` does not reread
`avahi-daemon.conf`. The new `.local` hostname takes effect on the next Avahi
restart (management-interface change or explicit owner service restart). Record
this behavior as the current known limitation, not a successful immediate rename.

7. With owner authorization, restart the application and compare opaque id
   equality without recording it. Capability/HTTPS changes should reload Avahi;
   management interface changes restart it. Observe actual behavior on both
   supported init systems when available. Leave unavailable combinations pending.

Record one row per phone/network/transition; repeat each timing at least three
times. Keep captures private and redact names, addresses and device ids.

| Phone / OS / browser | Network role | Transition / repetition | Appears (s) | Resolves (s) | HTTP reachable / request_path | TXT complete / id+caps match | Avahi action / event roles | Result / redacted notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Pending | hotspot | start | — | — | — | — | — | Not run |
| Pending | hotspot | home leave/rejoin | — | — | — | — | — | Not run |
| Pending | home | reconnect | — | — | — | — | — | Not run |

A pass needs successful actual resolution and matching identity on **both**
phones for hotspot-only/offline and home paths, with measured timing. This
procedure sets no unmeasured latency promise. Record intermittent failures and
leave the acceptance open until the owner accepts the observed results.
