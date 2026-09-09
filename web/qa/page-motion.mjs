import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import assert from 'node:assert/strict';
import { mkdir,writeFile } from 'node:fs/promises';
const out=process.env.SB_QA_OUT ?? '../.local/design-v5';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
const report={menus:[],motion:[],axe:[],navigation:[]};
try {
 for(const width of [320,360,390,430,768,1024,1440,1920]) {
  const c=await browser.newContext({viewport:{width,height:width<768?844:1000},isMobile:width<768,hasTouch:width<768});
  const p=await c.newPage();await p.goto('http://127.0.0.1:8137');await p.waitForTimeout(1000);
  const trigger=p.getByRole('button',{name:'Разделы',exact:true});await trigger.click();await p.waitForTimeout(300);
  const menu=await p.locator('.section-dropdown').evaluate(e=>({left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,overflow:document.documentElement.scrollWidth>innerWidth,small:[...e.querySelectorAll('a,button')].filter(x=>{const r=x.getBoundingClientRect();return r.width<44||r.height<44}).length}));
  assert(menu.left>=0&&menu.right<=width&&menu.top>=0&&menu.bottom<=(width<768?844:1000)&&!menu.overflow&&menu.small===0);report.menus.push({width,...menu});
  if(width===1440||width===390) {
   await p.screenshot({path:`${out}/menu-${width}.png`});
   const axe=await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();report.axe.push({width,state:'menu',violations:axe.violations});assert.equal(axe.violations.length,0);
   await p.keyboard.press('Escape');assert(await trigger.evaluate(e=>e===document.activeElement));
   await trigger.press('Enter');await p.keyboard.press('Tab');await p.keyboard.press('Tab');
   assert(await p.locator('.section-dropdown nav a').first().evaluate(e=>e===document.activeElement));
   await p.keyboard.press('Enter');assert.equal(await p.evaluate(()=>document.activeElement.id),'capture');assert.equal(await p.locator('.section-dropdown').count(),0);
   for(const id of ['timeline','self-model','search','memory']) {await trigger.click();await p.locator(`.section-dropdown a[href="#${id}"]`).click();assert.equal(await p.evaluate(()=>document.activeElement.id),id);assert.equal(await p.locator('.section-dropdown').count(),0);report.navigation.push({width,id});}
   await p.locator('.chapter-interlude').nth(1).scrollIntoViewIfNeeded();await p.waitForTimeout(900);await p.screenshot({path:`${out}/chapter-${width}.png`});
   const times=()=>p.evaluate(()=>document.getAnimations().filter(a=>a.animationName&&a.effect?.target?.closest?.('.chapter-scene,.page-atmosphere')).map(a=>({name:a.animationName,time:a.currentTime,state:a.playState})));
   const moving=await times();assert(moving.some(x=>x.name==='chapter-float'&&x.state==='running'));
   await trigger.click();await p.getByRole('button',{name:'Приостановить эффекты страницы'}).click();await p.keyboard.press('Escape');await p.waitForTimeout(300);const first=await times();await p.waitForTimeout(600);assert.deepEqual(await times(),first);report.motion.push({width,pause:'stable',animations:first.length});
   await trigger.click();await p.getByRole('button',{name:'Включить эффекты страницы'}).click();await p.keyboard.press('Escape');
   await p.locator('#search').scrollIntoViewIfNeeded();await p.waitForTimeout(350);
   assert(await p.locator('.chapter-interlude').nth(1).evaluate(e=>e.dataset.inView==='false'&&e.getAnimations({subtree:true}).every(a=>a.playState==='paused')));
   report.motion.push({width,offscreen:'chapter paused'});
   await p.emulateMedia({reducedMotion:'reduce'});await p.waitForTimeout(250);assert((await times()).length===0);report.motion.push({width,reduced:'no decorative animations'});
   const axeChapter=await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();report.axe.push({width,state:'chapter',violations:axeChapter.violations});assert.equal(axeChapter.violations.length,0);
  }await c.close();
 }
 for(const width of [1440,390]) {
  const c=await browser.newContext({viewport:{width,height:width===390?844:1000},recordVideo:{dir:out,size:{width,height:width===390?844:1000}}});const p=await c.newPage();await p.goto('http://127.0.0.1:8137');await p.waitForTimeout(1800);
  await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.waitForTimeout(700);await p.locator('.section-dropdown a[href="#capture"]').click();await p.waitForTimeout(900);
  for(let i=0;i<3;i++){await p.locator('.chapter-interlude').nth(i).scrollIntoViewIfNeeded();await p.waitForTimeout(2800);}
  await p.locator('#workspace-navigation').scrollIntoViewIfNeeded();await p.waitForTimeout(1800);await p.screenshot({path:`${out}/bottom-${width}.png`});const video=p.video();await c.close();await video.saveAs(`${out}/page-motion-${width}.webm`);
 }
}finally {await writeFile(`${out}/page-motion-report.json`,JSON.stringify(report,null,2));await browser.close();}
console.log('Menus, shared pause, keyboard, chapters and AA checks passed');
