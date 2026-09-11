import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const distDir = path.resolve(fileURLToPath(new URL("../dist/", import.meta.url)));
const [sourceServiceWorker, sourcePwa, viteConfig] = await Promise.all([
  readFile(path.resolve(fileURLToPath(new URL("../src/sw.ts", import.meta.url))), "utf8"),
  readFile(path.resolve(fileURLToPath(new URL("../src/pwa.ts", import.meta.url))), "utf8"),
  readFile(path.resolve(fileURLToPath(new URL("../vite.config.ts", import.meta.url))), "utf8"),
]);

async function readDistText(fileName) {
  return readFile(path.join(distDir, fileName), "utf8");
}

async function assertPngDimensions(fileName, expectedSize) {
  const bytes = await readFile(path.join(distDir, "icons", fileName));
  assert.deepEqual(
    [...bytes.subarray(0, 8)],
    [137, 80, 78, 71, 13, 10, 26, 10],
    `${fileName} must be a PNG file`,
  );
  assert.equal(bytes.readUInt32BE(16), expectedSize, `${fileName} width`);
  assert.equal(bytes.readUInt32BE(20), expectedSize, `${fileName} height`);
}

const [manifestText, indexHtml, serviceWorker, offlineHtml, offlineCss] = await Promise.all([
  readDistText("manifest.webmanifest"),
  readDistText("index.html"),
  readDistText("sw.js"),
  readDistText("offline.html"),
  readDistText("offline.css"),
]);
const builtScripts = await Promise.all(
  (await readdir(path.join(distDir, "assets")))
    .filter((fileName) => fileName.endsWith(".js"))
    .map((fileName) => readFile(path.join(distDir, "assets", fileName), "utf8")),
);
const builtJavaScript = builtScripts.join("\n");

const manifest = JSON.parse(manifestText);
assert.equal(manifest.name, "Second Brain");
assert.equal(manifest.short_name, "Second Brain");
assert.equal(manifest.lang, "ru");
assert.equal(manifest.start_url, "/");
assert.equal(manifest.scope, "/");
assert.equal(manifest.display, "standalone");
assert.equal(manifest.orientation, "any");
assert.equal(manifest.theme_color, "#050407");
assert.equal(manifest.background_color, "#050407");
assert.deepEqual(
  manifest.shortcuts.map(({ url }) => url),
  ["/#capture", "/#search"],
  "shortcuts must stay on existing hash routes",
);

const iconSpecs = [
  ["pwa-192.png", 192],
  ["pwa-512.png", 512],
  ["pwa-512-maskable.png", 512],
  ["apple-touch-icon-180.png", 180],
  ["favicon-32.png", 32],
  ["shortcut-add.png", 96],
  ["shortcut-search.png", 96],
];
await Promise.all(iconSpecs.map(([fileName, size]) => assertPngDimensions(fileName, size)));

assert.equal(
  (indexHtml.match(/<link rel="manifest"/g) ?? []).length,
  1,
  "the production shell must contain one manifest link",
);
assert.match(indexHtml, /href="\/icons\/apple-touch-icon-180\.png"/);
assert.match(indexHtml, /href="\/icons\/favicon-32\.png"/);
assert.match(builtJavaScript, /serviceWorker/);

assert.match(serviceWorker, /precache-v2/);
assert.match(serviceWorker, /offline\.html/);
assert.match(serviceWorker, /SKIP_WAITING/);
assert.match(serviceWorker, /clients\.claim/);
assert.match(serviceWorker, /icons\/pwa-512-maskable\.png/);
assert.match(serviceWorker, /manifest\.webmanifest/);

// The custom source is the privacy boundary: only the generated static shell is
// precached, navigation is network-first without shell caching, and private
// API/auth paths are returned to the browser untouched.
assert.match(sourceServiceWorker, /precacheAndRoute\(self\.__WB_MANIFEST\)/);
assert.match(sourceServiceWorker, /if \(isApiOrAuthPath\(url\.pathname\)\) return/);
assert.match(sourceServiceWorker, /if \(!isNavigationRequest\(request\)\) return/);
assert.doesNotMatch(sourceServiceWorker, /\b(?:CacheFirst|NetworkFirst|StaleWhileRevalidate)\b/);
assert.doesNotMatch(sourceServiceWorker, /\bcaches\.(?:open|put)\s*\(/);
assert.doesNotMatch(sourceServiceWorker, /\b(?:localStorage|sessionStorage|indexedDB)\b/i);
assert.match(viteConfig, /registerType: "prompt"/);
assert.match(sourcePwa, /onNeedRefresh/);
assert.match(sourcePwa, /updateServiceWorker\(true\)/);
assert.doesNotMatch(sourcePwa, /location\.reload/);

assert.doesNotMatch(offlineHtml, /<script\b/i, "offline page must not execute private code");
assert.match(offlineHtml, /Сеть недоступна/);
assert.match(offlineHtml, /не хранит личные заметки/i);
assert.match(offlineHtml, /href="\/"/);
assert.match(offlineCss, /safe-area-inset/);

console.log("PWA artifact QA passed: manifest, icons, shell links, service worker boundary, and offline fallback.");
