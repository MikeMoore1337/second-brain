import { chromium } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
const metrics=[];
try{
  for(let i=0;i<3;i++){
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();const cdp=await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:100,downloadThroughput:200000,uploadThroughput:100000});
    await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
    await page.addInitScript(()=>{
      window.__lcp=0;
      new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lcp=e.startTime;}).observe({type:'largest-contentful-paint',buffered:true});
    });
    await page.goto('http://127.0.0.1:8137');await page.waitForTimeout(2500);
    metrics.push(await page.evaluate(()=>({
      fcp:performance.getEntriesByName('first-contentful-paint')[0]?.startTime,lcp:window.__lcp,
      dom:performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd,
      bytes:performance.getEntriesByType('resource').reduce((n,e)=>n+e.transferSize,0),
    })));
    await context.close();
  }
}finally{await writeFile('../.local/design-v4/after-metrics.json',JSON.stringify(metrics,null,2));await browser.close();}
console.log(metrics);
