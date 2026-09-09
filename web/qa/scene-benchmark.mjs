import { chromium } from '@playwright/test';
import { mkdir,writeFile } from 'node:fs/promises';
const phase=process.env.SB_QA_PHASE??'after';
const out=process.env.SB_QA_OUT ?? '../.local/design-v4';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
const report={browser:browser.version(),phase,frames:[]};
try{
  for(const width of [1440,390]){
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000},isMobile:width===390,hasTouch:width===390});
    const page=await context.newPage();await page.goto('http://127.0.0.1:8137');
    if(process.env.SB_QA_SELECTOR)await page.locator(process.env.SB_QA_SELECTOR).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await page.screenshot({path:`${out}/${phase}-${width}.png`});
    const cdp=await context.newCDPSession(page);
    for(const rate of [1,4]){
      await cdp.send('Emulation.setCPUThrottlingRate',{rate});
      const result=await page.evaluate(()=>new Promise(resolve=>{
        const frames=[];let first=0,last=0;
        function step(t){if(!first)first=t;if(last)frames.push(t-last);last=t;if(t-first<4000)requestAnimationFrame(step);else{frames.sort((a,b)=>a-b);resolve({count:frames.length,median:frames[Math.floor(frames.length*.5)],p95:frames[Math.floor(frames.length*.95)],max:frames.at(-1)});}}
        requestAnimationFrame(step);
      }));report.frames.push({width,rate,...result});
    }await context.close();
  }
}finally{await writeFile(`${out}/${phase}-frames.json`,JSON.stringify(report,null,2));await browser.close();}
console.log(report);
