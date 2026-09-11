import assert from "node:assert/strict";

import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:4173";
const parsedOrigin = new URL(origin);
assert.equal(parsedOrigin.hostname, "127.0.0.1", "Only a local synthetic server is permitted");

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.SB_QA_CHROMIUM,
});
const context = await browser.newContext();
const page = await context.newPage();

try {
  const onlineResponse = await page.goto(`${origin}/`, { waitUntil: "networkidle" });
  assert.equal(onlineResponse?.status(), 200);
  await page.evaluate(async () => {
    if (!("serviceWorker" in navigator)) throw new Error("service worker API is unavailable");
    await navigator.serviceWorker.ready;
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);

  const onlineContract = await page.evaluate(async () => {
    const manifestLink = document.querySelector('link[rel="manifest"]');
    const manifest = await fetch(manifestLink?.href ?? "/manifest.webmanifest").then((response) => response.json());
    const icon = await fetch("/icons/pwa-192.png");
    return {
      manifestDisplay: manifest.display,
      manifestStartUrl: manifest.start_url,
      manifestShortcutUrls: manifest.shortcuts.map(({ url }) => url),
      iconStatus: icon.status,
      iconContentType: icon.headers.get("content-type"),
      controlled: navigator.serviceWorker.controller !== null,
    };
  });
  assert.deepEqual(onlineContract, {
    manifestDisplay: "standalone",
    manifestStartUrl: "/",
    manifestShortcutUrls: ["/#capture", "/#search"],
    iconStatus: 200,
    iconContentType: "image/png",
    controlled: true,
  });

  await context.setOffline(true);
  const offlineResponse = await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });
  assert.equal(offlineResponse?.status(), 200);
  assert.match(await page.locator("body").innerText(), /Сеть недоступна/);
  assert.equal(await page.locator("#root").count(), 0);

  const privateApi = await page.evaluate(async () => {
    try {
      const response = await fetch("/api/search");
      return { ok: true, status: response.status, contentType: response.headers.get("content-type") };
    } catch {
      return { ok: false };
    }
  });
  assert.deepEqual(privateApi, { ok: false });
} finally {
  await context.close();
  await browser.close();
}

console.log("PWA browser QA passed: install metadata, service-worker control, offline fallback, and API isolation.");
