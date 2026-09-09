import assert from 'node:assert/strict';
// Synthetic transport fixtures for populated states of optional capabilities.
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { writeFile } from 'node:fs/promises';
const id='0198f4c5-6a00-7000-8000-000000000010';
const stamp='2026-09-09T12:00:00Z';
const claim={dimension:'preference',claim:'Мне полезно возвращаться к записанным идеям.',confidence:{state:'supported',supporting_evidence_count:1},temporal_context:{known_evidence_count:1},supporting_evidence:[{id,evidence_kind:'user_statement'}],contradicting_evidence:[],contextual_evidence:[]};
const assistant={kind:'analysis',output_label:'independent_recommendation_analysis',recommendation:null,selected_option:null,rationale:['Начни с небольшого шага и проверь результат.'],evidence_refs:[],constraints_used:[],objectives_used:[],uncertainty:[],abstention_code:null,contract_version:'assistant-v1'};
const prediction={kind:'prediction',selected_option:{id:'a',label:'Вернуться к заметкам'},evidence_refs:[],contextual_evidence_refs:[],temporal_caveats:[]};
const layer={status:'healthy',required:true,code:null};
const fixtures={
  '/api/timeline':{known_items:[{id,event_at:stamp,event_kind:'personal_memory',evidence_kind:'user_statement',summary:'Вернуться к идеям в конце недели',relative_path:'30 Resources/Идеи.md'}],unknown_items:[],known_total:1,unknown_total:0},
  '/api/self-model':{claims:[claim],eligible_evidence_count:1,represented_evidence_count:1,generated_at:stamp},
  '/api/self-retrieval':{items:[{note_id:id,title:'Идеи и практика',note_type:'resource',body:'Синтетическая заметка.\n\n'+('Возвращайся к мысли и проверяй её на практике.\n\n').repeat(15),self_model_claims:[],tags:['идеи']}],exclusions:[],candidate_count:1,included_count:1,excluded_count:0},
  '/api/simulate-me':prediction,
  '/api/assistant':assistant,
  '/api/compare':{option_ids:['a','b'],assistant:{state:'result',result:assistant,error:null},simulate_me:{state:'result',result:prediction,error:null},delta:{relation:'assistant_only_selected',assistant_state:'result',simulate_me_state:'result',assistant_selected_option_id:null,simulate_me_selected_option_id:'a',explanation:'Независимый совет и прогноз показаны раздельно.'}},
  '/api/diagnostics':{status:'healthy',generated_at:stamp,config:{resolvable:true},vault:{manifest_available:true,content_roots_available:true,attachments_scan_complete:true},manifest:{available:true,schema_version:1},counts:{managed_notes:4,enrolled_personal_memory:2,valid_decision_journals:1,valid_outcome_observations:1},notes:4,enrolled_personal_memory:2,valid_decision_journals:1,valid_outcome_observations:1,attachments:{scan_complete:true,count:0,total_bytes:0},attachment_total:0,attachment_bytes:0,timeline:layer,self_model:layer,self_retrieval:layer,errors:0,warnings:0,diagnostics:[],exit_code:0},
};
const out=process.env.SB_QA_OUT ?? '../.local/design-v4';const results=[];
const browser=await chromium.launch({headless:true,executablePath:process.env.SB_QA_CHROMIUM});
try{
  for(const width of [1440,390]){
    const c=await browser.newContext({viewport:{width,height:width===390?844:1000}});
    await c.route('**/*',route=>{
      const u=new URL(route.request().url());if(u.origin!=='http://127.0.0.1:8137')return route.abort();
      return fixtures[u.pathname]?route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixtures[u.pathname])}):route.continue();
    });
    const page=await c.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8137');
    await page.locator('.fold-section').evaluateAll(items=>items.forEach(e=>e.open=true));
    await page.locator('#self-model').getByRole('button',{name:'Построить / обновить'}).click();
    await page.getByLabel('Запрос для контекста').fill('идеи');await page.locator('#self-retrieval').getByRole('button',{name:'Собрать',exact:true}).click();
    await page.locator('#simulate-me').getByLabel('Задача или запрос').fill('Как вернуться к идеям?');
    await page.locator('#simulate-me').getByLabel('Название',{exact:true}).fill('Вернуться к заметкам');
    await page.locator('#simulate-me button[type=submit]').click();
    await page.locator('#assistant-compare textarea').first().fill('Как вернуться к идеям?');
    await page.locator('.stage7-option-row input').nth(0).fill('Вернуться к заметкам');await page.locator('.stage7-option-row input').nth(1).fill('Собрать новый материал');
    await page.getByRole('button',{name:'Совет + прогноз + сравнение',exact:true}).click();
    await page.locator('.stage7-delta-panel').waitFor();
    await page.locator('#diagnostics').getByRole('button',{name:'Обновить',exact:true}).click();
    for(const selector of ['#timeline','#self-model','.self-retrieval-item','.simulate-me-result','.stage7-result-grid','#diagnostics']){
      const e=page.locator(selector);await e.waitFor();await e.evaluate(e=>e.scrollIntoView({block:'start'}));await page.waitForTimeout(350);
      await page.screenshot({path:`${out}/populated-${selector.replace(/[.#]/g,'')}-${width}.png`});
    }
    const axe=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    results.push({width,errors,violations:axe.violations,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
    await c.close();
  }
}finally{await writeFile(`${out}/surfaces-report.json`,JSON.stringify(results,null,2));await browser.close();}
assert.equal(results.some(r=>r.errors.length||r.violations.length||r.overflow),false);
console.log('optional surfaces complete');
