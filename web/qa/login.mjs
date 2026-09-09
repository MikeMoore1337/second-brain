// Browser/a11y checks for the public single-owner login surface.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:8137";
assert.equal(new URL(origin).hostname, "127.0.0.1", "Only a local synthetic server is permitted");
const out = resolve(process.env.SB_QA_OUT ?? "../.local/design-v4");
await mkdir(out, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.SB_QA_CHROMIUM,
});
const report = { widths: [], errors: [], accessibility: [] };

async function guard(context) {
  await context.route("**/*", (route) =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort(),
  );
}

async function layout(page) {
  return page.evaluate(() => ({
    width: innerWidth,
    overflow: document.documentElement.scrollWidth > innerWidth,
    smallTargets: [...document.querySelectorAll("a,button,input,summary")]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && (rect.width < 43.5 || rect.height < 43.5);
      })
      .map((element) => ({
        text: element.textContent?.trim().slice(0, 60),
        width: element.getBoundingClientRect().width,
        height: element.getBoundingClientRect().height,
      })),
  }));
}

try {
  for (const width of [320, 360, 390, 430, 768, 1024, 1440]) {
    const context = await browser.newContext({
      viewport: { width, height: width < 768 ? 844 : 1000 },
      isMobile: width < 768,
      hasTouch: width < 768,
    });
    await guard(context);
    const page = await context.newPage();
    page.on("pageerror", (error) => report.errors.push(error.message));
    await page.goto(`${origin}/login`);
    await page.locator("[data-auth-screen]").waitFor();
    await page.screenshot({ path: `${out}/login-${width}.png` });

    const currentLayout = await layout(page);
    report.widths.push(currentLayout);
    assert.equal(currentLayout.overflow, false);
    assert.equal(currentLayout.smallTargets.length, 0);
    await page.getByRole("link", { name: "Войти через GitHub" }).waitFor();
    assert.equal(await page.locator("input,textarea,select").count(), 0);

    if (width === 390) {
      await page.keyboard.press("Tab");
      assert.equal(await page.locator(".login-brand").evaluate((element) => element === document.activeElement), true);
      await page.keyboard.press("Tab");
      const action = page.locator("[data-login-action]");
      assert.equal(await action.evaluate((element) => element === document.activeElement), true);
      assert.notEqual(await action.evaluate((element) => getComputedStyle(element).outlineStyle), "none");

      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.reload();
      await page.locator("[data-auth-screen]").waitFor();
      assert.equal(await page.locator(".login-brain").evaluate((element) => getComputedStyle(element).animationName), "none");
      assert.equal(await page.locator(".login-scene-glow-one").evaluate((element) => getComputedStyle(element).animationName), "none");
    }

    const accessibility = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    report.accessibility.push({ width, violations: accessibility.violations });
    assert.equal(accessibility.violations.length, 0);

    if (width === 390) {
      await page.goto(`${origin}/login?error=denied&token=not-for-display`);
      await page.getByRole("alert").waitFor();
      assert.match(await page.getByRole("alert").textContent(), /Доступ закрыт/);
      assert.doesNotMatch(await page.locator("body").textContent(), /not-for-display/);
      await page.screenshot({ path: `${out}/login-denied-${width}.png` });
    }
    await context.close();
  }

  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await guard(context);
  const page = await context.newPage();
  await page.goto(origin);
  assert.equal(await page.locator("body").getAttribute("data-second-brain-auth-mode"), "disabled");
  assert.equal(await page.locator(".account-menu").count(), 0);
  await context.close();
} finally {
  await writeFile(`${out}/login-report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}

assert.equal(report.errors.length, 0);
assert.equal(report.accessibility.some((entry) => entry.violations.length > 0), false);
console.log("login surface layout and accessibility passed");
