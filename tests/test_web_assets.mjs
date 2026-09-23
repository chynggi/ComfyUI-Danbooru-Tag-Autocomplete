import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(REPO_ROOT, "web");

function webModules() {
  // Model how ComfyUI collects extensions: server.py globs `**/*.js` under the web directory,
  // so a module in a subdirectory is loaded too and has to be checked here.
  return readdirSync(WEB, { recursive: true })
    .filter((name) => name.endsWith(".js"))
    .map((name) => name.split(sep).join("/"))
    .sort();
}

test("every browser module parses", () => {
  const modules = webModules();
  assert.ok(modules.length >= 5, `expected the browser modules, found ${modules.length}`);
  for (const name of modules) {
    execFileSync(process.execPath, ["--check", join(WEB, name)], { stdio: "pipe" });
  }
});

test("every sibling import resolves to a file that exists", () => {
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    for (const match of source.matchAll(/from\s+"(\.[^"]+)"/g)) {
      if (!match[1].startsWith("./")) {
        continue;
      }
      assert.ok(existsSync(resolve(WEB, match[1])), `${name} imports ${match[1]}, which does not exist`);
    }
  }
});

test("the only outside imports are ComfyUI's own frontend modules", () => {
  // `../../scripts/*.js` is not on disk inside this repo: ComfyUI serves the frontend from its
  // own package at /scripts/, and this module is loaded from /extensions/danbooruTagAutocomplete/,
  // so the path is correct at runtime and unresolvable from here. Pin the allowed set to the two
  // modules the extension actually needs, so a typo is caught. The pattern sees a double-quoted
  // relative specifier, which is the only import form this repo uses; a bare package specifier,
  // a side-effect import or a dynamic import would each need their own check.
  const allowed = new Set(["../../scripts/app.js", "../../scripts/widgets.js"]);
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    for (const match of source.matchAll(/from\s+"(\.[^"]+)"/g)) {
      if (match[1].startsWith("./")) {
        continue;
      }
      assert.ok(allowed.has(match[1]), `${name} imports ${match[1]}, which is not a ComfyUI frontend module`);
    }
  }
});

test("only the entry point registers an extension", () => {
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    assert.equal(
      source.includes("app.registerExtension"),
      name === "dtautocomplete.js",
      `${name} should ${name === "dtautocomplete.js" ? "" : "not "}register an extension`,
    );
  }
});
