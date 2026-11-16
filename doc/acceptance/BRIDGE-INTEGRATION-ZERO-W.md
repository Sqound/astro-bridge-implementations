> Commands use the verified staged project described in [candidate preparation](../candidate-preparation.md). Hardware steps require separate owner approval.

# Pi Zero W integration acceptance (owner-run)

**Documented, not verified on hardware.** This is an Alpine ARMv6 application candidate for an original Zero W. It requires an explicit owner decision for a named device, approved maintenance window and candidate digest. Preparing the archive or passing portable tests does not open that gate. Reserve a Pi 3B or better for the separate camera bench.

## Candidate and access prerequisites

1. Use a known-good, tested spare boot card/backup and local recovery console. Keep the original card offline and secure; it contains private state. Record the baseline app version/build (nullable), OS image, Pi model, radio count/roles, Singularity/phone versions and normal access from hotspot and home. In Singularity compare its controller list and connected devices with the expected setup. Write counts and equality only. Note normal discovery, reconnect, control ownership and session behavior.
2. The release owner supplies the reviewed candidate, manifest and independently trusted approved SHA256/build identity. On the laptop, use the matching source revision, Python 3.11+, Node with built-in fetch, and a private evidence directory. Example offline reproduction:

   ```sh
   umask 077
   CAND="$HOME/astro-bridge-candidate"
   # Builder only, into an empty private directory. Owners use the supplied files.
   mkdir -p "$CAND"
   python3 tools/package-release.py --output-dir "$CAND"
   python3 tools/verify-release.py --apps-only --artifact-dir "$CAND"
   ```

   Both matching platform pairs are produced. For this Pi use **only** `ota-manifest-linux-alpine-armhf.json` and the archive named by its `asset`. The trust path is trusted transport, archive SHA256 and the packaged build identity. No detached signature is supplied. Read the manifest, verify the digest with `shasum -a 256 "$CAND"/astro-bridge-*-linux-alpine-armhf-ota.tar.gz` and compare with the independently approved value. Never change a manifest version or name to satisfy a preflight check.
3. **Owner console.** Require a healthy persistent data mount with at least 100 MiB free beyond staging, an actual running version strictly **older** than the manifest for an OTA install, and the **fixed** v3 validator already installed by a separately reviewed image/maintenance action. Current source packages 0.6.35. An existing same-version or numerically newer installation (including 1.x) cannot take it. Do not assume the live device's version. A new SD image seeded from the same source would also have the same version. For a different or unknown loaded identity at the same/newer version, stop for a separately approved compatible baseline or genuinely newer reviewed app.
4. Check `cat /etc/wifi-bridge/updater-version` gives 3. Compare `sha256sum /usr/libexec/astro-bridge-ota` with the fixed helper digest from the reviewed image recipe/source (`shasum -a 256 wifi_bridge/ota.py` on the matching laptop checkout). Require both. Format 3 requires updater 3 on either platform. A v1/v2 image, a missing helper or a mismatched digest stops the procedure. The replaceable app cannot update this Alpine helper or the Pi OS equivalent at `/usr/local/lib/astro-bridge/ota.py`. The reviewed standard overlay installs the helper and marker together for a future approved image. Do not hand-edit the marker or run the app copy as the fixed helper.
5. End all observations normally. Have their owners finish reservations and remove their own locks as appropriate, disable saved instrument profiles in the normal UI and wait for network/settings work to finish. The owner's local `wget -qO- http://127.0.0.1/api/update-health` must show `management_ready:true`, `telescopes_connected:false`, `update_safe:true` and a supported ready mode. Missing/false or a deferred update is a stop. Confirm the baseline version here too.
   **Exact candidate already running:** if an independently approved image/source operation installed the candidate, compare its trusted provenance, running version and non-null loaded build ID to the approved manifest, verify both helper checks and healthy management on both paths. Only when all match, skip step 6, record `delivery:"already-running"` and proceed with the bench. Record OTA installation as **not exercised**. Otherwise follow the strictly-newer prerequisite; a version string alone cannot establish identity.
6. Only after these checks, transfer the **two reviewed files only** through the owner's trusted private channel to a private staging directory on the device as `archive.tar.gz` and `manifest.json`. At the root console in that directory:

   ```sh
   umask 077
   sha256sum archive.tar.gz
   # Set APPROVED_SHA256 to the independently approved public digest after comparison.
   install -d -m 700 /var/lib/wifi-bridge/updates/import
   install -m 600 manifest.json /var/lib/wifi-bridge/updates/import/manifest.json
   install -m 600 archive.tar.gz /var/lib/wifi-bridge/updates/import/archive.tar.gz
   /usr/bin/python3 /usr/libexec/astro-bridge-ota queue-local --sha256 "$APPROVED_SHA256"
   ```

   The detached worker may interrupt management. Wait up to the 180-second verification window plus service restart/reassociation time. From console recheck loopback health, then from both actual management networks open the dashboard. In General/Updates confirm installed, current version **and** current loaded build equal the manifest. Confirm prior settings/credentials still work and the owner sees the expected radio roles. Record `delivery:"ota-installed"` for a verified OTA. Mark `candidate_verified` only after all applicable checks. If verification or service recovery fails, go directly to recovery below.

## Measure idle, matched relay controls and a complete observing session

Use the same RF placement, power supply, OS/build and radio assignments throughout. Keep the laptop awake on confirmed home management; on this Singularity home path the phone may require the existing router route to the Vespera subnet. Use the existing working route. Do not reroute ordinary home traffic for this bench. The only manufacturer app controlling the instrument is the owner's Singularity. Zero W observation uses the enabled read-only Vespera observer. Record loss, availability and units; omit secrets, raw payloads, screenshots with identities, actual endpoints, personal network names and device IDs.

1. On the laptop create a fresh private directory outside Git (`umask 077; mkdir -m 700 "$RUN"`, with `RUN` set to a new absolute path). Set `BRIDGE` to the resolved management origin. After at least 60 seconds of warm-up, all instrument profiles and telemetry inactive, record **10 minutes with no echo traffic**:

   ```sh
   python3 tools/load-bench.py --bridge "$BRIDGE" --metrics-only --duration 600 --output-dir "$RUN/idle-run"
   cp "$RUN"/idle-run/load-bench-*.json "$RUN/idle.json"
   ```

   Each bench invocation prompts privately for the administrator password and revokes its temporary token. Metrics-only reads the existing five-second process cache. Keep each subdirectory unique so each glob selects exactly one result.
2. Prepare the dedicated isolated UDP echo simulator and its normal temporary SynScan profile exactly as in [LOAD-BENCH.md](LOAD-BENCH.md). Choose a spare radio, an independent simulator target, and the **home** path. Confirm the relay endpoint belongs only to this simulator. Set `SIM_PROFILE` to its local profile ID. Never send bench packets to Vespera. Leave this simulator topology in place for both runs. If there are insufficient radios for home + Vespera + simulator, leave the comparison incomplete. Preserve the separate AP path for later tests.
3. Connect Vespera normally. With the Vespera observer off and Singularity in normal use, note the current controller/connected-device lists and behavior. Capture a matched control in a 125-second observing interval. Use this *same* command later with `on` in place of `off` during a comparable observing interval with telemetry enabled:

   ```sh
   phase=off
   python3 tools/load-bench.py --bridge "$BRIDGE" --profile "$SIM_PROFILE" --simulator-target \
     --path home --transport udp --flows 1 --duration 125 --rate 10 --size 256 --output-dir "$RUN/relay-$phase-run"
   cp "$RUN"/relay-"$phase"-run/load-bench-*.json "$RUN/relay-$phase.json"
   ```

   Use one UDP flow in both runs. Keep `simulator_conditions_matched` unknown if radio placement, host/path, traffic, RF conditions, duration or load changed materially. Compare replies to sent counts, retaining RTT/poll snapshots in the original bench files. Stop on an adverse instrument or phone behavior.
4. As owner, enable data collection for the saved Vespera, including `vespera_status_v1` and `link_v1`, in the Data/Session settings. Check the observer reaches listening and normalized Vespera values appear. Start the metrics collector **before** the owner's next normal observing session; do not interrupt observing for any off/on or hotspot tests. At the beginning of the observation start an independent monotonic stopwatch and capture the laptop UTC window label with `date -u +%Y-%m-%dT%H:%M:%SZ`. Use this same label, observing duration and confirmed loaded identity on the owner and telemetry count sheets. Watch Singularity through connection, start, tracking/stacking, completion and return to normal. Compare its controller list and connected-device list before/during/after; record counts and membership equality, who retained control, and any refusal, yield, timeout or reconnect. Compare actual behavior with the telemetry-off control. Collect the matched `phase=on` relay run during this session from a second terminal.

   ```sh
   python3 tools/load-bench.py --bridge "$BRIDGE" --metrics-only --duration 42000 --output-dir "$RUN/session-run"
   # Press Ctrl-C only after the whole session closes; this finishes the current evidence window.
   cp "$RUN"/session-run/load-bench-*.json "$RUN/session.json"
   ```

   Wait for one more sample (five seconds) after observation ends before stopping the collector. Record the stopwatch duration in seconds in `owner.json`. The collector has an 11h40m cap within its token lifetime. A capped, aborted or partial session never certifies a longer one. Deduplicate repeated cache sequences. Missing/invalid CPU, sequence reset, missed cache sequences, excessive age or timing holes leave resource evidence incomplete. A quiet recovery window can be retained with another 60-second metrics-only run.
5. In Sessions select the exact Vespera session. Record a redacted count sheet with beginning/end, history availability/limited status, observer pauses, normalized metrics present, retained `analysis.sample_count` and any known gaps. Compare a counted independent expected sample set with the observed samples **at the same observation layer and units** for the whole window. For example, if an owner-approved source exposes a normalized count/sequence reference, match per-sample sequences in memory and retain only expected/missing counts, scope and method. Do not persist raw payloads. Multiple values per status, deliberate change-only emission, unrelated journal sequences, lifecycle events and reconnects must not count as missing samples. A nominal firmware cadence, global journal range or a download matching the bridge's own retained count alone cannot measure source losses. If an independent count is unavailable, record `basis:"estimated"` or `"unknown"` and leave this gate **incomplete**. The bridge currently exposes no independent Vespera source counter. Keep approximate timing/retention evidence for follow-up.
6. After session end release the simulator flow and remove its temporary profile normally. Preserve the owner's Vespera profile, return settings to the agreed state at the end, and keep physical power-cycle tests outside any live observation.

## Discovery, native clients and hotspot outage replay

1. Run **all steps** in [DISCOVERY-HOTSPOT.md](DISCOVERY-HOTSPOT.md) for iOS and Android, three timings per phone/network/transition. Retain a private redacted `discovery.md` using its table. Record unavailable paths/init systems. For this Alpine check, require both phones to resolve/reach the matching bridge and demonstrate the expected ingress and TXT on hotspot-only/offline and home. Set each phone's result only after reviewing its rows.
2. Complete this sequence **first on hotspot, then home**. Laptop joins only the chosen network; disable VPN/Ethernet/cellular fallback. From the DNS-SD browser note the resolved origin and TXT id transiently. Verify the same opaque bridge identity on both networks and avoid recording the interactive terminal. Have exactly one owner-authorized Vespera profile with paired-client connection allowed, no foreign reservation or pending operation, and a free/eligible instrument radio. Only use the normal owner-permitted path; do not force replacement. Start from a safely idle, powered-on Vespera. The owner powers it off/on **only** at the prompts, observing its normal shutdown/startup requirements. In the workspace shell, run the respective command:

   ```sh
   MANIFEST="$CAND/ota-manifest-linux-alpine-armhf.json"
   node tools/zero-w-native.mjs hotspot "$RUN/native-hotspot.json" "$MANIFEST"
   # After finishing the hotspot run, switch the laptop to the home network.
   node tools/zero-w-native.mjs home "$RUN/native-home.json" "$MANIFEST"
   ```

   A separate memory-only administrator session reads `GET /api/v1/system/updates` before and after each native/replay flow, including cleanup. It verifies the actual non-demo loaded version/build against the approved manifest and logs out. It performs no update action. Loopback update-health is restricted to the device console. The paired client keeps only its normal test scopes. Raw password buffers are cleared after initial authentication.

   The wrapper imports `examples/astro-bridge-client.mjs`, checks `info` matches the resolved TXT and ingress before pairing, requests read/connect only, lists the catalog, connects by instrument ID, and waits for a current endpoint. It never sends traffic to that endpoint. It times pairing, list, connect/endpoint, lost/restored events relative to the owner's physical switch actions, the restored endpoint and release. It rejects event cursor resets. Timeouts, absent endpoints or protected connections require a fresh attempt after owner review. If a previous run already connected the profile, record that warm start; for a cold association use the normal owner UI outside observation. Revoke each temporary paired app in Settings after use.
3. Radio outage, **after** the observing/native runs. Use an approved dedicated removable AP radio with a separately functioning home/console management path; confirm normal access, isolation and current radio roles. Only use the owner's safe test configuration. The laptop joins only the hotspot; disable automatic failover. Run:

   ```sh
   node tools/zero-w-native.mjs replay "$RUN/replay.json" "$MANIFEST"
   ```

   The read-only pairing saves a pre-outage journal cursor in memory. At its prompt, remove only the AP adapter. From the other management client watch reserved radios, any eligible fallback and actual AP loss. Wait for confirmed unavailability; reinsert the adapter and rejoin once DHCP, DNS-SD and HTTP return. Press Enter. The wrapper reads forward from the same boot/cursor and requires ordered `hotspot_unavailable` then `hotspot_restored` for the hotspot. Record unplug → actual access loss, replug → DHCP/HTTP, role/fallback behavior and redacted failure/restoration reason in `outage.md`. The restored AP event alone does not demonstrate instrument availability. Confirm no reserved home/instrument radio was taken. If a spare carried the hotspot continuously, document that fallback separately; the **outage replay** still requires a safe configuration that demonstrates an actual interruption. Do not manufacture a pass from a deliberate UI disconnect, fresh snapshot, service reset or unverified loss. Revoke the read-only test pairing, restore roles and independently recheck access.

## Evidence and decision

Local `idle.json`, `session.json`, `relay-off.json`, `relay-on.json`, `native-hotspot.json`, `native-home.json` and `replay.json` come from the commands above. Retain the approved manifest/digest, pre/post loaded identity and checks, redacted count sheet, controller/device counts, timing sheets and references to failed attempts alongside the owner's observations. The original bench files can include addresses in relay metrics; keep them private. Never attach configuration backups, authentication data, full API responses or wire captures to a report.

Create `owner.json` with the following fields. Use the confirmed loaded version/build, the observed UTC window label and numeric stopwatch duration. Set delivery to `ota-installed` or `already-running`. Replace null with true/false for each completed observation. Leave unavailable entries null. `whole_session` attests that the sample window actually covered the same whole session as the owner's start/end observations. Record failures as false even if another trial passed.

```json
{"identity":{"version":null,"build_id":null},"window_start_utc":null,
 "delivery":null,"candidate_verified":null,"whole_session":null,"observing_seconds":null,
 "simulator_conditions_matched":null,"controller_list_unchanged":null,
 "connected_devices_unchanged":null,"singularity_session_unchanged":null,
 "discovery_ios":null,"discovery_android":null,"hotspot_outage_replay":null}
```

Create `telemetry.json` from the count sheet. Tag the counts with the same confirmed build, observation start in UTC and stopwatch duration as the owner sheet. Use `"counted"` only for an independently counted same-window denominator. `missing` counts expected samples with no matching observation.

```json
{"identity":{"version":null,"build_id":null},"window_start_utc":null,"observing_seconds":null,
 "basis":"unknown","whole_session":null,"expected":null,"missing":null}
```

```sh
python3 tools/zero-w-summary.py "$RUN" "$CAND/ota-manifest-linux-alpine-armhf.json" > "$RUN/summary.json"
```

| Gate | Passing evidence |
| --- | --- |
| Process memory | Maximum session RSS minus **median ten-minute idle RSS** strictly below 12 MiB |
| CPU | Nearest-rank p95 of unique cached **5-second process CPU** windows over the whole observing session strictly below 60% |
| Relay | Matched simulator loss (100 × lost / sent) with observer on minus off **at most 2 percentage points** |
| Telemetry | Whole-session missing / expected samples **strictly below 1%**, counted basis |
| Owner and client checks | Both Singularity lists and observed session behavior unchanged; both phone discovery checks, both timed native flows and real outage/replay verified |

Exactly 12 MiB, 60% or 1% fails. Exactly two points of additional relay loss passes. The summarizer prints only numeric measurements and fixed check names. Exit 0 means all supplied checks passed, 1 means a check failed, 2 means incomplete. A passing summary still needs the owner to review the evidence and report their decision. Include the source revision, candidate build, summary and redacted failed/partial cases when handing results back.

## Recovery

* **Deferred or rejected before switching:** leave the known-good application running. Record the status category, version/build and safety reason. Finish existing activity normally or resolve the prerequisite through owner-reviewed maintenance.
* **Interrupted or failed verification:** the fixed helper's locked transaction restores the previous app and saved config, then attempts to restart. On an interrupted active transaction the normal Alpine pre-start recovery is idempotent. Wait for completion before acting. Inspect General/Updates or the root-private transaction/status locally. If files were restored but the service is stopped, use the approved console and `rc-service wifi-bridge start` once the worker has finished, then verify the prior loaded version/build, settings, phone and management paths. If recovery is required or state is unclear, stop; do not delete the journal, manually rename live trees or override health.
* **Later functional failure after an installed transaction:** end observing normally and record the failing check. The updater's recovery command operates on unfinished transactions; it cannot undo an already completed install. Shut down normally using the owner controls, power down fully, restore the previously tested original boot card and power on. Verify the saved baseline version/build, credentials, radio roles, management paths and normal Singularity behavior. Keep the failed test card private for owner-reviewed diagnosis. This fallback is why a known-good baseline card is a prerequisite.
