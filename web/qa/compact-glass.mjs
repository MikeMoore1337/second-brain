import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const out=process.env.SB_QA_OUT ?? '../.local/design-v6';await mkdir(out,{recursive:true});
const b=await chromium.launch({executablePath:process.env.SB_QA_CHROMIUM});const report=[];
try {
 for(const width of [320,360,390,430,768,1024,1440,1920]) {
  const c=await b.newContext({viewport:{width,height:width<768?844:1000},recordVideo:[390,1440].includes(width)?{dir:out}:undefined});
  await c.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:8137'?r.continue():r.abort());
  const p=await c.newPage();await p.goto('http://127.0.0.1:8137');await p.waitForTimeout(800);
  const height=await p.evaluate(()=>document.documentElement.scrollHeight);
  assert.equal(await p.locator('.fold-section[open]').count(),0);
  assert.equal(await p.getByRole('button',{name:/Остановить движение|Приостановить эффекты/}).count(),0);
  const summary=p.locator('#timeline-panel > summary');await summary.scrollIntoViewIfNeeded();
  const bounds=await summary.boundingBox();assert(bounds.height>=44);
  await summary.press('Enter');assert(await p.locator('#timeline').isVisible());await summary.press('Space');assert(!await p.locator('#timeline').isVisible());
  await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.locator('.section-dropdown a[href="#simulate-me"]').click();
  assert(await p.locator('#simulate-me').isVisible());assert.equal(await p.evaluate(()=>document.activeElement.id),'simulate-me');
  const field=p.locator('#simulate-me textarea').first();await field.fill('Синтетическое решение для проверки сохранности');
  await p.locator('#simulate-me-panel > summary').click();await p.locator('#simulate-me-panel > summary').click();
  assert.equal(await field.inputValue(),'Синтетическое решение для проверки сохранности');
  await p.locator('#simulate-me-panel > summary').click();
  await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.locator('.section-dropdown a[href="#simulate-me"]').click();assert(await field.isVisible());
  await p.locator('#simulate-me-panel > summary').click();
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if([390,1440].includes(width)) {
   await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:`${out}/home-${width}.png`});
   await p.locator('#capture').scrollIntoViewIfNeeded();await p.screenshot({path:`${out}/capture-${width}.png`});
   await p.locator('.tools-heading').scrollIntoViewIfNeeded();await p.screenshot({path:`${out}/tools-${width}.png`});
   const axe=await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();report.push({width,height,axe:axe.violations});
   await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.getByRole('switch',{name:'Анимация'}).uncheck();assert.equal(await p.locator('.motion-world').getAttribute('data-page-motion'),'false');await p.waitForTimeout(300);await p.screenshot({path:`${out}/menu-${width}.png`});
   await p.keyboard.press('Escape');await p.emulateMedia({reducedMotion:'reduce'});await p.goto('http://127.0.0.1:8137/#self-model');await p.waitForTimeout(300);assert(await p.locator('#self-model').isVisible());
   await p.screenshot({path:`${out}/expanded-${width}.png`});
   assert.equal(await p.locator('.motion-world').getAttribute('data-page-motion'),'false');
   const expanded=await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();report.push({width,state:'expanded',axe:expanded.violations});
   const video=p.video();await c.close();await video.saveAs(`${out}/navigation-${width}.webm`);
  } else {report.push({width,height});await c.close();}
 }
}finally{await writeFile(`${out}/compact-report.json`,JSON.stringify(report,null,2));await b.close();}
assert(report.every(r=>!r.axe?.length),JSON.stringify(report.filter(r=>r.axe?.length)));
console.log('Compact navigation, retained edits, direct links, reduced motion, eight widths and axe: PASS');
