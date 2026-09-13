import assert from 'node:assert/strict';
// Synthetic transport fixtures for populated states of optional capabilities.
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { writeFile } from 'node:fs/promises';
const id='0198f4c5-6a00-7000-8000-000000000010';
const journalId='0198f4c5-6a00-7000-8000-000000000011';
const stamp='2026-09-09T12:00:00Z';
const hash=value=>'sha256:'+value.repeat(64);
const stage10Cohort=hash('a');
const stage10Option=hash('b');
const stage10Pattern={
  cohort:{grouping_policy:'exact-v1',domain:'work-format',situation_fingerprint:hash('c'),information_fingerprint:hash('d'),option_namespace_fingerprint:hash('e'),criteria_fingerprint:hash('f'),cohort_fingerprint:stage10Cohort},
  pattern_type:'repeated_exact_choice',state:'current',selected_option:{option_index:0,option_fingerprint:stage10Option},support_count:3,total_comparable_observations:3,support_ratio:{numerator:3,denominator:3},choice_support:[{option:{option_index:0,option_fingerprint:stage10Option},support_count:3,support_ratio:{numerator:3,denominator:3}}],
  temporal_span:{earliest_evidence_at:stamp,latest_evidence_at:stamp},windows:[{window:'current',observation_count:3,choice_support:[{option:{option_index:0,option_fingerprint:stage10Option},support_count:3,support_ratio:{numerator:3,denominator:3}}]}],outcome_presence:{present:1,absent:2},provenance:{source_journal_uuids:[journalId],source_count:1,provenance_fingerprint:hash('g')},caveats:['support_is_descriptive']
};
const claim={dimension:'preference',claim:'Мне полезно возвращаться к записанным идеям.',domain:'work-format',confidence:{state:'supported',supporting_evidence_count:1},temporal_context:{known_evidence_count:1},supporting_evidence:[{id,evidence_kind:'user_statement',self_kind:'preference',domain:'work-format',evidence_at:stamp,evidence_at_precision:'exact'}],contradicting_evidence:[],contextual_evidence:[]};
const stage10Review={
  generated_at:stamp,
  stated:{source_note_uuid:id,dimension:'preference',source_evidence_kind:'user_statement',source_self_kind:'preference',domain:'work-format',evidence_at:stamp,evidence_at_precision:'exact',source_contract_version:'self-model-v1',source_derivation_version:'self-model-derivation-v1',self_model_policy_fingerprint:hash('h'),source_fingerprint:hash('i'),claim_fingerprint:hash('j')},
  behavioral:{cohort:stage10Pattern.cohort,option:{option_index:0,option_fingerprint:stage10Option},pattern_type:'repeated_exact_choice',pattern_state:'current',pattern_fingerprint:hash('k'),source_fingerprint:hash('l'),provenance_fingerprint:hash('g'),source_count:1,behavioral_contract_version:'behavioral-self-model-v1',behavioral_derivation_version:'behavioral-self-model-derivation-v1',observation_version:'behavioral-observation-v1',policy_id:'behavioral-observation-v1',policy_fingerprint:hash('m'),comparison_subject:'current-exact-option-v1'},
  candidate_mapping_fingerprint:hash('n'),claim_text:claim.claim,cohort_domain:'work-format',situation:'Перед рабочим блоком',information_known_at_decision_time:'Доступный контекст',criteria:['сфокусированность'],ordered_options:[{option_index:0,option_fingerprint:stage10Option,label:'Сохранить контекст'},{option_index:1,option_fingerprint:hash('o'),label:'Сразу перейти к действию'}],pattern_type:'repeated_exact_choice',pattern_state:'current',caveats:['current_source_revalidated']
};
const stage10Accepted={mapping_id:'0198f4c5-6a00-7000-8000-000000000012',lifecycle_state:'active',source_note_uuid:id,domain:'work-format',behavioral_cohort_fingerprint:stage10Cohort,behavioral_option_index:0,behavioral_option_fingerprint:stage10Option,pattern_type:'repeated_exact_choice',pattern_state:'current',mapping_fingerprint:stage10Review.candidate_mapping_fingerprint,mapping_policy_fingerprint:hash('p'),created_at:stamp,reviewed_at:stamp,supersedes_mapping_id:null};
const stage10Composition={contract_version:'stated-observed-mapping-v1',derivation_version:'stated-observed-composition-derivation-v1',mapping_policy_id:'stated-observed-explicit-mapping-v1',mapping_policy_fingerprint:hash('q'),generated_at:stamp,state:'aligned',reason_code:null,mapping_id:stage10Accepted.mapping_id,mapping_fingerprint:stage10Accepted.mapping_fingerprint,observed_option:{option_index:0,option_fingerprint:stage10Option},behavioral_pattern_type:'repeated_exact_choice',behavioral_pattern_state:'current',caveats:[]};
const assistant={kind:'analysis',output_label:'independent_recommendation_analysis',recommendation:null,selected_option:null,rationale:['Начни с небольшого шага и проверь результат.'],evidence_refs:[],constraints_used:[],objectives_used:[],uncertainty:[],abstention_code:null,contract_version:'assistant-v1'};
const prediction={kind:'prediction',selected_option:{id:'a',label:'Вернуться к заметкам'},evidence_refs:[],contextual_evidence_refs:[],temporal_caveats:[]};
const layer={status:'healthy',required:true,code:null};
const fixtures={
  '/api/timeline':{known_items:[{id,event_at:stamp,event_kind:'personal_memory',evidence_kind:'user_statement',summary:'Вернуться к идеям в конце недели',relative_path:'30 Resources/Идеи.md'}],unknown_items:[],known_total:1,unknown_total:0},
  '/api/self-model':{claims:[claim],eligible_evidence_count:1,represented_evidence_count:1,generated_at:stamp},
  '/api/behavioral-self-model':{contract_version:'behavioral-self-model-v1',derivation_version:'behavioral-self-model-derivation-v1',policy_id:'behavioral-observation-v1',policy_fingerprint:hash('r'),generated_at:stamp,patterns:[stage10Pattern],eligible_journal_count:3,comparable_observation_count:3,excluded_unknown_time_count:0,excluded_outside_horizon_count:0,caveats:['support_is_descriptive']},
  '/api/stated-observed-mapping/status':{mapping_policy_id:'stated-observed-explicit-mapping-v1',mappings:[],active_mapping_count:0},
  '/api/stated-observed-mapping/review':stage10Review,
  '/api/stated-observed-mapping/confirm':{status:'accepted',mapping:stage10Accepted},
  '/api/stated-observed-composition':stage10Composition,
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
    await page.getByRole('button',{name:'Независимый совет + прогноз + сравнение',exact:true}).click();
    await page.locator('.stage7-delta-panel').waitFor();
    await page.locator('#diagnostics').getByRole('button',{name:'Обновить',exact:true}).click();
    const cognitive=page.locator('#cognitive-twin');
    await cognitive.getByRole('button',{name:'Обновить текущие данные',exact:true}).click();
    await cognitive.locator('#cognitive-twin-stated-select').selectOption(id);
    await cognitive.locator('#cognitive-twin-observed-select').selectOption(`${stage10Cohort}:0:${stage10Option}`);
    await cognitive.getByRole('button',{name:'Показать review',exact:true}).click();
    await cognitive.getByText('Labels shown here are only for human review. The application does not automatically match them by text.').waitFor();
    await cognitive.locator('.cognitive-twin-confirm-label input').check();
    await cognitive.getByRole('button',{name:'Подтвердить mapping',exact:true}).click();
    await cognitive.getByText('Accepted mapping UUID').waitFor();
    for(const selector of ['#timeline','#self-model','#cognitive-twin','.self-retrieval-item','.simulate-me-result','.stage7-result-grid','#diagnostics']){
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
