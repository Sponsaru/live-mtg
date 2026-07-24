#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const cli = new URL("cli/live-mtg.mjs", root);
const testHome = mkdtempSync(join(tmpdir(), "live-mtg-supervisor-"));
process.env.LIVE_MTG_HOME = testHome;
process.env.LIVE_MTG_LANGUAGE = "ja";

const {
  confirmedServiceHealth,
  createRotatingLogWriter,
  restartDelayMs,
  serviceStatusText,
  superviseProcess,
} = await import(`${cli.href}?test=${Date.now()}`);

let healthAttempts = 0;
const confirmed = await confirmedServiceHealth(async () => {
  healthAttempts++;
  return healthAttempts === 3 ? { ok: true, service: "live-mtg" } : null;
}, 3, 0);
assert.equal(confirmed?.service, "live-mtg");
assert.equal(healthAttempts, 3, "status must retry transient health failures before reporting a restart");

assert.deepEqual(
  [1, 2, 3, 4, 5, 6, 20].map(count => restartDelayMs(count)),
  [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000],
);

const logPath = join(testHome, "rotation.log");
const writer = createRotatingLogWriter(logPath, 32, 2);
writer.write("a".repeat(24));
writer.write("b".repeat(24));
writer.write("c".repeat(24));
assert.equal(readFileSync(logPath, "utf8"), "c".repeat(24));
assert.equal(readFileSync(`${logPath}.1`, "utf8"), "b".repeat(24));
assert.equal(readFileSync(`${logPath}.2`, "utf8"), "a".repeat(24));

const waitFor = async (predicate, timeoutMs = 2_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error("timed out waiting for supervised process");
};

const startsPath = join(testHome, "starts.txt");
const events = [];
const output = [];
const crashing = superviseProcess(process.execPath, [
  "-e",
  "require('fs').appendFileSync(process.argv[1], 'x'); process.stdout.write('boot\\n'); setTimeout(() => process.exit(7), 15)",
  startsPath,
], {
  stdio: ["ignore", "pipe", "pipe"],
  restartBaseMs: 10,
  restartMaxMs: 20,
  stableMs: 1_000,
  onEvent: event => events.push(event),
  onOutput: chunk => output.push(String(chunk)),
});
await waitFor(() => existsSync(startsPath) && readFileSync(startsPath, "utf8").length >= 3);
await crashing.stop();
const startsAfterStop = readFileSync(startsPath, "utf8").length;
await new Promise(resolve => setTimeout(resolve, 80));
assert.equal(readFileSync(startsPath, "utf8").length, startsAfterStop, "manual stop must cancel restarts");
assert.ok(events.filter(event => event.type === "restart").length >= 2, "unexpected exits must restart the child");
assert.ok(events.some(event => event.type === "restart" && event.code === 7));
assert.match(output.join(""), /boot/);

assert.match(serviceStatusText(false, false), /live-mtg start/);
assert.match(serviceStatusText(false, false), /live-mtg logs/);
assert.match(serviceStatusText(false, true), /再起動を試行中/);
assert.equal(serviceStatusText(true), "LiveMTGは起動中です");

const source = readFileSync(fileURLToPath(cli), "utf8");
assert.match(source, /<string>serve<\/string><string>--daemon<\/string>/, "LaunchAgent must enable daemon logging");
assert.match(source, /const task = `[^\n]+serve --daemon`/, "Task Scheduler must enable daemon logging");
assert.match(source, /spawnSync\("schtasks", \["\/Create"/, "Task Scheduler registration must remain enabled");
assert.match(source, /CreateObject\("WScript\.Shell"\)[^\n]+serve --daemon/, "Startup VBS must enable daemon logging");
assert.match(source, /daemonSupervisorVersion/, "existing Windows daemon definitions must be migrated");
assert.match(source, /spawnSync\("taskkill", \["\/PID", String\(pid\), "\/T", "\/F"\]/, "Windows stop must terminate the supervised process tree");

rmSync(testHome, { recursive: true, force: true });
console.log("CLI server logging, restart supervision, and status guidance OK");
