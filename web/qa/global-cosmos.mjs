// Local browser evidence: no backend, user data, network providers or writes.
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';

const origin = process.env.SB_QA_URL ?? 'http://127.0.0.1:5186';
assert.equal(new URL(origin).hostname, '127.0.0.1');
const out = resolve(process.env.SB_QA_OUT ?? '../.local/global-cosmos');
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: process.env.SB_QA_CHANNEL ?? 'msedge' });
const report = { viewports: [], motion: [], errors: [] };
async function open(width, height, reducedMotion = 'no-preference', noWebGL = false) {
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  if (noWebGL) await context.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
      return type === 'webgl' ? null : original.call(this, type, ...args);
    };
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(origin); await page.locator('.global-cosmos').waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(900);
  return { context, page };
}
const time = page => page.locator('.cosmos-gas').getAttribute('data-time');
try {
  for (const [width, height] of (process.env.SB_QA_MOTION_ONLY ? [] : [[1440, 900], [1920, 1080], [2560, 1440], [390, 844], [375, 844], [768, 1024]])) {
    const { context, page } = await open(width, height);
    assert.equal(await page.locator('.global-cosmos').count(), 1);
    assert.equal(await page.locator('.cinematic-hero canvas,.page-atmosphere,.stage-particles,.stage-haze').count(), 0);
    const rendering = await page.locator('.cosmos-gas').evaluate(e => ({width:e.width,height:e.height,hidden:e.hidden}));
    assert.equal(rendering.hidden,false);
    assert.ok(rendering.width >= width && rendering.height >= height, 'No viewport upscaling at tested resolutions');
    const glass = await page.locator('.fold-section').first().evaluate(e=>({fill:getComputedStyle(e).backgroundColor,blur:getComputedStyle(e).backdropFilter}));
    assert.match(glass.blur,/blur/);
    const checkpoints = [0, 600, 950, 1500, 2000, 100000];
    for (const [index, y] of checkpoints.entries()) {
      await page.evaluate(y => window.scrollTo(0, y), y); await page.waitForTimeout(220);
      const geometry = await page.locator('.global-cosmos').evaluate(e => {
        const r = e.getBoundingClientRect();
        return { x:r.x, y:r.y, width:r.width, height:r.height, overflow:document.documentElement.scrollWidth > innerWidth };
      });
      assert.deepEqual(geometry, { x:0, y:0, width, height, overflow:false });
      await page.screenshot({ path: `${out}/${width}-${index}.png` });
    }
    await page.locator('#timeline').evaluate(e => { e.open = true; e.scrollIntoView(); });
    await page.screenshot({ path: `${out}/${width}-expanded.png` });
    const axe = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa']).analyze();
    report.viewports.push({width,height,rendering,glass,checkpoints:checkpoints.length,violations:axe.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)}))});
    await context.close();
  }
  for (const width of (process.env.SB_QA_MOTION_ONLY ? [] : [1440,390])) {
    const {context,page}=await open(width,width===1440?900:844,'reduce');
    const before=await time(page); await page.evaluate(()=>window.scrollTo(0,900)); await page.waitForTimeout(700);
    assert.equal(await time(page),before);
    assert.equal(await page.locator('.cosmos-gas').evaluate(e=>e.hidden),false);
    await page.screenshot({path:`${out}/${width}-reduced.png`});
    report.motion.push({width,reduced:'static cosmos retained'}); await context.close();
  }
  if (process.env.SB_QA_STATIC_ONLY) {
    await writeFile(`${out}/static-report.json`,JSON.stringify(report,null,2));
    await browser.close();
    process.exit(0);
  }
  const {context,page}=await open(1440,900);
  await page.getByRole('button',{name:'Разделы',exact:true}).click();
  await page.getByRole('switch',{name:'Анимация'}).uncheck();
  const paused=await time(page); await page.waitForTimeout(650); assert.equal(await time(page),paused);
  await page.getByRole('switch',{name:'Анимация'}).check();
  await page.waitForTimeout(350); assert.ok(Number(await time(page))>Number(paused));
  await page.keyboard.press('Escape');
  // Synthetic visibility event exercises the real listener without OS focus assumptions.
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  const hidden=await time(page);await page.waitForTimeout(650);assert.equal(await time(page),hidden);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await page.evaluate(()=>{window.cosmosLoss=document.querySelector('.cosmos-gas').getContext('webgl').getExtension('WEBGL_lose_context');window.cosmosLoss.loseContext();});
  await page.waitForTimeout(250);
  assert.equal(await page.locator('.cosmos-fallback').evaluate(e=>e.hidden),false);
  await page.screenshot({path:`${out}/context-loss.png`});
  await page.evaluate(()=>window.cosmosLoss.restoreContext());
  await page.waitForTimeout(600);assert.equal(await page.locator('.cosmos-gas').evaluate(e=>e.hidden),false);
  report.motion.push({pauseResume:true,syntheticVisibility:true,contextLossAndRestore:true});
  // Keep production content visible in all preceding captures. These are explicitly gas studies.
  await page.locator('.page-shell').evaluate(e => e.style.setProperty('display', 'none', 'important'));
  const started=Date.now();
  for (const seconds of [0,3,5,10,20,65,130]) {
    await page.waitForTimeout(Math.max(0,started+seconds*1000-Date.now()));
    await page.screenshot({path:`${out}/gas-study-${seconds}s.png`});
    report.motion.push({seconds,rendererTime:await time(page)});
    console.log(`Gas deformation observed: ${seconds}s`);
  }
  await context.close();
  const fallback=await open(390,844,'reduce',true);
  assert.equal(await fallback.page.locator('.cosmos-fallback').evaluate(e=>e.hidden),false);
  await fallback.page.screenshot({path:`${out}/no-webgl.png`});
  await fallback.context.close();
  assert.deepEqual(report.errors,[]);
} finally {
  await writeFile(`${out}/${process.env.SB_QA_MOTION_ONLY ? "motion-report" : "report"}.json`,JSON.stringify(report,null,2));
  await browser.close();
}
console.log(JSON.stringify(report,null,2));
