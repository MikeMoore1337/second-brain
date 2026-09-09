import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import assert from 'node:assert/strict';
import { mkdir,writeFile } from 'node:fs/promises';
const out=process.env.SB_QA_OUT??'../.local/design-v7';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.SB_QA_CHROMIUM});const report=[];
try {
 for(const width of [320,360,390,430,768,1024,1440,1920]) {
  const recording=[390,1440].includes(width);
  const c=await browser.newContext({viewport:{width,height:width<768?844:1000},recordVideo:recording?{dir:out}:undefined});
  await c.route('**/*',r=>new URL(r.request().url()).origin==='http://127.0.0.1:8137'?r.continue():r.abort());
  const p=await c.newPage();await p.goto('http://127.0.0.1:8137');
  assert.equal(await p.locator('.page-filament').count(),0);
  assert.equal(await p.locator('.topbar').evaluate(e=>getComputedStyle(e).borderBottomWidth),'0px');
  await p.locator('.pillars').scrollIntoViewIfNeeded();await p.waitForTimeout(500);
  const height=await p.locator('.pillars').evaluate(e=>e.getBoundingClientRect().height);
  assert(height<(width<600?320:200),`width ${width}: height ${height}`);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert(await p.locator('.pillar-backdrop').evaluateAll(es=>es.every(e=>e.complete&&e.naturalWidth>0&&getComputedStyle(e).pointerEvents==='none')));
  const sample=()=>p.locator('.pillar-backdrop').evaluateAll(es=>es.flatMap(e=>e.getAnimations().map(a=>({time:a.currentTime,state:a.playState}))));
  assert((await sample()).some(a=>a.state==='running'));
  if(recording) {
   await p.waitForTimeout(1200);await p.screenshot({path:`${out}/bottom-${width}.png`});
   const cdp=await c.newCDPSession(p);await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
   const frames=await p.evaluate(()=>new Promise(resolve=>{const ts=[];let first=0,last=0;function frame(t){if(!first)first=t;if(last)ts.push(t-last);last=t;if(t-first<3000)requestAnimationFrame(frame);else{ts.sort((a,b)=>a-b);resolve({median:ts[Math.floor(ts.length*.5)],p95:ts[Math.floor(ts.length*.95)],max:ts.at(-1)});}}requestAnimationFrame(frame);}));
   await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
   await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.getByRole('switch',{name:'Анимация'}).uncheck();await p.keyboard.press('Escape');
   await p.waitForTimeout(100);const first=await sample();await p.waitForTimeout(600);assert.deepEqual(await sample(),first);
   await p.getByRole('button',{name:'Разделы',exact:true}).click();await p.getByRole('switch',{name:'Анимация'}).check();await p.keyboard.press('Escape');
   await p.evaluate(()=>scrollTo(0,0));await p.waitForTimeout(300);assert((await sample()).every(a=>a.state==='paused'));
   await p.screenshot({path:`${out}/header-${width}.png`});
   await p.emulateMedia({reducedMotion:'reduce'});await p.locator('.pillars').scrollIntoViewIfNeeded();assert.equal((await sample()).length,0);
   const axe=await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();assert.equal(axe.violations.length,0);
   report.push({width,height,frames,axe:axe.violations,pause:'stable',offscreen:'paused',reduced:'none'});
   const video=p.video();await c.close();await video.saveAs(`${out}/motion-${width}.webm`);
  }else{report.push({width,height});await c.close();}
 }
}finally{await writeFile(`${out}/open-edges-report.json`,JSON.stringify(report,null,2));await browser.close();}
console.log('Open edges, compact closing block, motion controls, eight widths and axe: PASS');


