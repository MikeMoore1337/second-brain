import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const out=process.env.SB_QA_OUT ?? '../.local/design-v4';
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
const cards=[];
try {
 for(const width of [320,360,390,430,768,1024,1440,1920]) {
  const c=await browser.newContext({viewport:{width,height:width<768?844:1000}});
  const p=await c.newPage();await p.goto('http://127.0.0.1:8137');
  await p.waitForTimeout(1100);await p.screenshot({path:`${out}/home-${width}.png`});
  await p.locator('.pillars').scrollIntoViewIfNeeded();await p.waitForTimeout(350);
  const layout=await p.locator('.pillar-card').evaluateAll(xs=>xs.map(x=>({textBottom:x.querySelector('p').getBoundingClientRect().bottom,metaTop:x.querySelector('.card-meta').getBoundingClientRect().top,cardBottom:x.getBoundingClientRect().bottom,metaBottom:x.querySelector('.card-meta').getBoundingClientRect().bottom})));
  assert(layout.every(x=>x.metaTop>=x.textBottom+20&&x.metaBottom<x.cardBottom));cards.push({width,layout});await c.close();
 }
 for(const width of [1440,390]) {
  const c=await browser.newContext({viewport:{width,height:width===390?844:1000},isMobile:width===390,hasTouch:width===390,recordVideo:{dir:out,size:{width,height:width===390?844:1000}}});
  const p=await c.newPage();await p.goto('http://127.0.0.1:8137');await p.waitForTimeout(1500);
  if(width===390)await p.evaluate(()=>scrollTo(0,370));else await p.mouse.move(1250,300);
  await p.waitForTimeout(5500);await p.screenshot({path:`${out}/scene-${width}.png`});
  if(width===1440)await p.mouse.move(800,620);
  await p.waitForTimeout(3500);await p.getByRole('button',{name:'Остановить движение'}).click();await p.waitForTimeout(1400);
  await p.getByRole('button',{name:'Включить движение'}).click();await p.waitForTimeout(1500);
  await p.locator('.quick-nav a[href="#capture"]').click();await p.waitForTimeout(1200);
  await p.locator('.quick-nav a[href="#search"]').click();await p.waitForTimeout(1200);
  const video=p.video();await c.close();await video.saveAs(`${out}/scene-${width}.webm`);
 }
 await writeFile(`${out}/card-layout.json`,JSON.stringify(cards,null,2));
}finally {await browser.close();}
