import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const origin='http://127.0.0.1:8137';
const out='../.local/design-v3';
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
const report={motion:[],inputs:[],fonts:[],colors:[],keyboard:[],accessibility:[]};
async function context(options={}) {
  const c=await browser.newContext(options);
  await c.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
  return c;
}
async function shot(page,name,selector) {
  if(selector)await page.locator(selector).scrollIntoViewIfNeeded();
  await page.screenshot({path:`${out}/${name}.png`});
}
async function colors(page,name) {
  report.colors.push({name,...await page.evaluate(()=>{
    const greens=[];const unexpectedFonts=[];
    for(const e of document.querySelectorAll('body *')) {
      if(!e.getClientRects().length)continue;
      const s=getComputedStyle(e);
      if(/\b(Georgia|Times|serif)\b/.test(s.fontFamily.replaceAll('sans-serif','')))unexpectedFonts.push(e.tagName+': '+s.fontFamily);
      for(const pseudo of [null,'::before','::after']){
        const style=pseudo?getComputedStyle(e,pseudo):s;
        for(const key of ['color','backgroundColor','borderColor','boxShadow','backgroundImage','outlineColor']){
          for(const m of style[key].matchAll(/rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+)(?:[, /]+([\d.]+))?\)/g)){
            const [r,g,b]=m.slice(1,4).map(Number);
            if(g>r*1.12&&g>b*1.05&&Number(m[4]??1)>.05)greens.push({tag:e.tagName,class:e.className,key,color:m[0],pseudo});
          }
        }
      }
    }return {greens,unexpectedFonts};
  })});
}
try {
  for(const width of [1440,390]) {
    const c=await context({viewport:{width,height:width===390?844:1000},isMobile:width===390,hasTouch:width===390});
    const p=await c.newPage();await p.goto(origin);await p.waitForTimeout(1200);
    const cdp=await c.newCDPSession(p);
    await cdp.send('DOM.enable');await cdp.send('CSS.enable');
    const {root}=await cdp.send('DOM.getDocument');
    for(const selector of ['#hero-title','.universe-thesis','#entry-title']) {
      const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector});
      report.fonts.push({width,selector,...await cdp.send('CSS.getPlatformFontsForNode',{nodeId})});
    }
    await colors(p,`initial-${width}`);
    const states=()=>p.evaluate(()=>({moving:document.querySelector('.cinematic-hero').dataset.moving,animations:document.getAnimations().filter(a=>a.animationName && a.effect?.target?.closest?.('.universe-parallax')).map(a=>({name:a.animationName,state:a.playState,time:a.currentTime})),transforms:[...document.querySelectorAll('[data-depth]')].map(e=>e.style.transform)}));
    report.motion.push({width,phase:'active',...await states()});
    await p.mouse.move(width*.9,300);await p.waitForTimeout(400);
    report.motion.push({width,phase:'pointer',...await states()});
    await p.getByRole('button',{name:'Остановить движение'}).click();
    await p.waitForTimeout(400); const first=await states();await p.waitForTimeout(650);const second=await states();
    assert.equal(second.moving,'false');assert.deepEqual(first.animations,second.animations);
    report.motion.push({width,phase:'paused-stable-650ms',...second});
    await shot(p,`paused-${width}`,'.cinematic-hero');
    await p.getByRole('button',{name:'Включить движение'}).click();
    await p.locator('#search').scrollIntoViewIfNeeded();await p.waitForTimeout(200);assert.equal((await states()).moving,'false');
    report.motion.push({width,phase:'offscreen',...await states()});
    await p.evaluate(()=>scrollTo(0,0));await p.waitForTimeout(300);
    for(const rate of [1,4]) {
      await cdp.send('Emulation.setCPUThrottlingRate',{rate});
      const timing=await p.evaluate(()=>new Promise(resolve=>{
        const frames=[];let start=0,last=0;
        function frame(t){if(!start)start=t;if(last)frames.push(t-last);last=t;if(t-start<4000)requestAnimationFrame(frame);else{frames.sort((a,b)=>a-b);resolve({frames:frames.length,median:frames[Math.floor(frames.length*.5)],p95:frames[Math.floor(frames.length*.95)],over33ms:frames.filter(n=>n>33.4).length,max:frames.at(-1)});}}requestAnimationFrame(frame);
      }));report.motion.push({width,phase:'raf-4seconds',cpu:rate,...timing});
    }
    await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
    await p.emulateMedia({reducedMotion:'reduce'});await p.waitForTimeout(200);
    assert.equal((await states()).moving,'false');assert.equal((await states()).animations.length,0);
    await shot(p,`reduced-${width}`);report.motion.push({width,phase:'reduced',...await states()});
    await c.close();
  }
  const c=await context({viewport:{width:390,height:844}});const p=await c.newPage();
  await p.goto(origin);await p.getByRole('link',{name:'Добавить мысль'}).click();
  const capture=p.locator('#capture');
  await capture.getByLabel('Публичный URL').fill('https://example.com/synthetic');
  await capture.getByRole('button',{name:'Создать черновик',exact:true}).click();await capture.getByRole('heading',{name:'Черновик готов'}).waitFor();
  await shot(p,'url-draft-390','.draft-result');report.inputs.push('URL → synthetic DI draft');
  await capture.getByRole('button',{name:'Добавить ещё'}).click();await capture.getByRole('button',{name:'Голос',exact:true}).click();
  const wav=Buffer.alloc(1644);wav.write('RIFF');wav.writeUInt32LE(1636,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(1600,40);
  await capture.locator('input[type=file]').setInputFiles({name:'synthetic.wav',mimeType:'audio/wav',buffer:wav});
  await capture.getByRole('button',{name:'Распознать',exact:true}).click();
  await capture.getByLabel('Текст материала').waitFor();assert.match(await capture.getByLabel('Текст материала').inputValue(),/Распознанный русский текст/);
  await shot(p,'voice-transcript-390','#capture');
  await capture.getByLabel('Текст материала').fill('Проверенный синтетический голосовой текст');
  await capture.getByRole('button',{name:'Создать черновик',exact:true}).click();await capture.getByRole('heading',{name:'Черновик готов'}).waitFor();
  report.inputs.push('Synthetic WAV → DI transcription → editable text → draft');
  await capture.getByRole('button',{name:'Добавить ещё'}).click();await capture.getByRole('button',{name:'Текст',exact:true}).click();
  await capture.getByLabel('Текст материала').fill('Синтетическая проверка ошибки');
  let release;await p.route('**/api/drafts/text',async route=>{await new Promise(r=>release=r);await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'Сервис временно недоступен. Попробуй позже.'}})});});
  await capture.getByRole('button',{name:'Создать черновик',exact:true}).click();await capture.getByRole('button',{name:'Создаю…'}).waitFor();
  assert.equal(await capture.getByLabel('Текст материала').isDisabled(),true);await shot(p,'loading-390','#capture');release();
  await capture.getByRole('alert').waitFor();await shot(p,'error-390','#capture');await colors(p,'error');
  report.inputs.push('Deferred HTTP → busy state → localized 503 error → editable recovery');
  report.accessibility.push({state:'error',violations:(await new AxeBuilder({page:p}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations});
  await p.setViewportSize({width:390,height:430});await capture.getByLabel('Текст материала').focus();
  await shot(p,'keyboard-height-390');
  report.inputs.push({emulatedKeyboardViewport:await p.evaluate(()=>({height:innerHeight,fieldBottom:document.activeElement.getBoundingClientRect().bottom,navigationVisible:getComputedStyle(document.querySelector(".quick-nav")).display!=="none",navigationTop:document.querySelector('.quick-nav').getBoundingClientRect().top,overflow:document.documentElement.scrollWidth>innerWidth}))});
  await c.close();
  const k=await context({viewport:{width:1440,height:1000}});const q=await k.newPage();await q.goto(origin);
  await q.keyboard.press('Tab');assert.equal(await q.locator('.skip-link').evaluate(e=>e===document.activeElement),true);
  await shot(q,'keyboard-skip');await q.keyboard.press('Enter');assert.equal(await q.locator('main').evaluate(e=>e===document.activeElement),true);
  async function tabTo(locator){for(let i=0;i<80;i++){await q.keyboard.press('Tab');if(await locator.evaluate(e=>e===document.activeElement))return;}throw Error('Keyboard target unreachable');}
  await tabTo(q.getByRole('link',{name:'Добавить мысль'}));await q.keyboard.press('Enter');
  await tabTo(q.locator('#capture').getByRole('button',{name:'Текст',exact:true}));await q.keyboard.press('Space');
  await tabTo(q.getByLabel('Текст материала'));await q.keyboard.type('Synthetic keyboard input');
  await shot(q,'keyboard-focus','#capture');
  await tabTo(q.locator('#capture').getByRole('button',{name:'Создать черновик',exact:true}));await q.keyboard.press('Enter');await q.getByRole('heading',{name:'Черновик готов'}).waitFor();
  for(const name of ['Предпросмотр','Подготовить сохранение','Подтвердить сохранение']) {
    await tabTo(q.locator('#capture').getByRole('button',{name,exact:true}));await q.keyboard.press('Enter');
    await q.waitForTimeout(200);
  }
  await q.locator('.saved-note').waitFor();
  await tabTo(q.locator('.quick-nav').getByRole('link',{name:'Поиск',exact:true}));await q.keyboard.press('Enter');
  await tabTo(q.getByLabel('Поисковый запрос'));await q.keyboard.type('идеи');
  await tabTo(q.locator('#search').getByRole('button',{name:'Найти',exact:true}));await q.keyboard.press('Enter');
  await q.locator('.search-hit').waitFor();await tabTo(q.locator('.search-hit').getByRole('button',{name:'Открыть',exact:true}));await q.keyboard.press('Enter');
  await q.locator('.retrieved-note').waitFor();await tabTo(q.locator('.retrieved-note-body'));
  await shot(q,'keyboard-note');
  report.keyboard.push('Tab → skip → main → add → text → draft → preview → prepare → confirm → search → open → reading focus; keyboard only');
  await k.close();
}finally{await writeFile(`${out}/motion-input-report.json`,JSON.stringify(report,null,2));await browser.close();}
assert.equal(report.colors.some(x=>x.greens.length||x.unexpectedFonts.length),false);
assert.equal(report.accessibility.some(x=>x.violations.length),false);
console.log('motion/input QA complete');
