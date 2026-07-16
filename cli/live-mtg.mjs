#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const defaultHome = join(homedir(), ".live-mtg");
const legacyHome = join(homedir(), "mtg-live");

function hasMeetings(dir) {
  const meetings = join(dir, "meetings");
  try { return existsSync(meetings) && readdirSync(meetings).some(name => !name.startsWith(".")); }
  catch { return false; }
}

// 初期配布版は ~/mtg-live を使っていた。新保存先に会議がまだ無く、
// 旧保存先に会議がある場合だけ旧側を採用し、「更新したら消えた」を防ぐ。
const autoLegacyHome = !process.env.LIVE_MTG_HOME
  && !hasMeetings(defaultHome) && hasMeetings(legacyHome);
const home = process.env.LIVE_MTG_HOME || (autoLegacyHome ? legacyHome : defaultHome);
const port = process.env.PORT || "8777";
const server = join(root, "server.py");
const pidFile = join(home, "server.pid");
const configFile = join(home, "config.json");
const logFile = join(home, "server.log");
const isMac = platform() === "darwin";
const isWindows = platform() === "win32";
const windowsWhisperRoot = join(home, "tools", "whisper.cpp");
const windowsModel = join(home, "models", "ggml-large-v3-turbo.bin");
const whisperWindowsRelease = {
  version: "v1.9.1",
  url: "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.1/whisper-bin-x64.zip",
  sha256: "7d8be46ecd31828e1eb7a2ecdd0d6b314feafd82163038ab6092594b0a063539"
};

mkdirSync(home, { recursive: true });

function readConfig() {
  try { return JSON.parse(readFileSync(configFile, "utf8")); } catch { return {}; }
}

function normalizeLanguage(value) {
  const language = String(value || "").trim().toLowerCase();
  return language.startsWith("en") || language === "英語" ? "en" : "ja";
}

function detectedLanguage() {
  const locale = Intl.DateTimeFormat().resolvedOptions().locale || process.env.LANG || "ja";
  return normalizeLanguage(locale);
}

function selectedLanguage() {
  // ~/mtg-live 時代のUIは日本語固定。移行時にOSロケール判定で
  // 英語へ変わらないよう、未設定の自動互換利用者だけ日本語を維持する。
  return normalizeLanguage(process.env.LIVE_MTG_LANGUAGE || readConfig().language
    || (autoLegacyHome ? "ja" : detectedLanguage()));
}

function t(ja, en) { return selectedLanguage() === "en" ? en : ja; }

function selectedProvider() {
  const value = String(process.env.AI_PROVIDER || readConfig().aiProvider || "claude").toLowerCase();
  return value === "codex" ? "codex" : "claude";
}

function saveProvider(provider) {
  const config = readConfig();
  config.aiProvider = provider;
  writeFileSync(configFile, JSON.stringify(config, null, 2) + "\n");
}

function saveLanguage(language) {
  const config = readConfig();
  config.language = normalizeLanguage(language);
  writeFileSync(configFile, JSON.stringify(config, null, 2) + "\n");
}

function setupComplete() {
  const config = readConfig();
  return ["claude", "codex"].includes(String(config.aiProvider || "").toLowerCase())
    && ["ja", "en"].includes(String(config.language || "").toLowerCase());
}

function findFile(rootDir, fileName) {
  if (!existsSync(rootDir)) return null;
  const pending = [rootDir];
  while (pending.length) {
    const dir = pending.pop();
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (name.toLowerCase() === fileName.toLowerCase()) return path;
      try { if (statSync(path).isDirectory()) pending.push(path); } catch {}
    }
  }
  return null;
}

function windowsWhisperExe() {
  return isWindows ? findFile(windowsWhisperRoot, "whisper-cli.exe") : null;
}

function effectivePath() {
  if (isMac) return [join(homedir(), ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin", process.env.PATH || ""].join(":");
  if (!isWindows) return process.env.PATH || "";
  const paths = [];
  const whisper = windowsWhisperExe();
  if (whisper) paths.push(dirname(whisper));
  if (process.env.LOCALAPPDATA) paths.push(join(process.env.LOCALAPPDATA, "Microsoft", "WinGet", "Links"));
  paths.push(process.env.PATH || "");
  return paths.join(";");
}

function commandEnv() {
  return { ...process.env, PATH: effectivePath() };
}

function commandExists(command) {
  const checker = isWindows ? "where" : "command";
  const args = isWindows ? [command] : ["-v", command];
  return spawnSync(checker, args, { shell: !isWindows, stdio: "ignore", env: commandEnv() }).status === 0;
}

function runInteractive(command, args, extraEnv = {}) {
  return spawnSync(command, args, {
    stdio: "inherit", shell: isWindows, env: { ...commandEnv(), ...extraEnv }
  }).status === 0;
}

async function confirmStep(question, assumeYes = false) {
  if (assumeYes) return true;
  if (!process.stdin.isTTY) return false;
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const answer = (await rl.question(`${question} [Y/n]: `)).trim().toLowerCase();
  rl.close();
  return !answer || answer === "y" || answer === "yes";
}

function isAiLoggedIn(provider) {
  const command = provider === "codex" ? "codex" : "claude";
  if (!commandExists(command)) return false;
  return spawnSync(command, provider === "codex" ? ["login", "status"] : ["auth", "status"],
    { stdio: "ignore", env: commandEnv() }).status === 0;
}

async function prepareAi(provider, assumeYes) {
  const command = provider === "codex" ? "codex" : "claude";
  const label = provider === "codex" ? "Codex" : "Claude Code";
  const npmPackage = provider === "codex" ? "@openai/codex" : "@anthropic-ai/claude-code";
  if (!commandExists(command)) {
    if (!await confirmStep(t(`${label}をnpmでインストールしますか？`, `Install ${label} with npm?`), assumeYes)) return false;
    if (!runInteractive("npm", ["install", "-g", npmPackage])) return false;
  }
  if (!isAiLoggedIn(provider)) {
    if (!await confirmStep(t(`${label}へログインしますか？`, `Sign in to ${label}?`), assumeYes)) return false;
    const loginArgs = provider === "codex" ? ["login"] : ["auth", "login"];
    if (!runInteractive(command, loginArgs)) return false;
  }
  return isAiLoggedIn(provider);
}

async function installWithSystemManager(label, macArgs, windowsArgs, assumeYes) {
  if (!await confirmStep(t(`${label}をインストールしますか？`, `Install ${label}?`), assumeYes)) return false;
  if (isMac) {
    if (!commandExists("brew")) {
      console.log(t("Homebrewがありません。先に https://brew.sh/ の手順でインストールしてください。", "Homebrew is missing. Install it first from https://brew.sh/."));
      return false;
    }
    return runInteractive("brew", macArgs);
  }
  if (isWindows) {
    if (!commandExists("winget")) {
      console.log(t("wingetがありません。Microsoft StoreのApp Installerを更新してください。", "winget is missing. Update App Installer from Microsoft Store."));
      return false;
    }
    return runInteractive("winget", windowsArgs);
  }
  console.log(t(`${label}は、お使いのOSのパッケージ管理ツールでインストールしてください。`, `Install ${label} using your OS package manager.`));
  return false;
}

async function prepareRuntime(assumeYes) {
  if (!pythonCommand()) {
    await installWithSystemManager("Python 3", ["install", "python@3.12"],
      ["install", "-e", "--id", "Python.Python.3.12"], assumeYes);
  }
  if (!commandExists("ffmpeg")) {
    await installWithSystemManager("ffmpeg", ["install", "ffmpeg"],
      ["install", "-e", "--id", "Gyan.FFmpeg"], assumeYes);
  }
  if (isMac && !commandExists("mlx_whisper")) {
    if (!commandExists("pipx")) {
      await installWithSystemManager("pipx", ["install", "pipx"], [], assumeYes);
    }
    if (commandExists("pipx") && await confirmStep(t("mlx-whisperをインストールしますか？", "Install mlx-whisper?"), assumeYes)) {
      const installed = runInteractive("pipx", ["install", "mlx-whisper"]);
      if (!installed) console.log(t("mlx-whisperのインストールに失敗しました。上のpipxエラーを確認し、live-mtg onboardを再実行してください。", "mlx-whisper installation failed. Review the pipx error above, then run live-mtg onboard again."));
      else if (!commandExists("mlx_whisper")) console.log(t("mlx-whisperは導入されましたが実行ファイルを検出できません。~/.local/bin を確認してください。", "mlx-whisper was installed but its executable was not detected. Check ~/.local/bin."));
    }
  }
  if (isMac && !commandExists("whispermlx") && commandExists("pipx")) {
    if (await confirmStep(t("話者分離（whispermlx）をインストールしますか？ 清書前に話者A/Bを確認できます。", "Install speaker diarization (whispermlx)? You can review Speaker A/B before polishing."), assumeYes)) {
      // whispermlx 3.12.2 は mlx-whisper 経由で古いnumbaを選ぶため、素のpip解決では
      // Python 3.10+という自身の要件と衝突する。動作確認済みの現行numbaへ上書きし、
      // Homebrew Python 3.12があれば明示してユーザーの通常環境と分離する。
      const py312 = [
        "/opt/homebrew/opt/python@3.12/bin/python3.12",
        "/usr/local/opt/python@3.12/bin/python3.12"
      ].find(existsSync);
      const args = ["install"];
      if (py312) args.push("--python", py312);
      args.push("whispermlx");
      const installed = runInteractive("pipx", args, {
        UV_OVERRIDE: join(root, "defaults", "whispermlx-overrides.txt")
      });
      if (!installed) console.log(t("whispermlxのインストールに失敗しました。従来の文字起こしはそのまま利用できます。", "whispermlx installation failed. Standard transcription remains available."));
    }
  }
  if (isWindows && !commandExists("whisper-cli") && !windowsWhisperExe()) {
    if (await confirmStep(t("Windows用whisper.cppをダウンロードしますか？（約8MB）", "Download whisper.cpp for Windows (about 8 MB)?"), assumeYes)) {
      installWindowsWhisper();
    }
  }
  if (isWindows && !existsSync(windowsModel)) {
    if (await confirmStep(t("文字起こしモデルをダウンロードしますか？（約1.6GB）", "Download the transcription model (about 1.6 GB)?"), assumeYes)) {
      downloadWindowsModel();
    }
  }
}

function powershell(script) {
  return runInteractive("powershell.exe", ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]);
}

function installWindowsWhisper() {
  const zip = join(home, `whisper-${whisperWindowsRelease.version}.zip`);
  mkdirSync(windowsWhisperRoot, { recursive: true });
  console.log(t(`whisper.cpp ${whisperWindowsRelease.version} を取得しています…`, `Downloading whisper.cpp ${whisperWindowsRelease.version}…`));
  const downloaded = powershell(`$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '${whisperWindowsRelease.url}' -OutFile '${zip.replaceAll("'", "''")}'`);
  if (!downloaded || !existsSync(zip)) return false;
  const digest = createHash("sha256").update(readFileSync(zip)).digest("hex");
  if (digest !== whisperWindowsRelease.sha256) {
    rmSync(zip, { force: true });
    console.log(t("whisper.cppの検証に失敗したため展開しませんでした。", "whisper.cpp checksum verification failed; the archive was not extracted."));
    return false;
  }
  const extracted = powershell(`Expand-Archive -LiteralPath '${zip.replaceAll("'", "''")}' -DestinationPath '${windowsWhisperRoot.replaceAll("'", "''")}' -Force`);
  rmSync(zip, { force: true });
  return extracted && Boolean(windowsWhisperExe());
}

function downloadWindowsModel() {
  mkdirSync(dirname(windowsModel), { recursive: true });
  const partial = `${windowsModel}.download`;
  const url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin";
  console.log(t("文字起こしモデルを取得しています。回線によって数分かかります…", "Downloading the transcription model. This may take several minutes…"));
  const ok = powershell(`$ProgressPreference='Continue'; Invoke-WebRequest -UseBasicParsing -Uri '${url}' -OutFile '${partial.replaceAll("'", "''")}'; Move-Item -LiteralPath '${partial.replaceAll("'", "''")}' -Destination '${windowsModel.replaceAll("'", "''")}' -Force`);
  if (!ok || !existsSync(windowsModel) || statSync(windowsModel).size < 100_000_000) {
    rmSync(partial, { force: true });
    rmSync(windowsModel, { force: true });
    console.log(t("文字起こしモデルを正しく取得できませんでした。もう一度onboardを実行してください。", "The transcription model download failed. Run live-mtg onboard again."));
    return false;
  }
  return true;
}

function pythonCommand() {
  for (const name of isWindows ? ["python", "py"] : ["python3", "python"]) {
    if (commandExists(name)) return name;
  }
  return null;
}

function runtimeEnv() {
  return {
    ...process.env,
    LIVE_MTG_DESKTOP: "1",
    RUN: home,
    MEETINGS_DIR: join(home, "meetings"),
    DRIVE_SYNC_DIR: join(home, "meetings"),
    PROFILE_MD: join(home, "profile.md"),
    PLAYBOOK_DIR: join(home, "playbooks"),
    ASR_BACKEND: process.env.ASR_BACKEND || (isMac ? "mlx" : "cpp"),
    MODEL: process.env.MODEL || (isWindows ? windowsModel : undefined),
    AI_PROVIDER: selectedProvider(),
    LIVE_MTG_LANGUAGE: selectedLanguage(),
    PATH: effectivePath(),
    PORT: port
  };
}

function openUrl(url) {
  if (isMac) spawn("open", [url], { detached: true, stdio: "ignore" }).unref();
  else if (isWindows) spawn("cmd", ["/c", "start", "", url], { detached: true, stdio: "ignore" }).unref();
  else spawn("xdg-open", [url], { detached: true, stdio: "ignore" }).unref();
}

async function fetchJson(path, timeout = 1800) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}${path}`, { signal: AbortSignal.timeout(timeout) });
    return response.ok ? await response.json() : null;
  } catch { return null; }
}

async function serviceHealth() {
  const current = await fetchJson("/api/health", 1200);
  if (current?.service === "live-mtg") return { ...current, legacy: false };
  // beta.9以前は軽量health APIがない。stateが返れば「不通」ではなく旧版。
  const legacy = await fetchJson("/api/state", 1200);
  const isLiveMtg = legacy && typeof legacy === "object" && "recording" in legacy
    && "current" in legacy && Array.isArray(legacy.sessions);
  return isLiveMtg ? { ok: true, version: null, service: "live-mtg", legacy: true } : null;
}

async function desktopHealth() {
  return fetchJson("/api/desktop-health", 12000);
}

function doctor(provider = selectedProvider()) {
  const hasMlx = commandExists("mlx_whisper");
  const hasCpp = commandExists("whisper-cli") || (isWindows && Boolean(windowsWhisperExe()));
  const asrInstalled = hasMlx || hasCpp;
  const asr = hasMlx ? "mlx_whisper" : hasCpp ? "whisper-cli" : "mlx_whisper / whisper-cli";
  const aiCommand = provider === "codex" ? "codex" : "claude";
  const aiLabel = provider === "codex" ? "Codex" : "Claude Code";
  const aiInstalled = commandExists(aiCommand);
  const aiLoggedIn = aiInstalled && spawnSync(aiCommand,
    provider === "codex" ? ["login", "status"] : ["auth", "status"],
    { stdio: "ignore", env: commandEnv() }).status === 0;
  const checks = [
    ["Node.js 20+", Number(process.versions.node.split(".")[0]) >= 20, process.version],
    ["Python 3", Boolean(pythonCommand()), "python3"],
    [aiLabel, aiInstalled, provider === "codex" ? "npm install -g @openai/codex" : "npm install -g @anthropic-ai/claude-code"],
    [t(`${aiLabel} ログイン`, `${aiLabel} sign-in`), aiLoggedIn, provider === "codex" ? "codex login" : "claude auth login"],
    ["ffmpeg", commandExists("ffmpeg"), isMac ? "brew install ffmpeg" : "winget install Gyan.FFmpeg"],
    [t(`文字起こし（${asr}）`, `Transcription (${asr})`), asrInstalled, isMac ? "pipx install mlx-whisper" : t("live-mtg onboard で自動取得", "downloaded by live-mtg onboard")],
    ...(isWindows ? [[t("文字起こしモデル", "Transcription model"), existsSync(windowsModel), t("live-mtg onboard で自動取得", "downloaded by live-mtg onboard")]] : [])
  ];
  console.log("LiveMTG doctor\n");
  for (const [label, ok, detail] of checks) console.log(`${ok ? "✓" : "✗"} ${label}${ok ? "" : ` — ${detail}`}`);
  console.log(`${commandExists("whispermlx") ? "✓" : "○"} ${t("話者分離", "Speaker diarization")}${commandExists("whispermlx") ? " (whispermlx)" : t(" — 任意: live-mtg onboardで導入", " — optional: install with live-mtg onboard")}`);
  const failed = checks.filter(([, ok]) => !ok).length;
  console.log(t(`\nデータ: ${home}`, `\nData: ${home}`));
  console.log(`AI: ${provider === "codex" ? "Codex" : "Claude Code"}`);
  console.log(t(`言語: ${selectedLanguage() === "en" ? "英語" : "日本語"}`, `Language: ${selectedLanguage() === "en" ? "English" : "Japanese"}`));
  if (failed) console.log(t(`\n${failed}項目を準備してから live-mtg doctor を再実行してください。`, `\nPrepare the ${failed} missing item(s), then run live-mtg doctor again.`));
  return failed === 0;
}

function redactDiagnostic(value) {
  let text = String(value ?? "");
  const homes = [homedir(), process.env.USERPROFILE, process.env.HOME].filter(Boolean);
  for (const path of homes) text = text.split(path).join("~");
  return text
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/(token|authorization|api[_-]?key)\s*[:=]\s*[^\s]+/gi, "$1=[redacted]");
}

function showLogs(lines = 120) {
  if (!existsSync(logFile)) {
    console.log(t(`ログはまだありません: ${logFile}`, `No logs yet: ${logFile}`));
    return;
  }
  const content = readFileSync(logFile, "utf8").split(/\r?\n/);
  console.log(content.slice(-Math.max(1, lines)).join("\n"));
}

function commandVersion(command, args = ["--version"]) {
  if (!commandExists(command)) return "not installed";
  const result = spawnSync(command, args, { encoding: "utf8", env: commandEnv() });
  return String(result.stdout || result.stderr || "unknown").trim().split(/\r?\n/)[0];
}

async function createReport() {
  const provider = selectedProvider();
  const recentErrorCount = existsSync(logFile)
    ? readFileSync(logFile, "utf8").split(/\r?\n/).filter(line => /error|fail|exception|traceback|失敗/i.test(line)).slice(-500).length
    : 0;
  const diagnostic = {
    createdAt: new Date().toISOString(),
    liveMtg: pkg.version,
    os: `${platform()} ${process.arch}`,
    node: process.version,
    provider,
    language: selectedLanguage(),
    providerVersion: commandVersion(provider === "codex" ? "codex" : "claude"),
    providerLoggedIn: isAiLoggedIn(provider),
    python: pythonCommand() || "not installed",
    ffmpeg: commandVersion("ffmpeg", ["-version"]),
    asr: isMac ? (commandExists("mlx_whisper") ? "mlx_whisper installed" : commandExists("whisper-cli") ? "whisper-cli fallback installed" : "not installed")
      : (windowsWhisperExe() ? "whisper-cli installed by LiveMTG" : commandVersion("whisper-cli", ["--help"])),
    modelReady: !isWindows || existsSync(windowsModel),
    service: await serviceHealth(),
    runtime: await desktopHealth(),
    recentErrorCount
  };
  const path = join(home, `diagnostics-${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
  writeFileSync(path, redactDiagnostic(JSON.stringify(diagnostic, null, 2)) + "\n");
  console.log(t(`診断レポートを作成しました: ${path}`, `Diagnostic report created: ${path}`));
  console.log(t("文字起こし本文・会議資料・APIキーは含めていません。送付前に内容を確認してください。", "The report excludes transcripts, meeting files, and API keys. Review it before sharing."));
}

function serve() {
  const python = pythonCommand();
  if (!python) throw new Error(t("Python 3がありません。live-mtg doctorで確認してください。", "Python 3 is missing. Run live-mtg doctor."));
  const args = python === "py" ? ["-3", "-u", server] : ["-u", server];
  writeFileSync(pidFile, String(process.pid));
  const child = spawn(python, args, { env: runtimeEnv(), stdio: "inherit" });
  const stop = signal => { if (!child.killed) child.kill(signal); };
  process.on("SIGINT", () => stop("SIGINT"));
  process.on("SIGTERM", () => stop("SIGTERM"));
  child.on("exit", code => { rmSync(pidFile, { force: true }); process.exit(code ?? 0); });
}

function macPlistPath() { return join(homedir(), "Library", "LaunchAgents", "com.rakuhub.live-mtg.plist"); }

function installDaemon() {
  if (isMac) {
    const plist = macPlistPath();
    mkdirSync(dirname(plist), { recursive: true });
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.rakuhub.live-mtg</string>
<key>ProgramArguments</key><array><string>${process.execPath}</string><string>${fileURLToPath(import.meta.url)}</string><string>serve</string></array>
<key>EnvironmentVariables</key><dict><key>LIVE_MTG_HOME</key><string>${home}</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>${join(home, "server.log")}</string>
<key>StandardErrorPath</key><string>${join(home, "server.log")}</string>
</dict></plist>`;
    spawnSync("launchctl", ["bootout", `gui/${process.getuid()}`, plist], { stdio: "ignore" });
    writeFileSync(plist, xml);
    const result = spawnSync("launchctl", ["bootstrap", `gui/${process.getuid()}`, plist], { stdio: "inherit" });
    if (result.status !== 0) throw new Error(t("LaunchAgentを登録できませんでした", "Could not register the LaunchAgent"));
  } else if (isWindows) {
    const task = `\"${process.execPath}\" \"${fileURLToPath(import.meta.url)}\" serve`;
    const result = spawnSync("schtasks", ["/Create", "/F", "/SC", "ONLOGON", "/TN", "LiveMTG", "/TR", task], { stdio: "inherit" });
    if (result.status !== 0) throw new Error(t("Windowsの自動起動を登録できませんでした", "Could not register Windows auto-start"));
    spawnSync("schtasks", ["/Run", "/TN", "LiveMTG"], { stdio: "ignore" });
  } else {
    console.log(t("Linuxのsystemd登録は今後対応します。別ターミナルで live-mtg serve を実行してください。", "Linux systemd setup is not available yet. Run live-mtg serve in another terminal."));
  }
}

async function start() {
  let running = await serviceHealth();
  const hadMacDaemon = isMac && existsSync(macPlistPath());
  let currentMacDaemon = false;
  if (hadMacDaemon) {
    try {
      const plist = readFileSync(macPlistPath(), "utf8");
      currentMacDaemon = plist.includes(fileURLToPath(import.meta.url))
        && plist.includes("<key>LIVE_MTG_HOME</key>");
    } catch {}
  }
  const currentServer = running && !running.legacy && running.version === pkg.version;
  if (currentServer && (!hadMacDaemon || currentMacDaemon)) {
    return console.log(t("LiveMTGは起動済みです", "LiveMTG is already running"));
  }
  if (running) {
    console.log(currentServer
      ? t("旧自動起動設定を現行CLIへ切り替えます…", "Updating the legacy auto-start configuration…")
      : t(`旧サーバー（${running.version || "不明"}）を ${pkg.version} へ切り替えます…`, `Restarting the old server (${running.version || "unknown"}) with ${pkg.version}…`));
    stop();
    for (let i = 0; i < 20 && await serviceHealth(); i++) await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (hadMacDaemon) {
    // 旧手動版も同じplist名を使う。内容を現行CLIへ書き換え、
    // 次回ログインで旧server.pyがKeepAlive復活するのも防ぐ。
    installDaemon();
  } else {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), "serve"], { detached: true, stdio: "ignore" });
    child.unref();
  }
  for (let i = 0; i < 20; i++) {
    await new Promise(resolve => setTimeout(resolve, 500));
    running = await serviceHealth();
    if (running && !running.legacy && running.version === pkg.version) return console.log(t(`LiveMTGを起動しました: http://127.0.0.1:${port}`, `LiveMTG started: http://127.0.0.1:${port}`));
  }
  throw new Error(t("LiveMTGを起動できませんでした。live-mtg doctorを実行してください。", "Could not start LiveMTG. Run live-mtg doctor."));
}

function stop() {
  if (isMac && existsSync(macPlistPath())) {
    spawnSync("launchctl", ["bootout", `gui/${process.getuid()}`, macPlistPath()], { stdio: "ignore" });
  } else if (isWindows) {
    spawnSync("schtasks", ["/End", "/TN", "LiveMTG"], { stdio: "ignore" });
  }
  if (existsSync(pidFile)) {
    const pid = Number(readFileSync(pidFile, "utf8"));
    try { process.kill(pid, "SIGTERM"); } catch {}
    rmSync(pidFile, { force: true });
  }
  console.log(t("LiveMTGを停止しました", "LiveMTG stopped"));
}

async function chooseProvider(requested) {
  if (requested && !["claude", "codex"].includes(requested)) {
    throw new Error(t("--provider は claude または codex を指定してください", "--provider must be claude or codex"));
  }
  let provider = requested;
  if (!provider && process.stdin.isTTY) {
    const current = selectedProvider();
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question(t(`利用するAIを選んでください [1: Claude Code / 2: Codex]（現在: ${current}）: `, `Choose your AI [1: Claude Code / 2: Codex] (current: ${current}): `))).trim();
    rl.close();
    provider = answer === "2" || answer.toLowerCase() === "codex" ? "codex" : "claude";
  }
  provider ||= selectedProvider();
  saveProvider(provider);
  return provider;
}

async function chooseLanguage(requested) {
  let language = requested ? normalizeLanguage(requested) : selectedLanguage();
  if (!requested && process.stdin.isTTY && !readConfig().language) {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question(`Language / 言語 [1: 日本語 / 2: English] (current: ${language}): `)).trim().toLowerCase();
    rl.close();
    if (answer === "2" || answer === "en" || answer === "english") language = "en";
    else if (answer === "1" || answer === "ja" || answer === "japanese" || answer === "日本語") language = "ja";
  }
  saveLanguage(language);
  return language;
}

async function onboard(install, requestedProvider, requestedLanguage, assumeYes = false) {
  await chooseLanguage(requestedLanguage);
  console.log(t("LiveMTG 初期設定\n", "LiveMTG setup\n"));
  const provider = await chooseProvider(requestedProvider);
  console.log(t(`\n${provider === "codex" ? "Codex" : "Claude Code"}を使用します。\n`, `\nUsing ${provider === "codex" ? "Codex" : "Claude Code"}.\n`));
  await prepareAi(provider, assumeYes);
  await prepareRuntime(assumeYes);
  const ok = doctor(provider);
  if (!ok) return process.exitCode = 1;
  if (install) installDaemon(); else await start();
  for (let i = 0; i < 20 && !(await serviceHealth()); i++) await new Promise(resolve => setTimeout(resolve, 500));
  openUrl(`http://127.0.0.1:${port}`);
  console.log(t("\n初期設定が完了しました。会議データは " + home + " に保存されます。", `\nSetup complete. Meeting data is stored in ${home}.`));
}

async function configure(provider, language) {
  if (language) {
    await chooseLanguage(language);
    console.log(t(`言語を${selectedLanguage() === "en" ? "英語" : "日本語"}に変更しました。`, `Language changed to ${selectedLanguage() === "en" ? "English" : "Japanese"}.`));
  }
  if (provider) {
    provider = await chooseProvider(provider);
    console.log(t(`AIを${provider === "codex" ? "Codex" : "Claude Code"}に変更しました。`, `AI changed to ${provider === "codex" ? "Codex" : "Claude Code"}.`));
  }
  if (!provider && !language) throw new Error(t("--provider または --language を指定してください", "Specify --provider or --language"));
  if (await serviceHealth()) {
    stop();
    await start();
  }
}

async function update() {
  const channel = pkg.version.includes("-") ? "beta" : "latest";
  const wasRunning = Boolean(await serviceHealth());
  console.log(t(`LiveMTGを${channel}チャンネルの最新版へ更新します…`, `Updating LiveMTG from the ${channel} channel…`));
  const result = spawnSync("npm", ["install", "-g", `live-mtg@${channel}`],
    { stdio: "inherit", shell: isWindows });
  if (result.status !== 0) process.exit(result.status ?? 1);
  if (isMac && existsSync(macPlistPath())) installDaemon();
  else if (wasRunning) {
    stop();
    for (let i = 0; i < 20 && await serviceHealth(); i++) await new Promise(resolve => setTimeout(resolve, 250));
    await start();
  }
  console.log(t("更新が完了しました", "Update complete"));
}

async function rollback(requestedVersion) {
  let version = requestedVersion;
  if (version && !/^[0-9A-Za-z.+-]+$/.test(version)) throw new Error(t("バージョンの形式が正しくありません", "Invalid version format"));
  if (!version) {
    const result = spawnSync("npm", ["view", "live-mtg", "versions", "--json"],
      { encoding: "utf8", shell: isWindows });
    if (result.status !== 0) throw new Error(t("公開済みバージョンを取得できませんでした", "Could not retrieve published versions"));
    const versions = JSON.parse(result.stdout || "[]");
    const index = versions.lastIndexOf(pkg.version);
    version = index > 0 ? versions[index - 1] : versions.filter(v => v !== pkg.version).at(-1);
  }
  if (!version) throw new Error(t("戻せる旧バージョンがありません", "No previous version is available"));
  console.log(t(`LiveMTGを ${version} へ戻します…`, `Rolling LiveMTG back to ${version}…`));
  const result = spawnSync("npm", ["install", "-g", `live-mtg@${version}`],
    { stdio: "inherit", shell: isWindows });
  if (result.status !== 0) process.exit(result.status ?? 1);
  if (isMac && existsSync(macPlistPath())) installDaemon();
  console.log(t(`ロールバックしました。live-mtg doctor で状態を確認してください。`, `Rollback complete. Run live-mtg doctor to verify the installation.`));
}

function help() {
  console.log(selectedLanguage() === "en" ? `LiveMTG

Usage:
  live-mtg onboard                     Choose AI and prepare dependencies
  live-mtg dashboard                   Open the dashboard
  live-mtg doctor                      Check required dependencies
  live-mtg config --provider codex     Switch AI (claude is also supported)
  live-mtg config --language en        Switch language (ja is also supported)
  live-mtg start | stop | restart | status
                                       Start, stop, restart, or check status
  live-mtg update                      Update to the latest release
  live-mtg logs [--lines 200]          Show server logs
  live-mtg report                      Create a privacy-safe diagnostic report
  live-mtg rollback [version]          Roll back to a previous version
  live-mtg onboard --no-daemon         Set up without auto-start
  live-mtg serve                       Run the server in the foreground
  live-mtg --version                   Show version

Issues: https://github.com/Sponsaru/live-mtg/issues` : `LiveMTG

使い方:
  live-mtg onboard                   AI選択・必要環境の準備・常駐化
  live-mtg dashboard                 画面を開く
  live-mtg doctor                    必要環境を診断
  live-mtg config --provider codex   AIをCodexへ変更（claudeも可）
  live-mtg config --language en      言語を英語へ変更（jaも可）
  live-mtg start | stop | restart | status
                                     起動・停止・再起動・状態確認
  live-mtg update                    最新版へ更新
  live-mtg logs [--lines 200]        サーバーログを表示
  live-mtg report                    個人情報を伏せた診断レポートを作成
  live-mtg rollback [version]        直前または指定バージョンへ戻す
  live-mtg onboard --no-daemon       常駐化せず初期設定
  live-mtg serve                     サーバーを手前で実行
  live-mtg --version                 バージョン表示

不具合報告: https://github.com/Sponsaru/live-mtg/issues`);
}

const args = process.argv.slice(2);
const command = args[0] || "dashboard";
const providerAt = args.indexOf("--provider");
const requestedProvider = providerAt >= 0 ? String(args[providerAt + 1] || "").toLowerCase() : undefined;
const languageAt = args.indexOf("--language");
const requestedLanguage = languageAt >= 0 ? String(args[languageAt + 1] || "").toLowerCase() : undefined;
if (requestedLanguage !== undefined && !["ja", "en", "japanese", "english", "日本語", "英語"].includes(requestedLanguage)) {
  console.error("LiveMTG: --language must be ja or en");
  process.exit(1);
}
const linesAt = args.indexOf("--lines");
const requestedLines = linesAt >= 0 ? Number(args[linesAt + 1] || 120) : 120;
try {
  if (command === "doctor") process.exitCode = doctor() ? 0 : 1;
  else if (command === "serve") serve();
  else if (command === "start") await start();
  else if (command === "stop") stop();
  else if (command === "restart") {
    stop();
    for (let i = 0; i < 20 && await serviceHealth(); i++) await new Promise(resolve => setTimeout(resolve, 250));
    await start();
  }
  else if (command === "status") console.log(await serviceHealth() ? t("LiveMTGは起動中です", "LiveMTG is running") : t("LiveMTGは停止中です", "LiveMTG is stopped"));
  else if (command === "dashboard") {
    // `npm install -g live-mtg && live-mtg` must not open a dashboard that only
    // looks usable. On first launch, choose the AI and verify every required
    // runtime before recording can begin.
    if (!setupComplete()) await onboard(true, requestedProvider, requestedLanguage);
    else { await start(); openUrl(`http://127.0.0.1:${port}`); }
  }
  else if (command === "onboard") await onboard(!args.includes("--no-daemon"), requestedProvider, requestedLanguage, args.includes("--yes"));
  else if (command === "config") await configure(requestedProvider, requestedLanguage);
  else if (command === "update") await update();
  else if (command === "logs") showLogs(Number.isFinite(requestedLines) ? requestedLines : 120);
  else if (command === "report") await createReport();
  else if (command === "rollback") await rollback(args[1]);
  else if (command === "--version" || command === "-v") console.log(pkg.version);
  else help();
} catch (error) {
  console.error(`LiveMTG: ${error.message}`);
  process.exitCode = 1;
}
