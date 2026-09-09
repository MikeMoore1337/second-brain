import { chromium } from '@playwright/test';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const out='../.local/design-v4';
const labels={add:'Добавить',search:'Поиск',memory:'Память',timeline:'Хронология',decision:'Решения','self-model':'Модель себя',growth:'Развитие',simulate:'Прогноз',relation:'Связь / совет','self-retrieval':'Сбор контекста',diagnostics:'Диагностика',voice:'Голос',close:'Закрыть',expand:'Раскрыть',open:'Открыть',refresh:'Обновить',info:'Информация',warning:'Предупреждение',success:'Подтверждение',copy:'Копировать',pause:'Пауза',play:'Продолжить'};
const files=await readdir('src/assets/icons');
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
try {
 const page=await browser.newPage({viewport:{width:1200,height:1500},deviceScaleFactor:1});
 const img=async(name,variant,size)=>`<img width="${size}" height="${size}" src="data:image/webp;base64,${(await readFile(`src/assets/icons/${name}-${variant}-${variant==='detail'?96:48}.webp`)).toString('base64')}" alt="">`;
 const cards=await Promise.all(Object.entries(labels).map(async([name,label])=>`<article><h2>${label}</h2><div>${files.includes(`${name}-detail-96.webp`)?await img(name,'detail',64):await img(name,'compact',48)}${await img(name,'compact',26)}${await img(name,'compact',20)}</div><p>${files.includes(`${name}-detail-96.webp`)?'64':'48'} / 26 / 20 px</p></article>`));
 await page.setContent(`<style>body{background:#050407;color:#f6f0ff;font:16px system-ui;margin:32px}h1{font-size:28px}main{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}article{background:#0c0911;border:1px solid #594166;border-radius:16px;padding:16px}h2{font-size:16px;font-weight:500;margin:0 0 12px}article div{height:72px;display:flex;align-items:center;gap:24px}p{color:#b2a0c5;font-size:12px}</style><h1>Second Brain · оптические версии иконок</h1><main>${cards.join('')}</main>`);
 await page.screenshot({path:`${out}/icon-family.png`,fullPage:true});
 await page.goto('http://127.0.0.1:8137');await page.waitForTimeout(1800);
 const initial=await page.evaluate(()=>performance.getEntriesByType('resource').map(x=>x.name).filter(x=>x.endsWith('.webp')));
 assert(!initial.some(x=>/growth-detail|self-model-detail|diagnostics-detail/.test(x)),'Distant section artwork must not load at startup');
 await page.locator('#workspace-navigation').scrollIntoViewIfNeeded();await page.waitForTimeout(400);
 await page.screenshot({path:`${out}/icons-navigation.png`});
 const icons=await page.locator('img[data-icon]').evaluateAll(xs=>xs.filter(x=>x.getBoundingClientRect().height>0).map(x=>({name:x.dataset.icon,alt:x.alt,hidden:x.getAttribute('aria-hidden'),width:x.getBoundingClientRect().width,loaded:x.complete&&x.naturalWidth>0})));
 assert(icons.every(x=>x.alt===''&&x.hidden==='true'));
 const navigationIcons=await page.locator('.topnav img').evaluateAll(xs=>xs.every(x=>x.complete&&x.naturalWidth>0));assert(navigationIcons);
 await writeFile(`${out}/iconography.json`,JSON.stringify({initialRasterRequests:initial.map(x=>new URL(x).pathname),icons,navigationIcons},null,2));
} finally {await browser.close();}
