import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

// Production React against the synthetic loopback server; no real operations.
const out = process.env.SB_QA_OUT ?? '../.local/ui-polish';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.SB_QA_CHROMIUM });
let checks = 0;
try {
  for (const width of [320, 360, 390, 430, 768, 1024, 1440, 1920]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
    await page.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:8137' ? route.continue() : route.abort());
    await page.goto('http://127.0.0.1:8137/');
    await page.locator('.fold-section').first().waitFor();
    await page.locator('.fold-section').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
    await page.locator('#simulate-me').getByRole('button', { name: 'Добавить вариант', exact: true }).click();
    await page.locator('#simulate-me textarea').first().focus();
    const state = await page.evaluate(() => {
      const style = (selector, pseudo) => getComputedStyle(document.querySelector(selector), pseudo);
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const select = box('.timeline-controls select');
      const refresh = box('.timeline-controls > button');
      const forecast = box('#simulate-me');
      const input = box('#simulate-me textarea');
      return {
        overflow: document.documentElement.scrollWidth > innerWidth,
        searchBorder: style('.search-form').borderBottomWidth,
        rail: style('.simulate-me-surface', '::before').display,
        footer: style('.footer-line').display,
        summary: style('.fold-section[open] > summary').backgroundColor,
        stage: style('.stage7-surface').backgroundImage,
        stageShadow: style('.stage7-surface').boxShadow,
        hero: style('.cinematic-hero').backgroundColor,
        mode: style('.mode-switch').backgroundColor,
        focus: style('#simulate-me textarea').outlineStyle,
        inset: [input.left - forecast.left, forecast.right - input.right],
        aligned: Math.abs(select.bottom - refresh.bottom) < 1,
        heights: [...document.querySelectorAll('.simulate-me-option-row input,.simulate-me-option-row > button')].map(node => node.getBoundingClientRect().height),
        radii: [...document.querySelectorAll('button,.quick-nav a,.universe-actions a')].filter(node => node.getClientRects().length).map(node => getComputedStyle(node).borderRadius),
      };
    });
    assert.equal(state.overflow, false, `${width}: page overflow`);
    assert.equal(state.searchBorder, '0px');
    assert.equal(state.rail, 'none');
    assert.equal(state.footer, 'none');
    assert.equal(state.summary, 'rgba(0, 0, 0, 0)');
    assert.equal(state.stage, 'none');
    assert.equal(state.stageShadow, 'none');
    assert.equal(state.hero, 'rgba(0, 0, 0, 0)');
    assert.equal(state.mode, 'rgba(0, 0, 0, 0)');
    assert.notEqual(state.focus, 'none');
    assert.ok(state.inset.every(value => value >= 7), `${width}: focus inset ${state.inset}`);
    if (width > 600) assert.ok(state.aligned, `${width}: timeline alignment`);
    assert.ok(state.heights.length >= 6 && state.heights.every(value => value === 52), `${width}: control heights ${state.heights}`);
    assert.ok(state.radii.length > 10 && state.radii.every(value => value === '12px'), `${width}: radii ${state.radii}`);
    checks += 13 + (width > 600 ? 1 : 0);
    if ([390, 1440].includes(width)) {
      for (const selector of ['#search', '#timeline', '#simulate-me', '#assistant-compare']) {
        await page.locator(selector).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${out}/${selector.slice(1)}-${width}.png` });
      }
    }
    await page.close();
  }
  console.log(`UI polish: ${checks} checks passed across 8 widths`);
} finally {
  await browser.close();
}
