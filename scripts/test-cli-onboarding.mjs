#!/usr/bin/env node
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const cli = join(root, "cli", "live-mtg.mjs");

function invoke(args, language = "en") {
  const home = mkdtempSync(join(tmpdir(), "live-mtg-cli-onboard-"));
  const result = spawnSync(process.execPath, [cli, ...args], {
    env: { ...process.env, LIVE_MTG_HOME: home, LIVE_MTG_LANGUAGE: language },
    input: "",
    encoding: "utf8",
    timeout: 10_000,
  });
  return { ...result, home, output: `${result.stdout || ""}${result.stderr || ""}` };
}

for (const args of [["onboard"], []]) {
  const result = invoke(args, "ja");
  assert.equal(result.status, 2, result.output);
  assert.match(result.output, /live-mtg onboard --yes/);
  assert.match(result.output, /非対話環境/);
  assert.equal(existsSync(join(result.home, "config.json")), false, "non-TTY failure must not save partial setup");
}

for (const language of ["ja", "en"]) {
  const result = invoke(["--help"], language);
  assert.equal(result.status, 0, result.output);
  assert.match(result.output, /live-mtg onboard --yes/);
}

const commandHelp = invoke(["onboard", "--help"], "en");
assert.equal(commandHelp.status, 0, commandHelp.output);
assert.match(commandHelp.output, /Auto-approve prompts/);
assert.equal(existsSync(join(commandHelp.home, "config.json")), false, "onboard --help must have no setup side effects");

process.env.LIVE_MTG_HOME = mkdtempSync(join(tmpdir(), "live-mtg-cli-import-"));
process.env.LIVE_MTG_LANGUAGE = "en";
const helpers = await import(`${pathToFileURL(cli).href}?test=${Date.now()}`);
assert.equal(
  helpers.formatDownloadProgress(125_000_000, 500_000_000, 10),
  "Received 125.0 / 500.0 MB (25.0%) · elapsed 00:10 · 12.5 MB/s",
);
const literal = "safe;echo-not-executed";
const child = helpers.runCommandSync(process.execPath, ["-e", "process.stdout.write(process.argv[1])", literal], { encoding: "utf8" });
assert.equal(child.status, 0, child.stderr);
assert.equal(child.stdout, literal);

const payload = Buffer.alloc(256_000, 7);
const downloadDir = mkdtempSync(join(tmpdir(), "live-mtg-download-"));
const downloaded = join(downloadDir, "model.bin");
const progress = [];
const originalLog = console.log;
const originalFetch = globalThis.fetch;
console.log = (...values) => progress.push(values.join(" "));
try {
  globalThis.fetch = async () => new Response(payload, { status: 200, headers: { "content-length": payload.length } });
  assert.equal(await helpers.downloadFileWithProgress("https://example.invalid/model", downloaded), true);
  assert.equal(readFileSync(downloaded).length, payload.length);
  assert.equal(existsSync(`${downloaded}.download`), false);
  assert.match(progress.join("\n"), /100\.0%/);
  globalThis.fetch = async () => new Response("missing", { status: 404 });
  assert.equal(await helpers.downloadFileWithProgress("https://example.invalid/missing", join(downloadDir, "missing.bin")), false);
} finally {
  console.log = originalLog;
  globalThis.fetch = originalFetch;
}

const source = readFileSync(cli, "utf8");
assert.doesNotMatch(source, /shell:\s*(?:true|isWindows|!isWindows)/, "DEP0190-prone shell execution must not return");
assert.match(source, /Received .*MB\/s/);

const readme = readFileSync(join(root, "README.md"), "utf8");
const npmReadme = readFileSync(join(root, "README.npm.md"), "utf8");
const installer = readFileSync(join(root, "install.ps1"), "utf8");
assert.match(readme, /onboard --yes/);
assert.match(npmReadme, /onboard --yes/);
assert.match(readme, /Out-File -Encoding utf8/);
assert.match(installer, /\$global:OutputEncoding = \$utf8/);

console.log("CLI onboarding, command safety, progress, and encoding guidance OK");
