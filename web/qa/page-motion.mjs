import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:8137";
const WIDTHS = [320, 360, 390, 430, 768, 1024, 1440, 1920];
const REPRESENTATIVE_TARGETS = ["timeline", "self-model", "assistant-compare", "search", "memory"];
const out = process.env.SB_QA_OUT ?? "../.local/design-v7";
await mkdir(out, { recursive: true });

const report = { widths: [], navigation: [], motion: [], accessibility: [], recordings: [], checks: 0 };
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

async function openMenu(page) {
  const trigger = page.getByRole("button", { name: "Разделы", exact: true });
  check(await trigger.count() === 1, "v7 disclosure trigger Разделы is missing");
  if (await page.locator("#section-dropdown").count() === 0) await trigger.click();
  await page.waitForTimeout(300);
  const dropdown = page.locator("#section-dropdown");
  check(await dropdown.count() === 1, "v7 section-dropdown is missing after opening Разделы");
  check(await dropdown.isVisible(), "v7 section-dropdown is not visible");
  return { trigger, dropdown };
}

async function decorationStates(page) {
  return page.evaluate(() => [...document.querySelectorAll(".cinematic-hero, .page-atmosphere, .pillar-backdrop")]
    .flatMap((element) => element.getAnimations({ subtree: true }))
    .filter((animation) => animation.animationName && animation.effect?.target?.closest?.(".cinematic-hero, .page-atmosphere, .pillar-backdrop"))
    .map((animation) => ({ name: animation.animationName, state: animation.playState, time: animation.currentTime })));
}

try {
  for (const width of WIDTHS) {
    const context = await browser.newContext({
      viewport: { width, height: width < 768 ? 844 : 1000 },
      isMobile: width < 768,
      hasTouch: width < 768,
    });
    await guardLoopback(context);
    const page = await context.newPage();
    await page.goto(ORIGIN, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(900);

    check(await page.locator(".motion-world").count() === 1, "PageMotion root is missing");
    check(await page.locator(".cinematic-hero").count() === 1, "v7 cinematic hero is missing");
    check(await page.locator(".fold-section").count() === 7, "v7 disclosure sections are missing");
    check(await page.locator(".pillars").count() === 1, "v7 memory/growth surface is missing");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `horizontal overflow at ${width}px`);
    check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "true", `hero is not moving at ${width}px`);

    const { trigger, dropdown } = await openMenu(page);
    const menu = await dropdown.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight,
        overflow: document.documentElement.scrollWidth > innerWidth,
        smallTargets: [...element.querySelectorAll("a, button, input")]
          .filter((item) => { const itemRect = item.getBoundingClientRect(); return itemRect.width < 44 || itemRect.height < 44; }).length,
      };
    });
    check(menu.left >= 0 && menu.right <= menu.viewportWidth && menu.top >= 0 && menu.bottom <= menu.viewportHeight, `dropdown escapes viewport at ${width}px`);
    check(!menu.overflow && menu.smallTargets === 0, `dropdown has overflow or small target at ${width}px`);
    check(await dropdown.getByRole("switch", { name: "Анимация", exact: true }).count() === 1, "v7 animation switch is missing");
    const hrefs = await dropdown.locator("nav a").evaluateAll((links) => links.map((link) => link.getAttribute("href")));
    check(hrefs.length >= 10, "v7 disclosure navigation has too few current sections");
    for (const target of ["capture", "decision-journal", "timeline", "self-model", "simulate-me", "assistant-compare", "self-retrieval", "search", "diagnostics", "memory", "growth"]) {
      check(hrefs.includes(`#${target}`), `v7 disclosure target #${target} is missing`);
    }
    check(await page.getByRole("button", { name: /Приостановить эффекты|Остановить движение|Включить эффекты|Включить движение/ }).count() === 0, "retired motion button is still exposed");
    report.widths.push({ width, menu });

    if (width === 1440 || width === 390) {
      await page.screenshot({ path: `${out}/menu-${width}.png` });
      const axeMenu = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      check(axeMenu.violations.length === 0, `axe violations in menu at ${width}px`);
      report.accessibility.push({ width, state: "menu", violations: axeMenu.violations });

      await page.keyboard.press("Escape");
      check(await page.evaluate(() => document.activeElement?.classList.contains("sections-trigger")), "Escape did not return focus to Разделы");
      await trigger.press("Enter");
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      check(await page.locator("#section-dropdown nav a").first().evaluate((element) => element === document.activeElement), "keyboard focus did not enter disclosure links");
      await page.keyboard.press("Enter");
      check(await page.evaluate(() => document.activeElement?.id) === "capture", "keyboard disclosure link did not focus capture");
      check(await page.locator("#section-dropdown").count() === 0, "disclosure menu stayed open after selecting a section");

      for (const target of REPRESENTATIVE_TARGETS) {
        const menuState = await openMenu(page);
        await menuState.dropdown.locator(`a[href="#${target}"]`).click();
        check(await page.evaluate(() => document.activeElement?.id) === target, `disclosure did not focus #${target}`);
        check(await page.locator("#section-dropdown").count() === 0, `disclosure stayed open after #${target}`);
        report.navigation.push({ width, target });
      }

      await page.evaluate(() => scrollTo(0, 0));
      await page.waitForTimeout(350);
      const running = await decorationStates(page);
      check(running.some((animation) => animation.state === "running"), `no decorative animation is running at ${width}px`);

      const motionMenu = await openMenu(page);
      const motionSwitch = motionMenu.dropdown.getByRole("switch", { name: "Анимация", exact: true });
      check(await motionSwitch.isChecked(), "Анимация is not enabled by default");
      await motionSwitch.uncheck();
      check(await page.locator(".motion-world").getAttribute("data-page-motion") === "false", "Анимация did not pause PageMotion");
      await page.waitForTimeout(250);
      const paused = await decorationStates(page);
      check(paused.length > 0 && paused.every((animation) => animation.state !== "running"), "paused PageMotion left a decorative animation running");
      report.motion.push({ width, state: "paused", animations: paused.length });
      await motionSwitch.check();
      check(await page.locator(".motion-world").getAttribute("data-page-motion") === "true", "Анимация did not resume PageMotion");
      await page.keyboard.press("Escape");

      await page.locator("#search").scrollIntoViewIfNeeded();
      await page.waitForTimeout(500);
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "false", "offscreen hero kept moving");
      report.motion.push({ width, state: "offscreen", hero: "paused" });

      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.waitForTimeout(300);
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "false", "reduced-motion hero kept moving");
      const reduced = await decorationStates(page);
      check(reduced.every((animation) => animation.state !== "running"), "reduced-motion left decorative animation running");
      const axeReduced = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      check(axeReduced.violations.length === 0, `axe violations in reduced-motion at ${width}px`);
      report.motion.push({ width, state: "reduced-motion", animations: reduced.length });
      report.accessibility.push({ width, state: "reduced-motion", violations: axeReduced.violations });
    }
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
    await page.getByRole("button", { name: "Разделы", exact: true }).click();
    check(await page.getByRole("switch", { name: "Анимация", exact: true }).count() === 1, "recording menu lost Анимация switch");
    await page.getByRole("switch", { name: "Анимация", exact: true }).uncheck();
    await page.waitForTimeout(220);
    await page.getByRole("switch", { name: "Анимация", exact: true }).check();
    await page.keyboard.press("Escape");
    await page.locator("#capture").scrollIntoViewIfNeeded();
    await page.waitForTimeout(450);
    await page.locator("#search").scrollIntoViewIfNeeded();
    await page.waitForTimeout(450);
    await page.screenshot({ path: `${out}/page-motion-${width}.png` });
    const video = page.video();
    await context.close();
    if (video) await video.saveAs(`${out}/page-motion-${width}.webm`);
    report.recordings.push({ width, file: `page-motion-${width}.webm` });
  }
} finally {
  report.checks = checks;
  await writeFile(`${out}/page-motion-report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}

assert(checks > 0, "page-motion.mjs executed zero checks");
console.log(`v7 page motion, disclosure, switch, reduced-motion and AA checks passed (${checks} checks)`);
