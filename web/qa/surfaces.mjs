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
const growthGoal={source_note_uuid:id,dimension:'goal',source_evidence_kind:'user_statement',source_self_kind:'goal',domain:'work',evidence_at:'unknown',evidence_at_precision:'unknown',source_contract_version:'self-model-v1',source_derivation_version:'self-model-derivation-v1',self_model_policy_fingerprint:hash('r'),source_fingerprint:hash('s'),claim_fingerprint:hash('t')};
const growthGoalIdentityFingerprint=hash('u');
const growthGoals={contract_version:'growth-engine-v1',derivation_version:'growth-engine-derivation-v1',policy_id:'growth-engine-explicit-relation-v1',policy_fingerprint:hash('v'),generated_at:stamp,selection_mode:'each_current_goal',selected_goal_source_uuid:null,eligible_goal_count:1,goals:[{goal:growthGoal,goal_text:'Достичь ясного рабочего ритма',goal_identity_fingerprint:growthGoalIdentityFingerprint}],reason_codes:[],caveats:[]};
const growthResult={contract_version:'growth-engine-v1',derivation_version:'growth-engine-derivation-v1',policy_id:'growth-engine-explicit-relation-v1',policy_fingerprint:hash('w'),generated_at:stamp,selection_mode:'selected_goal',selected_goal_source_uuid:id,eligible_goal_count:1,goal_results:[{goal:growthGoal,state:'goal_mapping_missing',cohort_fingerprint:stage10Cohort,behavioral_pattern:{cohort_fingerprint:stage10Cohort,pattern_type:'repeated_exact_choice',pattern_state:'current',provenance_fingerprint:hash('x'),source_count:3,reference_fingerprint:hash('y'),current_option:{option_index:0,option_fingerprint:stage10Option}},behavioral_option:{option_index:0,option_fingerprint:stage10Option},mapping:null,reason_codes:['GROWTH_GOAL_MAPPING_MISSING'],caveats:['mapping_requires_owner_review'],temporal:{goal_evidence_at:'unknown',goal_evidence_at_precision:'unknown',behavioral_generated_at:stamp,behavioral_current_window_start:null,behavioral_current_window_end:null,mapping_reviewed_at:null,mapping_created_at:null,advisor_requested_at:null},advisor:null}],reason_codes:[],caveats:[]};
const growthMappingReview={generated_at:stamp,goal:growthGoal,behavioral_target:{},candidate_mapping_fingerprint:hash('z'),goal_text:'Достичь ясного рабочего ритма',goal_domain:'work',goal_evidence_at:'unknown',goal_evidence_at_precision:'unknown',situation:'Перед рабочим блоком',information_known_at_decision_time:'Доступный контекст',criteria:['ясность'],ordered_options:[{option_index:0,option_fingerprint:stage10Option,label:'Сначала прояснить задачу'}],pattern_type:'repeated_exact_choice',pattern_state:'current',selected_option:{option_index:0,option_fingerprint:stage10Option},proposed_relation:'conflicts_with_goal',caveats:[]};
const growthMappingStatus={mapping_policy_id:'growth-goal-choice-explicit-mapping-v1',mapping_policy_fingerprint:hash('a'),mappings:[],active_mapping_count:0};
const growthLearningCandidate={contract_version:'growth-learning-v1',derivation_version:'growth-learning-derivation-v1',candidate_id:'gl1:'+('b'.repeat(64)),kind:'relation_review',reason_code:'missing_goal_mapping',growth_contract_version:'growth-engine-v1',growth_derivation_version:'growth-engine-derivation-v1',growth_policy_id:'growth-engine-explicit-relation-v1',growth_policy_fingerprint:hash('c'),goal_source_uuid:id,goal_identity_fingerprint:growthGoalIdentityFingerprint,growth_state:'goal_mapping_missing',cohort_fingerprint:stage10Cohort,behavioral_option_fingerprint:stage10Option,behavioral_reference_fingerprint:hash('d'),mapping_id:null,mapping_fingerprint:null,question:'Для текущего наблюдаемого варианта ещё не задано, как он относится к выбранной цели. Хочешь проверить эту связь?',basis_fingerprint:hash('e'),issued_at:stamp,expires_at:'2099-09-09T12:10:00Z'};
const growthAdvisorPreview={contract_version:'growth-advisor-v1',goal_source_uuid:id,goal_identity_fingerprint:growthGoalIdentityFingerprint,assistant_contract_version:'assistant-v1',advisor_policy_id:'growth-advisor-owner-explicit-goal-v1',goal_text:'Достичь ясного рабочего ритма',goal_text_utf8_bytes:42};
const assistant={kind:'analysis',output_label:'independent_recommendation_analysis',recommendation:null,selected_option:null,rationale:['Начни с небольшого шага и проверь результат.'],evidence_refs:[],constraints_used:[],objectives_used:[],uncertainty:[],abstention_code:null,contract_version:'assistant-v1'};
const growthAdvisorBranch={branch:'advisor',state:'result',assistant_result:{...assistant,output_label:'Независимый анализ',recommendation:'Проверь первый шаг'},error:null,provenance:{}};
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
  '/api/growth/goals':growthGoals,
  '/api/growth/mappings/status':growthMappingStatus,
  '/api/growth':growthResult,
  '/api/growth/mappings/review':growthMappingReview,
  '/api/growth/mappings/confirm':{status:'accepted',mapping:{mapping_id:'0198f4c5-6a00-7000-8000-000000000013'}},
  '/api/growth-learning/questions':{contract_version:'growth-learning-v1',status:'candidate',candidate:growthLearningCandidate,no_candidate_code:null},
  '/api/growth-learning/questions/resolve':{candidate_id:growthLearningCandidate.candidate_id,disposition:'ignore',answer_draft:null,handoff:null},
  '/api/growth-advisor/preview':growthAdvisorPreview,
  '/api/growth-advisor/execute':growthAdvisorBranch,
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
    await cognitive.getByRole('button',{name:'Показать проверку',exact:true}).click();
    await cognitive.getByText('Подписи показаны только для проверки человеком. Приложение не сопоставляет их автоматически по тексту.').waitFor();
    await cognitive.locator('.cognitive-twin-confirm-label input').check();
    await cognitive.getByRole('button',{name:'Подтвердить сопоставление',exact:true}).click();
    await cognitive.getByText('UUID принятого сопоставления').waitFor();
    const growth=page.locator('#growth-engine');
    await growth.getByRole('button',{name:'Обновить Growth',exact:true}).click();
    await growth.locator('#growth-goal-select').selectOption(id);
    await growth.getByRole('button',{name:'Построить Growth result',exact:true}).click();
    await growth.locator('#growth-relation-select').selectOption('conflicts_with_goal');
    await growth.getByRole('button',{name:'Уточнить связь — показать review',exact:true}).click();
    await growth.getByLabel('Я проверил Goal, exact-контекст и вариант и подтверждаю эту связь.').check();
    await growth.getByRole('button',{name:'Подтвердить связь',exact:true}).click();
    await growth.locator('#growth-advisor-task').fill('Проверить следующий шаг');
    await growth.getByRole('button',{name:'Показать Goal preview',exact:true}).click();
    await growth.locator('.growth-advisor-panel input[type=checkbox]').check();
    await growth.getByRole('button',{name:'Выполнить независимый анализ',exact:true}).click();
    await growth.getByText('Проверь первый шаг').waitFor();
    await growth.getByRole('button',{name:'Уточнить Growth — задать один вопрос',exact:true}).click();
    await growth.getByRole('button',{name:'Игнорировать',exact:true}).click();
    await growth.getByText('Вопрос проигнорирован без записи').waitFor();
    for(const selector of ['#timeline','#self-model','#cognitive-twin','#growth-engine','.self-retrieval-item','.simulate-me-result','.stage7-result-grid','#diagnostics']){
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
