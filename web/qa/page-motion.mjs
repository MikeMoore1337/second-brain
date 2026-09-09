import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:8137";
const WIDTHS = [320, 360, 390, 430, 768, 1024, 1440, 1920];
const REPRESENTATIVE_TARGETS = ["timeline", "self-model", "assistant-compare", "search", "memory"];
const HERO_ANIMATION = { selector: ".brain-region-front", name: "thought-awaken", key: "brain-region-front:thought-awaken" };
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

async function readHeroAnimation(page) {
  return page.locator(HERO_ANIMATION.selector).evaluate((element, expected) => {
    const animation = element.getAnimations().find((candidate) => candidate.animationName === expected.name);
    if (!animation) return null;
    return { key: `${expected.className}:${animation.animationName}`, name: animation.animationName, state: animation.playState, currentTime: animation.currentTime };
  }, { name: HERO_ANIMATION.name, className: HERO_ANIMATION.selector.slice(1) });
}

async function waitForHeroAnimation(page, predicate, message, timeout = 2500) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() <= deadline) {
    last = await readHeroAnimation(page);
    if (last && predicate(last)) return last;
    await page.waitForTimeout(50);
  }
  throw new Error(`${message}; last=${JSON.stringify(last)}`);
}

async function observeHeroAnimation(page, duration = 250) {
  const deadline = Date.now() + duration;
  let last = await readHeroAnimation(page);
  while (Date.now() < deadline) {
    await page.waitForTimeout(Math.min(50, deadline - Date.now()));
    last = await readHeroAnimation(page);
  }
  return last;
}

async function waitForHeroInView(page, timeout = 2500) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() <= deadline) {
    last = await page.locator(".cinematic-hero").evaluate((element) => ({
      visible: Boolean(element.getClientRects().length),
      inView: element.dataset.inView,
      moving: element.dataset.moving,
    }));
    if (last.visible && last.inView === "true" && last.moving === "true") return last;
    await page.waitForTimeout(50);
  }
  throw new Error(`hero did not return to the viewport; last=${JSON.stringify(last)}`);
}

async function waitForNoRunningDecorations(page, message, timeout = 2500) {
  const deadline = Date.now() + timeout;
  let last = [];
  while (Date.now() <= deadline) {
    last = await decorationStates(page);
    if (last.every((animation) => animation.state !== "running")) return last;
    await page.waitForTimeout(50);
  }
  throw new Error(`${message}; last=${JSON.stringify(last)}`);
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
      await waitForHeroInView(page);
      const running = await waitForHeroAnimation(page, (animation) => animation.key === HERO_ANIMATION.key && animation.state === "running" && typeof animation.currentTime === "number", `hero animation ${HERO_ANIMATION.name} did not start at ${width}px`);
      check(running.key === HERO_ANIMATION.key, `hero animation identity changed at ${width}px`);
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "true", `hero movement state is not enabled at ${width}px`);
      report.motion.push({ width, state: "running", animation: running });

      const motionMenu = await openMenu(page);
      const motionSwitch = motionMenu.dropdown.getByRole("switch", { name: "Анимация", exact: true });
      check(await motionSwitch.isChecked(), "Анимация is not enabled by default");
      await motionSwitch.uncheck();
      check(await page.locator(".motion-world").getAttribute("data-page-motion") === "false", "Анимация did not pause PageMotion");
      const paused = await waitForHeroAnimation(page, (animation) => animation.key === running.key && animation.state !== "running" && typeof animation.currentTime === "number", "the selected hero animation did not pause");
      check(paused.key === running.key && paused.state !== "running", "paused PageMotion changed the selected animation");
      const pausedAfter = await observeHeroAnimation(page);
      check(pausedAfter?.key === running.key && pausedAfter.state !== "running", "paused PageMotion left the selected animation running");
      check(typeof pausedAfter?.currentTime === "number" && Math.abs(pausedAfter.currentTime - paused.currentTime) < 1, "paused hero animation currentTime was not stable");
      report.motion.push({ width, state: "paused", animation: paused, stableAnimation: pausedAfter });
      await motionSwitch.check();
      check(await page.locator(".motion-world").getAttribute("data-page-motion") === "true", "Анимация did not resume PageMotion");
      const resumed = await waitForHeroAnimation(page, (animation) => animation.key === running.key && animation.state === "running" && typeof animation.currentTime === "number" && typeof pausedAfter?.currentTime === "number" && animation.currentTime > pausedAfter.currentTime + 1, "the selected hero animation did not resume or advance");
      check(resumed.key === running.key && resumed.state === "running", "resumed PageMotion changed the selected animation");
      check(typeof resumed.currentTime === "number" && typeof pausedAfter?.currentTime === "number" && resumed.currentTime > pausedAfter.currentTime + 1, "resumed hero animation currentTime did not advance");
      report.motion.push({ width, state: "resumed", animation: resumed });
      await page.keyboard.press("Escape");

      await page.locator("#search").scrollIntoViewIfNeeded();
      const offscreen = await waitForHeroAnimation(page, (animation) => animation.key === running.key && animation.state !== "running", "offscreen hero animation kept running");
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "false", "offscreen hero kept moving");
      const offscreenAfter = await observeHeroAnimation(page);
      check(offscreen.key === running.key && offscreen.state !== "running", "offscreen check did not observe the selected animation");
      check(offscreenAfter?.key === running.key && offscreenAfter.state !== "running", "offscreen hero animation resumed unexpectedly");
      check(typeof offscreenAfter?.currentTime === "number" && typeof offscreen.currentTime === "number" && Math.abs(offscreenAfter.currentTime - offscreen.currentTime) < 1, "offscreen hero animation currentTime was not stable");
      report.motion.push({ width, state: "offscreen", animation: offscreen, stableAnimation: offscreenAfter });

      await page.evaluate(() => scrollTo(0, 0));
      await waitForHeroInView(page);
      const preReduced = await waitForHeroAnimation(page, (animation) => animation.key === running.key && animation.state === "running" && typeof animation.currentTime === "number" && typeof offscreenAfter?.currentTime === "number" && animation.currentTime > offscreenAfter.currentTime + 1, "hero animation did not resume before reduced-motion check");
      check(await page.locator(".motion-world").getAttribute("data-page-motion") === "true", "user pause remained enabled before reduced-motion check");
      const preReducedMenu = await openMenu(page);
      const preReducedSwitch = preReducedMenu.dropdown.getByRole("switch", { name: "Анимация", exact: true });
      check(await preReducedSwitch.isChecked(), "Анимация was not enabled before reduced-motion check");
      await page.keyboard.press("Escape");
      report.motion.push({ width, state: "before-reduced-motion", animation: preReduced });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.waitForFunction(() => document.querySelector(".cinematic-hero")?.getAttribute("data-moving") === "false", undefined, { timeout: 2500 });
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "false", "reduced-motion hero kept moving");
      const reduced = await waitForNoRunningDecorations(page, "reduced-motion left a decorative animation running");
      const reducedHero = await readHeroAnimation(page);
      check(reducedHero === null || (reducedHero.key === running.key && reducedHero.state !== "running"), "reduced-motion did not stop the selected hero animation");
      await page.waitForFunction(() => document.querySelector(".motion-preference")?.textContent?.trim() === "Движение отключено настройкой устройства", undefined, { timeout: 2500 });
      check((await page.locator(".motion-preference").textContent())?.trim() === "Движение отключено настройкой устройства", "reduced-motion did not expose the Russian explanation in the hero");

      // Motion's useReducedMotion reads the media preference on mount. Reloading
      // this isolated page makes the disclosure replacement observable without
      // changing the production component or relying on a stale hook snapshot.
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: "Разделы", exact: true }).waitFor({ state: "visible", timeout: 2500 });
      check(await page.locator(".cinematic-hero").getAttribute("data-moving") === "false", "reduced-motion hero moved after reload");
      const reducedMenu = await openMenu(page);
      check(await reducedMenu.dropdown.getByText("Движение отключено настройкой устройства", { exact: true }).count() === 1, "reduced-motion did not replace the switch with the Russian explanation");
      check(await reducedMenu.dropdown.getByRole("switch", { name: "Анимация", exact: true }).count() === 0, "reduced-motion still exposed the animation switch");
      const axeReduced = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      check(axeReduced.violations.length === 0, `axe violations in reduced-motion at ${width}px`);
      report.motion.push({ width, state: "reduced-motion", animations: reduced.length, heroAnimation: reducedHero });
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
