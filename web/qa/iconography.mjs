import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:8137";
const out = process.env.SB_QA_OUT ?? "../.local/design-v7";
await mkdir(out, { recursive: true });

// This list describes the local asset family. Aliases are checked through the
// React data-icon contract below and are intentionally not read as files here.
const ATLAS = [
  ["add", "Добавить", true],
  ["relation", "Связь и сравнение", true],
  ["memory", "Память", true],
  ["voice", "Голос", true],
  ["decision", "Журнал решений", true],
  ["timeline", "Хронология", true],
  ["search", "Поиск", true],
  ["self-model", "Модель себя", true],
  ["self-retrieval", "Сбор контекста", true],
  ["diagnostics", "Диагностика", true],
  ["simulate", "Прогноз", true],
  ["growth", "Развитие", true],
  ["open", "Открыть", false],
  ["refresh", "Обновить", false],
  ["close", "Закрыть", false],
  ["expand", "Раскрыть", false],
  ["copy", "Копировать", false],
  ["warning", "Предупреждение", false],
  ["info", "Информация", false],
  ["success", "Подтверждение", false],
  ["pause", "Совместимый alias pause", false],
  ["play", "Совместимый alias play", false],
];
const files = new Set(await readdir("src/assets/icons"));
let checks = 0;
const check = (condition, message) => {
  assert(condition, message);
  checks += 1;
};

const browser = await chromium.launch({
  headless: true,
  ...(process.env.SB_QA_CHROMIUM ? { executablePath: process.env.SB_QA_CHROMIUM } : {}),
});

const imageData = async (name, variant, size) => {
  const suffix = variant === "detail" ? 96 : 48;
  const file = `${name}-${variant}-${suffix}.webp`;
  const retina = `${name}-${variant}-${suffix * 2}.webp`;
  check(files.has(file) && files.has(retina), `missing local icon master ${name}/${variant}`);
  const [source, source2x] = await Promise.all([readFile(`src/assets/icons/${file}`), readFile(`src/assets/icons/${retina}`)]);
  return `<img width="${size}" height="${size}" src="data:image/webp;base64,${source.toString("base64")}" srcset="data:image/webp;base64,${source2x.toString("base64")} 2x" alt="">`;
};

const report = { staticAtlas: { assets: ATLAS.length, checks: 0 }, react: {}, checks: 0 };
try {
  const cards = await Promise.all(ATLAS.map(async ([name, label, detail]) => {
    const variant = detail ? "detail" : "compact";
    const largeSize = detail ? 64 : 48;
    return `<article><h2>${label}</h2><div>${await imageData(name, variant, largeSize)}${await imageData(name, "compact", 26)}${await imageData(name, "compact", 20)}</div><p>${largeSize} / 26 / 20 px</p></article>`;
  }));
  report.staticAtlas.checks = checks;

  const atlas = await browser.newPage({ viewport: { width: 1200, height: 1500 }, deviceScaleFactor: 1 });
  await atlas.setContent(`<style>body{background:#050407;color:#f6f0ff;font:16px system-ui;margin:32px}h1{font-size:28px}main{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}article{background:#0c0911;border:1px solid #594166;border-radius:16px;padding:16px}h2{font-size:16px;font-weight:500;margin:0 0 12px}article div{height:72px;display:flex;align-items:center;gap:24px}p{color:#b2a0c5;font-size:12px}</style><h1>Second Brain · локальная семья иконок</h1><main>${cards.join("")}</main>`);
  check(await atlas.locator("article").count() === ATLAS.length, "static icon atlas has no complete card set");
  check(await atlas.locator("article img").count() === ATLAS.length * 3, "static icon atlas has an incomplete size set");
  await atlas.screenshot({ path: `${out}/icon-family.png`, fullPage: true });
  await atlas.close();

  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
  await page.goto(ORIGIN, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const initial = await page.evaluate(() => performance.getEntriesByType("resource").map((entry) => entry.name).filter((name) => name.endsWith(".webp")));
  const initialSectionAssets = initial.filter((name) => /-(compact|detail)-/.test(name));
  check(initialSectionAssets.length < ATLAS.length, "the complete icon family loaded before lazy sections were visited");
  check(await page.locator(".quick-nav").count() === 1, "current React quick navigation is missing");
  check(await page.locator(".fold-section").count() === 7, "current React section icon hosts are missing");

  const trigger = page.getByRole("button", { name: "Разделы", exact: true });
  check(await trigger.count() === 1, "current Разделы trigger is missing");
  await trigger.click();
  const dropdown = page.locator("#section-dropdown");
  check(await dropdown.count() === 1 && await dropdown.isVisible(), "current section dropdown is missing");
  const links = dropdown.locator("nav a");
  check(await links.count() === 11, "current disclosure does not expose all sections");
  const menuIcons = await links.locator("img[data-icon]").evaluateAll((items) => items.map((item) => ({ name: item.dataset.icon, alt: item.alt, hidden: item.getAttribute("aria-hidden"), loaded: item.complete && item.naturalWidth > 0 })));
  check(menuIcons.length === 11, "current disclosure links are missing icons");
  check(menuIcons.every((item) => item.alt === "" && item.hidden === "true" && item.loaded), "current disclosure icon accessibility or loading contract failed");
  for (const link of await links.all()) await link.scrollIntoViewIfNeeded();
  const menuNames = new Set(menuIcons.map((item) => item.name));
  for (const name of ["add", "decision", "timeline", "self-model", "simulate", "relation", "self-retrieval", "search", "diagnostics", "memory", "growth"]) check(menuNames.has(name), `current disclosure icon ${name} is missing`);
  check(await dropdown.getByRole("switch", { name: "Анимация", exact: true }).count() === 1, "current animation switch is missing from disclosure");
  await page.keyboard.press("Escape");

  const navigationIcons = await page.locator(".quick-nav img[data-icon]").evaluateAll((items) => items.map((item) => ({ name: item.dataset.icon, loaded: item.complete && item.naturalWidth > 0 })));
  check(navigationIcons.length >= 3, "current quick navigation has no icon affordances");
  check(navigationIcons.every((item) => item.loaded), "current quick navigation icon failed to load");
  const visibleIcons = await page.locator("img[data-icon]").evaluateAll((items) => items.filter((item) => {
    const rect = item.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
  }).map((item) => ({ name: item.dataset.icon, alt: item.alt, hidden: item.getAttribute("aria-hidden"), loaded: item.complete && item.naturalWidth > 0 })));
  check(visibleIcons.length > 0, "current React GUI rendered zero visible data-icon elements");
  check(visibleIcons.every((item) => item.alt === "" && item.hidden === "true" && item.loaded), "current visible icons violate alt/aria-hidden/loading contract");
  await page.locator("#memory").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${out}/icons-navigation.png` });
  report.react = { initialRasterRequests: initial.map((name) => new URL(name).pathname), menuIcons, navigationIcons, visibleIcons };
} finally {
  report.checks = checks;
  await writeFile(`${out}/iconography.json`, JSON.stringify(report, null, 2));
  await browser.close();
}

assert(checks > 0, "iconography.mjs executed zero checks");
console.log(`v7 iconography asset and React checks passed (${checks} checks)`);
