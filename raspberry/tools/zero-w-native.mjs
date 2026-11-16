// Owner-run native v1 acceptance. Credentials and addresses never enter evidence.
import { spawnSync } from "node:child_process";
import { open, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline/promises";
import { setTimeout as delay } from "node:timers/promises";
import { AstroBridgeClient } from "../examples/astro-bridge-client.mjs";

const [mode, output, manifestPath] = process.argv.slice(2);
const role = mode === "replay" ? "hotspot" : mode;
if (!["hotspot", "home", "replay"].includes(mode) || !output || !manifestPath || !process.stdin.isTTY) {
  throw new Error("Use a terminal: node tools/zero-w-native.mjs hotspot|home|replay OUTPUT.json ALPINE_MANIFEST.json");
}
const file = await open(manifestPath, "r");
let expected;
try {
  const stat = await file.stat();
  if (!stat.isFile() || stat.size > 32768) throw new Error("Invalid candidate manifest");
  expected = JSON.parse(await file.readFile("utf8"));
  if (expected.format !== 3 || expected.minimum_updater !== 3 || expected.scope !== "application-only" ||
      expected.platform !== "linux-alpine-armhf" || !/^[0-9a-f]{64}$/.test(expected.build_id) ||
      !/^\d+\.\d+\.\d+$/.test(expected.version)) throw new Error("Invalid candidate manifest");
} finally { await file.close(); }
const prompt = () => createInterface({input: process.stdin, output: process.stderr});
let terminal = prompt();
const elapsed = start => Math.round(performance.now() - start);
const report = {role, passed: false, timings_ms: {}};
let bridge, witness, lease, instrument;
async function verifyBuild(phase) {
  // The authenticated witness performs only this read; update-health is loopback-only.
  const status = await witness.request("GET", "/system/updates");
  if (status.demo !== false || status.current_version !== expected.version ||
      status.current_build_id !== expected.build_id) throw new Error("Candidate identity mismatch");
  report[phase] = {version: expected.version, build_id: expected.build_id};
}
try {
  bridge = new AstroBridgeClient((await terminal.question("Resolved management origin: ")).trim());
  const id = (await terminal.question("Resolved DNS-SD TXT id: ")).trim();
  const info = await bridge.info();
  if (info.version !== expected.version || info.contract_version !== 1 || info.request_path !== role || info.device_id !== id) {
    throw new Error("Discovery mismatch");
  }
  // getpass reads directly from the controlling terminal; no shell or argv secret.
  terminal.close();
  const secret = spawnSync("python3", ["-c", "import getpass; print(getpass.getpass())"],
                           {stdio: ["inherit", "pipe", "inherit"], maxBuffer: 4096});
  terminal = prompt();
  if (secret.status !== 0) throw new Error("Password entry failed");
  let pair, started;
  try {
    witness = new AstroBridgeClient(bridge.baseURL);
    await witness.login(secret.stdout.toString().replace(/\r?\n$/, ""));
    await verifyBuild("identity_before");
    started = performance.now();
    const scopes = mode === "replay" ? ["read"] : ["read", "connect"];
    pair = await bridge.pair(secret.stdout.toString().replace(/\r?\n$/, ""), "Zero W " + mode, scopes);
  } finally {
    secret.stdout.fill(0);
  }
  if (pair.device_id !== id) throw new Error("Pairing mismatch");
  await bridge.loginClient(pair.client_id, pair.client_secret);
  report.timings_ms.pair = elapsed(started);
  if (mode === "replay") {
    const initial = await bridge.events();
    const outageStarted = performance.now();
    await terminal.question("Remove the dedicated AP adapter. After loss, restore it and rejoin only the hotspot. Press Enter when rejoined: ");
    if ((await bridge.info()).request_path !== "hotspot") throw new Error("Wrong ingress");
    let cursor = initial.latest_sequence, unavailable = false, restored = false;
    for (let pageCount = 0; pageCount < 12; pageCount++) {
      const page = await bridge.events({after: cursor, bootId: initial.boot_id, limit: 100});
      if (page.gap || page.reset_reason || page.boot_id !== initial.boot_id) throw new Error("Replay reset");
      for (const event of page.events) {
        if (event.profile_id !== "hotspot" || event.sequence <= cursor) continue;
        if (event.type === "hotspot_unavailable") unavailable = true;
        if (unavailable && event.type === "hotspot_restored" && event.ready === true) restored = true;
      }
      cursor = page.next_cursor;
      if (!page.has_more) break;
    }
    if (!restored) throw new Error("Outage pair unavailable");
    report.timings_ms.replay = elapsed(outageStarted);
  } else {
    const listStarted = performance.now();
    const catalog = await bridge.allInstruments();
    report.timings_ms.list = elapsed(listStarted);
    const matches = catalog.instruments.filter(row => row.family === "vespera" && row.client_connect);
    if (matches.length !== 1) throw new Error("Select exactly one authorized Vespera");
    instrument = matches[0].id;
    const connectStarted = performance.now();
    const result = await bridge.connect(instrument, catalog.config_revision, {lease_seconds: 900});
    lease = result.lease.id;
    if (result.state === "connecting") {
      for (;;) {
        const operation = await bridge.operation(result.operation.id);
        if (operation.state === "succeeded" && operation.endpoint_current) break;
        if (!["running","queued"].includes(operation.state) || elapsed(connectStarted) >= 180000) {
          throw new Error("Connect did not complete");
        }
        await delay(1000);
      }
    } else if (result.state !== "ready") throw new Error("No ready instrument");
    async function verifyEndpoint() {
      const endpoint = await bridge.endpoint(instrument);
      if (endpoint.instrument_id !== instrument || endpoint.request_path !== role ||
          !endpoint.primary?.host || !endpoint.primary.ports?.length) throw new Error("No current endpoint");
      const inventory = await bridge.endpoints(instrument);
      const paths = inventory.endpoints.find(row => row.profile_id === instrument)?.paths ?? [];
      if (!endpoint.primary.ports.every(port => paths.some(path => path.ready && path.path === role &&
          path.host === endpoint.primary.host && path.port === port.port && path.transport === port.transport))) {
        throw new Error("Endpoint unavailable on this management path");
      }
    }
    await verifyEndpoint();
    report.timings_ms.connect_endpoint = elapsed(connectStarted);
    for (const [action, type] of [["off", "lost"], ["on", "restored"]]) {
      const initial = await bridge.events();
      let cursor = initial.latest_sequence;
      await terminal.question("Press Enter as you switch Vespera " + action + ": ");
      const changeStarted = performance.now();
      let found = false;
      while (!found && elapsed(changeStarted) < 180000) {
        const page = await bridge.waitEvents({after: cursor, bootId: initial.boot_id, wait: 5});
        if (page.gap || page.reset_reason || page.boot_id !== initial.boot_id) throw new Error("Event history reset");
        found = page.events.some(event => event.type === "instrument." + type &&
                              event.instrument_id === instrument && event.sequence > cursor);
        cursor = page.next_cursor;
      }
      if (!found) throw new Error("Transition timed out");
      report.timings_ms[type] = elapsed(changeStarted);
    }
    // Restoration reports association; allow the service/endpoint time to return.
    const restoreStarted = performance.now();
    for (;;) {
      try { await verifyEndpoint(); break; }
      catch (error) {
        if (elapsed(restoreStarted) >= 60000) throw error;
        await delay(1000);
      }
    }
    report.timings_ms.restored_endpoint = elapsed(restoreStarted);
    const releaseStarted = performance.now();
    await bridge.release(instrument, lease);
    lease = undefined;
    report.timings_ms.release = elapsed(releaseStarted);
  }
  report.passed = true;
} catch {
  process.exitCode = 1;
  console.error("Native check incomplete or failed. Review the last step and the temporary pairing in Settings.");
} finally {
  if (lease) try { await bridge.release(instrument, lease); } catch { console.error("Check reservation expiry in Settings."); }
  if (report.identity_before) {
    try { await verifyBuild("identity_after"); }
    catch { report.passed = false; process.exitCode = 1; console.error("Final loaded candidate identity could not be verified."); }
  }
  if (witness) {
    try { await witness.logout(); report.witness_logout_confirmed = true; }
    catch { report.witness_logout_confirmed = false; console.error("Could not confirm witness logout. Check management access."); }
  }
  if (bridge) try { await bridge.logout(); } catch { /* owner revokes the temporary pairing */ }
  terminal.close();
  await writeFile(output, JSON.stringify(report) + "\n", {flag:"wx", mode:0o600});
}
