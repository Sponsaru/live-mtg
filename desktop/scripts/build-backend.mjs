import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "..");
const source = resolve(desktop, "..");
const output = join(desktop, "build", "backend");
const work = join(desktop, "build", "pyinstaller");
const binaries = join(desktop, "src-tauri", "binaries");
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const triple = execFileSync("rustc", ["--print", "host-tuple"], { encoding: "utf8" }).trim();
const extension = process.platform === "win32" ? ".exe" : "";
const separator = process.platform === "win32" ? ";" : ":";

rmSync(output, { recursive: true, force: true });
rmSync(work, { recursive: true, force: true });
mkdirSync(output, { recursive: true });
mkdirSync(work, { recursive: true });
mkdirSync(binaries, { recursive: true });

const addData = (path, destination = ".") => ["--add-data", `${join(source, path)}${separator}${destination}`];
const args = [
  "-m", "PyInstaller",
  "--noconfirm", "--clean", "--onefile",
  "--name", "live-mtg-backend",
  "--distpath", output,
  "--workpath", work,
  "--specpath", work,
  ...addData("index.html"),
  ...addData("brand-logo.png"),
  ...addData("app-icon.png"),
  ...addData("mermaid.min.js"),
  ...addData("make-mindmap.py"),
  ...addData("slides-template.html"),
  ...addData("make-slides.sh"),
  ...addData("VERSION"),
  ...addData("desktop/resources/playbooks", "playbooks"),
  join(source, "server.py")
];

execFileSync(python, args, {
  stdio: "inherit",
  env: { ...process.env, PYINSTALLER_CONFIG_DIR: join(desktop, "build", "pyinstaller-cache") }
});
const built = join(output, `live-mtg-backend${extension}`);
if (!existsSync(built)) throw new Error(`Backend was not created: ${built}`);
const target = join(binaries, `live-mtg-backend-${triple}${extension}`);
copyFileSync(built, target);
console.log(`Sidecar ready: ${target}`);
