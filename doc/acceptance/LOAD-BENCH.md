> Commands use the verified staged project described in [candidate preparation](../candidate-preparation.md). Hardware steps require separate owner approval.

# Reproducible bridge load measurements

The echo procedure below is for a disposable simulator only. For a ten-minute
idle or full Vespera observing window use the separate `--metrics-only` mode in
[Zero W acceptance](BRIDGE-INTEGRATION-ZERO-W.md). That mode polls the process
cache every four seconds and generates no instrument or simulator traffic.
Do not send echo traffic to a telescope or mount. The bench does not configure
radios, change a saved profile, release a UDP owner, or change instrument state.
Pi Zero W measurements are **documented, not verified on hardware**. Synthetic
loopback results establish tool behavior only and cannot explain the 28 October
field latency/loss.

## Prepare a simulator

Use a dedicated Linux test fixture following `tests/linux_isolated_mounts.py`:
one management client path, one bridge instrument namespace and a separate echo
simulator target connected by disposable veth links. The existing fixture's
`AM5N`/`SYN` protocol responders are not echo servers. On dedicated simulator
equipment, or inside the simulator target namespace of that disposable fixture,
start the supplied byte-transparent echo server (choose TCP or UDP; the address
must already belong to the simulator):

```sh
python3 tools/load-bench.py --serve-simulator --transport tcp \
  --listen 192.168.4.1 --listen-port 4030
python3 tools/load-bench.py --serve-simulator --transport udp \
  --listen 192.168.4.1 --listen-port 11880
```

Run the needed server in its own terminal; Ctrl-C closes it. These commands only
bind sockets and do not create namespaces, interfaces, addresses or firewall rules.
The default listen address is loopback; an external bind must be explicit.
Do not run namespace setup on the
owner's ordinary host network or alter an existing Docker deployment.

For hardware acceptance the owner must prepare a dedicated simulator Wi-Fi network on separate
equipment, then explicitly configure a temporary isolated mount profile through
the normal bridge interface: TCP 4030 (`zwo_mount`) or UDP 11880 (`synscan`), with
the simulator address as target. Keep every real instrument disconnected from
that profile. Use spare radios, preserve the home/hotspot management path, and
record the temporary profile's ID. An echo simulator does not answer the normal
read-only identity probe: `service.state=not_answering` is expected even while
forwarding is ready. Remove the temporary profile through the normal UI when done.

## Run from a client host

Python 3.11+ and the repository suffice; there are no external packages. When in
this workspace, source `env.sh` before every Python command. Start with the fully
local smoke check, which uses DemoNetwork HTTP plus real loopback echo relays:

```sh
python3 tools/load-bench.py --dry-run --duration 6 --transport tcp
python3 tools/load-bench.py --dry-run --duration 6 --transport udp
```

Then run from a host on the selected management path against the owner-prepared
simulator profile (replace the example origin and profile ID):

```sh
python3 tools/load-bench.py --bridge http://astro-bridge-example.local \
  --profile simulator --simulator-target --path home --transport tcp \
  --flows 1 --duration 125 --rate 10 --size 256
```

The administrator password is entered without terminal echo. The short-lived
Bearer token stays in memory and is revoked on exit; no credential is written to
the artifact. Use a trusted management network or already trusted HTTPS. The tool
refuses redirecting API responses. Endpoints come from the bridge inventory; the
client never asks for a separate instrument address.

Repeat TCP with 1, 2, 4 and 8 flows at the same duration/rate/size, then vary one
parameter at a time. A fresh connection is used per flow, reused across its echo
requests. Record an idle metrics baseline, 125-second active run and 60-second
recovery; keep power supply, OS/application revision, radio count, management
path, simulator equipment, RF placement and thermal conditions comparable. The
60-second CPU window is unavailable during warm-up; preserve sample sequence/age.
Compare a direct client-to-simulator control using the same echo flow parameters
outside the bridge to separate simulator/RF behavior. Do not infer capacity from
a single loopback result. Record setup details separately without personal SSIDs,
MACs, credentials or raw packet captures.

UDP has one controller per profile across management paths. One flow measures
round-trip behavior. N>1 intentionally exercises ownership refusal and counts
those refusals as drops; it does not measure N accepted UDP clients. For multiple
independent accepted UDP streams use separate simulator profiles/endpoints and
one bench process per profile. Do not release a real active owner for a bench.

Results are mode-0600 timestamped JSON files in `release-artifacts/` (override
`--output-dir` to another evidence directory). They contain the bridge application version, parameters, per-flow
sent/reply/failure/loss counts, p50/p95 RTT from at most the last 10,000 successful
exchanges per flow, and metrics snapshots at start, every five seconds and end.
Metrics retain cache age/sequence; the final snapshot may precede the last packet
by up to five seconds. Metrics-request failure aborts the run. Each exchange has an absolute deadline of at most one second. A TCP timeout ends
that flow because later replies would have ambiguous framing. UDP payloads carry
a disposable per-flow nonce and sequence; mismatched replies count as failures.
No echo payload, token, password or request headers are recorded. Review metrics
addresses before sharing artifacts externally.

Limits: 1–32 flows, 0.1–3600 seconds, 1–1000 exchanges/second/flow and 16–16384 bytes.
Reported process CPU includes all bridge threads and measurement overhead. CPU can
exceed 100% on multicore hosts; the Zero W has one core. No pass/fail threshold is
invented here: the acceptance run must retain idle/loaded/recovery RSS, CPU, lag, RTT and loss
and decide suitable operational headroom with the owner.
