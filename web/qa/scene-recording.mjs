import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:8137";
const WIDTHS = [320, 360, 390, 430, 768, 1024, 1440, 1920];
const out = process.env.SB_QA_OUT ?? "../.local/design-v7";
await mkdir(out, { recursive: true });

const report = { widths: [], recordings: [], checks: 0 };
let checks = 0;
const check = (condition, message) => {
  assert(condition, message);
  checks += 1;
};

const browser = await chromium.launch({
  headless: true,
  ...(process.env.SB_QA_CHROMIUM ? { executablePath: process.env.SB_QA_CHROMIUM } : {}),
});

async function guardLoopback(context) {
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return url.origin === ORIGIN ? route.continue() : route.abort();
  });
}

try {
  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: width < 768 ? 844 : 1000 }, isMobile: width < 768, hasTouch: width < 768 });
    await guardLoopback(context);
    const page = await context.newPage();
    await page.goto(ORIGIN, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);

    check(await page.locator(".cinematic-hero").count() === 1, "v7 cinematic hero is missing");
    check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "true", `v7 hero is not active at ${width}px`);
    check(await page.locator(".universe-parallax [data-depth]").count() >= 5, "v7 hero depth layers are missing");
    check(await page.locator(".pillars").count() === 1, "v7 memory/growth surface is missing");
    check(await page.locator(".pillar-card").count() === 2, "v7 memory/growth cards are incomplete");
    check(await page.locator(".fold-section").count() === 7, "v7 folded workspace sections are incomplete");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `horizontal overflow at ${width}px`);
    check(await page.getByRole("button", { name: /Приостановить эффекты|Остановить движение|Включить эффекты|Включить движение/ }).count() === 0, "retired motion button is still exposed");

    const layout = await page.locator(".pillar-card").evaluateAll((cards) => cards.map((card) => {
      const text = card.querySelector("p").getBoundingClientRect();
      const meta = card.querySelector(".card-meta").getBoundingClientRect();
      const bounds = card.getBoundingClientRect();
      return { textBottom: text.bottom, metaTop: meta.top, metaBottom: meta.bottom, metaVisible: meta.width > 0 && meta.height > 0, cardBottom: bounds.bottom };
    }));
    check(layout.length === 2 && layout.every((item) => item.textBottom <= item.cardBottom && (!item.metaVisible || (item.metaTop >= item.textBottom && item.metaBottom <= item.cardBottom))), `memory/growth content escapes its card at ${width}px`);
    await page.screenshot({ path: `${out}/home-${width}.png` });
    report.widths.push({ width, layout });
    await context.close();
  }

  for (const width of [1440, 390]) {
    const context = await browser.newContext({
      viewport: { width, height: width === 390 ? 844 : 1000 },
      isMobile: width === 390,
      hasTouch: width === 390,
      recordVideo: { dir: out, size: { width, height: width === 390 ? 844 : 1000 } },
    });
    await guardLoopback(context);
    const page = await context.newPage();
    await page.goto(ORIGIN, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    const hero = page.locator(".cinematic-hero");
    await hero.scrollIntoViewIfNeeded();
    if (width >= 1024) {
      const box = await hero.boundingBox();
      check(box !== null, "desktop hero has no measurable bounds");
      if (box) await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.35);
      await page.waitForTimeout(250);
    }
    check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "true", `recorded hero is not active at ${width}px`);
    await page.screenshot({ path: `${out}/scene-${width}.png` });

    await page.getByRole("button", { name: "Разделы", exact: true }).click();
    const motionSwitch = page.getByRole("switch", { name: "Анимация", exact: true });
    check(await motionSwitch.count() === 1, "recorded disclosure is missing Анимация");
    await motionSwitch.uncheck();
    check(await page.locator(".motion-world").getAttribute("data-page-motion") === "false", "recorded pause state was not applied");
    await page.waitForTimeout(180);
    await motionSwitch.check();
    check(await page.locator(".motion-world").getAttribute("data-page-motion") === "true", "recorded resume state was not applied");
    await page.keyboard.press("Escape");
    await page.locator(".quick-nav a[href='#capture']").click();
    await page.waitForTimeout(450);
    check(await page.evaluate(() => document.activeElement?.id) === "capture", "recorded Add transition did not focus capture");
    await page.locator(".quick-nav a[href='#search']").click();
    await page.waitForTimeout(450);
    check(await page.evaluate(() => document.activeElement?.id) === "search", "recorded Search transition did not focus search");
    await page.screenshot({ path: `${out}/scene-${width}-transitions.png` });
    const video = page.video();
    await context.close();
    if (video) await video.saveAs(`${out}/scene-${width}.webm`);
    report.recordings.push({ width, file: `scene-${width}.webm` });
  }
} finally {
  report.checks = checks;
  await writeFile(`${out}/scene-recording-report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}

assert(checks > 0, "scene-recording.mjs executed zero checks");
console.log(`v7 scene composition, responsive layout, switch and transition recording passed (${checks} checks)`);
