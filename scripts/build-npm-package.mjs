import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "dist", "npm");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

for (const path of [
  "cli", "defaults", "server.py", "index.html", "app-logo.svg", "mermaid.min.js",
  "make-mindmap.py", "make-slides.sh", "slides-template.html",
  "slide-work-template.html", "slide-work-pattern-examples.html", "slide-work-guide.md", "install.sh",
  "install.ps1", "LICENSE", "VERSION"
]) cpSync(join(root, path), join(out, path), { recursive: true });

cpSync(join(root, "README.npm.md"), join(out, "README.md"));
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
delete pkg.scripts;
delete pkg.files;
writeFileSync(join(out, "package.json"), JSON.stringify(pkg, null, 2) + "\n");

console.log(`Public npm package staged at ${out}`);
