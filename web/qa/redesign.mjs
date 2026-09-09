// Production GUI against a separately started, synthetic-only DI server.
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const origin = process.env.SB_QA_URL ?? 'http://127.0.0.1:8137';
assert.equal(new URL(origin).hostname, '127.0.0.1', 'Only a local synthetic server is permitted');
const out = resolve(process.env.SB_QA_OUT ?? '../.local/design-v4');
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.SB_QA_CHROMIUM });
const report = { widths: [], accessibility: [], surfaces: [], scenarios: [], motion: [], errors: [] };
async function guard(context) {
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
}
async function audit(page, name) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
  report.accessibility.push({ name, violations: results.violations.map(v => ({ id:v.id, impact:v.impact, nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary})) })) });
}
async function screenshot(page, name, selector) {
  if (selector) await page.locator(selector).evaluate(e=>e.scrollIntoView({block:'start'}));
  await page.waitForTimeout(450);
  await page.screenshot({ path: `${out}/${name}.png` });
}
async function layout(page) {
  return page.evaluate(() => ({
    width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
    smallTargets: [...document.querySelectorAll('button,a,input,select,summary,.voice-file-label,.personal-memory-toggle')].filter(e=>{
      const r=e.getBoundingClientRect(); return r.width>0 && r.height>0 && !e.disabled && !e.hidden && !e.closest('[hidden]') && e.getAttribute('type')!=='checkbox' && e.getAttribute('type')!=='file' && (r.width<43.5 || r.height<43.5);
    }).map(e=>({text:e.textContent?.trim().slice(0,60),class:e.className,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})),
    fonts:[...new Set([...document.querySelectorAll('h1,h2,h3,button,input,textarea,p,label')].map(e=>getComputedStyle(e).fontFamily))],
  }));
}
try {
  for (const width of [320,360,390,430,768,1024,1440,1920]) {
    const context=await browser.newContext({viewport:{width,height:width<768?844:1000},isMobile:width<768,hasTouch:width<768});
    await guard(context); const page=await context.newPage();
    page.on('pageerror',e=>report.errors.push(e.message));
    await page.goto(origin); await page.waitForTimeout(1100);
    report.widths.push(await layout(page));
    await page.screenshot({path:`${out}/home-${width}.png`});
    if(width===390 || width===1440) {
      await page.locator('.cinematic-hero').screenshot({path:`${out}/home-full-${width}.png`});
      await audit(page,`initial-${width}`);
      for (const id of ['capture','decision-journal','timeline','self-model','simulate-me','assistant-compare','self-retrieval','search','diagnostics','workspace-navigation']) {
        await screenshot(page,`${id}-${width}`,`#${id}`);
        report.surfaces.push({id,width,...await layout(page)});
      }
    }
    await context.close();
  }
  for (const width of [1440,390]) {
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000},recordVideo:{dir:out,size:{width,height:width===390?844:1000}}});
    await guard(context);const page=await context.newPage();let applies=0;
    page.on('request',r=>{if(r.url().endsWith('/save/apply')) applies++;});
    await page.goto(origin);await page.waitForTimeout(1800);
    await page.mouse.move(width*.7,240);await page.waitForTimeout(1200);await page.mouse.move(width*.9,550);await page.waitForTimeout(1200);
    await page.getByRole('link',{name:'Добавить мысль'}).click();
    await page.locator('#capture').getByRole('button',{name:'Текст',exact:true}).click();
    await page.getByLabel('Текст материала').fill('Мысль становится полезнее, когда мы возвращаемся к ней. Синтетический материал.');
    await screenshot(page,`text-${width}`,'#capture');
    await page.locator('#capture').getByRole('button',{name:'Создать черновик',exact:true}).click();
    await page.getByRole('heading',{name:'Черновик готов'}).waitFor();
    await page.locator('#capture').getByLabel('Название',{exact:true}).fill('Как идеи становятся знанием');
    await screenshot(page,`draft-${width}`,'.draft-result');
    await audit(page,`draft-${width}`);
    await page.locator('#capture').getByRole('button',{name:'Предпросмотр',exact:true}).click();
    await page.locator('.markdown-preview').waitFor();
    await screenshot(page,`preview-${width}`,'.markdown-preview');
    await page.locator('#capture').getByRole('button',{name:'Подготовить сохранение',exact:true}).click();
    await page.locator('.save-diff').waitFor(); assert.equal(applies,0);
    await screenshot(page,`diff-${width}`,'.save-plan');
    await audit(page,`diff-${width}`);
    await page.locator('#capture').getByLabel('Название',{exact:true}).fill('Уточнённая мысль');
    assert.equal(await page.locator('#capture').getByRole('button',{name:'Подтвердить сохранение',exact:true}).isVisible(),false);
    await page.locator('#capture').getByRole('button',{name:'Подготовить сохранение',exact:true}).click();
    await page.locator('#capture').getByRole('button',{name:'Подтвердить сохранение',exact:true}).click();
    await page.locator('.saved-note').waitFor(); assert.equal(applies,1);
    await screenshot(page,`saved-${width}`,'.saved-note');
    await audit(page,`saved-${width}`);
    await page.getByLabel('Поисковый запрос').fill('идеи');
    await page.locator('#search').getByRole('button',{name:'Найти',exact:true}).click();
    await page.locator('.search-hit').waitFor();
    await screenshot(page,`results-${width}`,'.search-hit');
    await page.locator('.search-hit').getByRole('button',{name:'Открыть',exact:true}).click();
    await page.locator('.retrieved-note').waitFor();
    assert.match(await page.locator('.retrieved-note-body').textContent(),/Мысль становится полезнее/);
    await screenshot(page,`note-${width}`,'.retrieved-note');
    await audit(page,`note-${width}`);
    await page.getByLabel('Поисковый запрос').fill('пусто');
    await page.locator('#search').getByRole('button',{name:'Найти',exact:true}).click();
    await page.locator('.search-empty').waitFor();
    await screenshot(page,`empty-${width}`,'#search');
    report.scenarios.push({width,textDraftPreviewDiffInvalidationConfirmSearchReadEmpty:'passed',applyRequests:applies,layout:await layout(page)});
    const video=page.video();await context.close();await video.saveAs(`${out}/flow-${width}.webm`);
  }
} finally {
  await writeFile(`${out}/qa-report.json`,JSON.stringify(report,null,2));
  await browser.close();
}
assert.equal(report.accessibility.flatMap(x=>x.violations).length,0);
assert.equal(report.widths.some(x=>x.overflow||x.smallTargets.length),false);
assert.equal(report.errors.length,0);
console.log('Layout, flows and accessibility passed');
