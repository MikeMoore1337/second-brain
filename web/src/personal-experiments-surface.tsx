import { useEffect, useRef, useState, type ReactElement } from "react";

import { presentError } from "./presentation";
import {
  applyPersonalExperimentReview,
  evaluatePersonalExperiment,
  loadPersonalExperiments,
  preparePersonalExperimentDefinition,
  preparePersonalExperimentLifecycle,
  preparePersonalExperimentObservation,
  preparePersonalExperimentReassessment,
  type PersonalExperimentApplyResponse,
  type PersonalExperimentBaselineStrategy,
  type PersonalExperimentDefinition,
  type PersonalExperimentDefinitionPrepareRequest,
  type PersonalExperimentDisposition,
  type PersonalExperimentEvaluationResult,
  type PersonalExperimentEnrollment,
  type PersonalExperimentGoal,
  type PersonalExperimentItem,
  type PersonalExperimentLifecycleEvent,
  type PersonalExperimentReassessmentPrepareRequest,
  type PersonalExperimentReviewResponse,
  type PersonalExperimentStage12Definition,
  type PersonalExperimentStage12Observation,
  type PersonalExperimentState,
} from "./personal-experiments-api";

type Operation = "load" | "definition" | "lifecycle" | "observation" | "evaluate" | "reassessment" | "apply";
type SurfaceMode = "list" | "create";

const BASELINE_LABELS: Record<PersonalExperimentBaselineStrategy, string> = {
  stage12_definition_explicit: "Исходное значение из правила измерения",
  reviewed_pre_activation_observation: "Проверенное наблюдение до активации",
};

const LIFECYCLE_LABELS: Record<string, string> = {
  planned: "Запланирован",
  active: "Активен",
  completed: "Завершён",
  cancelled: "Отменён",
  invalid: "Недействителен",
};

const RESULT_LABELS: Record<string, string> = {
  not_evaluated: "Результат ещё не построен",
  descriptive_result: "Описательный результат",
  insufficient_observations: "Недостаточно наблюдений",
  not_comparable: "Нельзя сопоставить",
  policy_mismatch: "Несовпадение политики",
  source_missing: "Источник недоступен",
  source_changed: "Источник изменился",
};

const STAGE12_STATUS_LABELS: Record<string, string> = {
  target_met: "Цель достигнута",
  toward_target: "Движение к цели",
  away_from_target: "Движение от цели",
  unchanged: "Без заметного изменения",
  milestone_observations_available: "Есть наблюдения по этапам",
  insufficient_observations: "Недостаточно наблюдений",
  definition_missing: "Правило измерения не найдено",
  goal_source_changed: "Источник цели изменился",
  not_comparable: "Нельзя сопоставить",
};

const DISPOSITION_LABELS: Record<PersonalExperimentDisposition, string> = {
  continue: "Продолжить текущий эксперимент",
  stop: "Остановить эту линию",
  repeat_with_new_definition: "Повторить с новым определением",
  hold: "Оставить решение на паузе",
  not_decided: "Пока не решено",
};

function safeText(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function formatMoment(value: unknown): string {
  if (!isString(value)) return "время не указано";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordText(record: Readonly<Record<string, unknown>>, key: string, fallback = "—"): string {
  return safeText(record[key], fallback);
}

function valueCount(record: Readonly<Record<string, unknown>>, key: string): number {
  return Array.isArray(record[key]) ? record[key].length : 0;
}

function lifecycleLabel(value: string): string {
  return LIFECYCLE_LABELS[value] ?? "Состояние не определено";
}

function resultLabel(value: string): string {
  return RESULT_LABELS[value] ?? "Результат требует осторожного прочтения";
}

function stage12StatusLabel(value: unknown): string {
  return typeof value === "string" ? STAGE12_STATUS_LABELS[value] ?? value : "—";
}

function definitionSummary(definition: PersonalExperimentStage12Definition | undefined): string {
  if (!definition) return "Правило измерения прогресса недоступно.";
  if (definition.progress_model === "numeric_target") {
    return [
      definition.metric_id,
      definition.baseline ? `база ${definition.baseline}` : null,
      definition.target ? `цель ${definition.target}` : null,
      definition.unit,
    ].filter(isString).join(" · ") || "Числовое правило прогресса без краткого описания.";
  }
  const milestones = definition.milestones?.map((item) => item.label).filter(Boolean).slice(0, 3).join(", ");
  return milestones ? `Этапы: ${milestones}` : "Правило прогресса по этапам.";
}

function observationSummary(observation: PersonalExperimentStage12Observation): string {
  if (observation.progress_model === "numeric_target") {
    return [observation.value, observation.unit].filter(isString).join(" ") || "Числовое значение не указано";
  }
  return [observation.milestone_id, observation.state].filter(isString).join(" · ") || "Состояние этапа не указано";
}

function enrollmentSummary(enrollment: PersonalExperimentEnrollment): string {
  const value = recordText(enrollment, "value", "");
  const unit = recordText(enrollment, "unit", "");
  const milestone = recordText(enrollment, "milestone_id", "");
  const state = recordText(enrollment, "state", "");
  return [value && unit ? `${value} ${unit}` : value, milestone, state].filter(isString).join(" · ")
    || "Точное значение доступно в исходном наблюдении прогресса.";
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="pe-error" role="alert" tabIndex={-1} ref={ref}>{message}</p> : null;
}

function StatusPill({ children, tone = "neutral" }: { readonly children: ReactElement | string; readonly tone?: "neutral" | "accent" | "danger" }): ReactElement {
  return <span className={`pe-status-pill pe-status-pill-${tone}`}>{children}</span>;
}

function ReviewPayload({ review }: { readonly review: PersonalExperimentReviewResponse }): ReactElement {
  const payload = review.payload;
  if (review.record_kind === "definition") {
    return (
      <dl className="pe-review-fields">
        <div><dt>Гипотеза</dt><dd>{recordText(payload, "hypothesis")}</dd></div>
        <div><dt>Вмешательство</dt><dd>{recordText(payload, "intervention")}</dd></div>
        <div><dt>База</dt><dd>{BASELINE_LABELS[recordText(payload, "baseline_strategy") as PersonalExperimentBaselineStrategy] ?? recordText(payload, "baseline_strategy")}</dd></div>
      </dl>
    );
  }
  if (review.record_kind === "lifecycle") {
    return (
      <dl className="pe-review-fields">
        <div><dt>Событие</dt><dd>{recordText(payload, "lifecycle_event")}</dd></div>
        <div><dt>Время события</dt><dd>{formatMoment(payload.event_at)}</dd></div>
      </dl>
    );
  }
  if (review.record_kind === "observation") {
    return (
      <dl className="pe-review-fields">
        <div><dt>Наблюдение прогресса</dt><dd>{recordText(payload, "stage12_observation_id")}</dd></div>
        <div><dt>Отпечаток наблюдения</dt><dd>{recordText(payload, "stage12_observation_fingerprint")}</dd></div>
      </dl>
    );
  }
  return (
    <dl className="pe-review-fields">
      <div><dt>Решение владельца</dt><dd>{DISPOSITION_LABELS[recordText(payload, "disposition") as PersonalExperimentDisposition] ?? recordText(payload, "disposition")}</dd></div>
      <div><dt>Обоснование</dt><dd>{recordText(payload, "rationale")}</dd></div>
      <div><dt>Отпечаток результата</dt><dd>{recordText(payload, "result_fingerprint")}</dd></div>
    </dl>
  );
}

function ReviewPanel({
  review,
  confirmed,
  busy,
  onConfirmedChange,
  onApply,
  onDismiss,
}: {
  readonly review: PersonalExperimentReviewResponse;
  readonly confirmed: boolean;
  readonly busy: boolean;
  readonly onConfirmedChange: (value: boolean) => void;
  readonly onApply: () => void;
  readonly onDismiss: () => void;
}): ReactElement {
  return (
    <section className="pe-review" aria-labelledby="pe-review-title" data-personal-experiments-review>
      <div className="pe-section-heading">
        <span className="pe-step-number">Проверка</span>
        <div>
          <h3 id="pe-review-title">Проверь запись перед публикацией</h3>
          <p>Это предварительная проверка. Пока ты не подтвердил запись, хранилище не изменяется.</p>
        </div>
      </div>
      <ReviewPayload review={review} />
      <label className="pe-confirmation">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => onConfirmedChange(event.target.checked)}
        />
        <span>Я проверил точную цель, правило прогресса, содержимое записи и контрольный отпечаток плана.</span>
      </label>
      <div className="pe-action-row">
        <button type="button" className="pe-button pe-button-primary" disabled={!confirmed || busy} onClick={onApply}>
          {busy ? "Применяем…" : "Подтвердить запись"}
        </button>
        <button type="button" className="pe-button pe-button-secondary" disabled={busy} onClick={onDismiss}>Закрыть проверку</button>
      </div>
      <details className="pe-provenance">
        <summary>Показать точные идентификаторы проверки</summary>
        <dl>
          <div><dt>Тип</dt><dd>{review.record_kind}</dd></div>
          <div><dt>Идентификатор записи</dt><dd>{review.record_id}</dd></div>
          <div><dt>Отпечаток плана</dt><dd>{review.plan_sha256}</dd></div>
          <div><dt>Отпечаток содержимого</dt><dd>{review.content_sha256}</dd></div>
          <div><dt>ID цели</dt><dd>{review.bindings.goal_source_uuid}</dd></div>
          <div><dt>Идентификатор определения эксперимента</dt><dd>{review.bindings.experiment_definition_id}</dd></div>
        </dl>
      </details>
    </section>
  );
}

function GoalSelect({
  goals,
  value,
  onChange,
}: {
  readonly goals: readonly PersonalExperimentGoal[];
  readonly value: string;
  readonly onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="pe-field">
      <span>Точная текущая цель</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Выбери цель</option>
        {goals.map((goal) => <option value={goal.source_note_uuid} key={goal.source_note_uuid}>{goal.goal_text}</option>)}
      </select>
      <small>Эксперимент всегда привязан к одной текущей цели и её контрольному отпечатку.</small>
    </label>
  );
}

function ProgressDefinitionSelect({
  definitions,
  value,
  onChange,
}: {
  readonly definitions: readonly PersonalExperimentStage12Definition[];
  readonly value: string;
  readonly onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="pe-field">
      <span>Правило измерения прогресса</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={definitions.length === 0}>
        <option value="">Выбери правило</option>
        {definitions.map((definition) => <option value={definition.id} key={`${definition.id}:${definition.definition_fingerprint}`}>{definitionSummary(definition)}</option>)}
      </select>
      <small>{definitions.length === 0 ? "Для этой цели нет активного правила измерения." : "Выбирается только активное правило; устаревшая цепочка будет отклонена."}</small>
    </label>
  );
}

function CreateExperiment({
  state,
  busy,
  onPrepare,
}: {
  readonly state: PersonalExperimentState;
  readonly busy: boolean;
  readonly onPrepare: (request: PersonalExperimentDefinitionPrepareRequest) => void;
}): ReactElement {
  const [goalUuid, setGoalUuid] = useState(state.goals[0]?.source_note_uuid ?? "");
  const [definitionId, setDefinitionId] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [intervention, setIntervention] = useState("");
  const [baseline, setBaseline] = useState<PersonalExperimentBaselineStrategy>("stage12_definition_explicit");
  const [baselineObservationId, setBaselineObservationId] = useState("");

  const definitions = state.stage12_definitions.filter((item) => item.active && item.goal_source_uuid === goalUuid);
  const selectedGoal = state.goals.find((item) => item.source_note_uuid === goalUuid);
  const selectedDefinition = definitions.find((item) => item.id === definitionId);
  const baselineObservations = state.stage12_observations.filter((item) => (
    item.active
    && item.goal_source_uuid === goalUuid
    && item.progress_definition_id === definitionId
    && item.definition_fingerprint === selectedDefinition?.definition_fingerprint
    && item.observed_at_precision === "exact"
  ));

  const prepare = (): void => {
    if (!selectedGoal || !selectedDefinition || !hypothesis.trim() || !intervention.trim()) return;
    const selectedBaseline = baselineObservations.find((item) => item.id === baselineObservationId);
    onPrepare({
      goal_source_uuid: selectedGoal.source_note_uuid,
      goal_identity_fingerprint: selectedGoal.goal_identity_fingerprint,
      goal_progress_definition_id: selectedDefinition.id,
      goal_progress_definition_fingerprint: selectedDefinition.definition_fingerprint,
      hypothesis: hypothesis.trim(),
      intervention: intervention.trim(),
      baseline_strategy: baseline,
      baseline_observation_uuid: baseline === "reviewed_pre_activation_observation" ? selectedBaseline?.id ?? null : null,
      baseline_observation_fingerprint: baseline === "reviewed_pre_activation_observation" ? selectedBaseline?.observation_fingerprint ?? null : null,
      supersedes_definition_id: null,
      supersedes_definition_fingerprint: null,
    });
  };

  return (
    <section className="pe-editor" aria-labelledby="pe-editor-title">
      <div className="pe-section-heading">
        <span className="pe-step-number">01</span>
        <div>
          <h3 id="pe-editor-title">Определи эксперимент</h3>
          <p>Сформулируй проверяемую гипотезу, одно вмешательство и правило измерения.</p>
        </div>
      </div>
      <div className="pe-field-grid">
        <GoalSelect goals={state.goals} value={goalUuid} onChange={(value) => { setGoalUuid(value); setDefinitionId(""); setBaselineObservationId(""); }} />
        <ProgressDefinitionSelect definitions={definitions} value={definitionId} onChange={(value) => { setDefinitionId(value); setBaselineObservationId(""); }} />
      </div>
      <div className="pe-field-grid pe-field-grid-wide">
        <label className="pe-field"><span>Гипотеза</span><textarea value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} placeholder="Если я…, то в измеряемом прогрессе…" /></label>
        <label className="pe-field"><span>Вмешательство</span><textarea value={intervention} onChange={(event) => setIntervention(event.target.value)} placeholder="В течение окна эксперимента я буду…" /></label>
      </div>
      <fieldset className="pe-fieldset">
        <legend>Явная база</legend>
        <label className="pe-radio"><input type="radio" name="pe-baseline" checked={baseline === "stage12_definition_explicit"} onChange={() => { setBaseline("stage12_definition_explicit"); setBaselineObservationId(""); }} /><span>{BASELINE_LABELS.stage12_definition_explicit}</span></label>
        <label className="pe-radio"><input type="radio" name="pe-baseline" checked={baseline === "reviewed_pre_activation_observation"} onChange={() => setBaseline("reviewed_pre_activation_observation")} /><span>{BASELINE_LABELS.reviewed_pre_activation_observation}</span></label>
        {baseline === "reviewed_pre_activation_observation" ? (
          <label className="pe-field pe-field-nested"><span>Наблюдение для базы</span><select value={baselineObservationId} onChange={(event) => setBaselineObservationId(event.target.value)} disabled={baselineObservations.length === 0}>
            <option value="">Выбери проверенное наблюдение</option>
            {baselineObservations.map((item) => <option value={item.id} key={item.id}>{observationSummary(item)} · {formatMoment(item.observed_at)}</option>)}
          </select>
          <small>{baselineObservations.length === 0 ? "Нет подходящего точного наблюдения до активации." : "Время базы будет перепроверено при безопасной записи."}</small>
          </label>
        ) : null}
      </fieldset>
      <p className="pe-inline-note">Создание не запускает эксперимент и не меняет цель, разделы развития или Компас решения.</p>
      <button type="button" className="pe-button pe-button-primary" disabled={busy || !selectedGoal || !selectedDefinition || !hypothesis.trim() || !intervention.trim() || (baseline === "reviewed_pre_activation_observation" && !baselineObservationId)} onClick={prepare}>
        {busy ? "Готовим проверку…" : "Проверить и подготовить"}
      </button>
    </section>
  );
}

function Measurement({ definition }: { readonly definition: PersonalExperimentStage12Definition | undefined }): ReactElement {
  return (
    <section className="pe-section" aria-labelledby="pe-measurement-title">
      <div className="pe-section-heading"><span className="pe-step-number">03</span><div><h3 id="pe-measurement-title">Измерение</h3><p>Результат рассчитывается по действующему правилу измерения прогресса цели.</p></div></div>
      <div className="pe-measurement">
        <StatusPill tone={definition?.active ? "accent" : "danger"}>{definition?.active ? "Активное правило" : "Источник требует проверки"}</StatusPill>
        <p>{definitionSummary(definition)}</p>
        {definition ? <details className="pe-provenance"><summary>Показать идентификаторы правила</summary><dl><div><dt>ID</dt><dd>{definition.id}</dd></div><div><dt>Отпечаток</dt><dd>{definition.definition_fingerprint}</dd></div><div><dt>Проверено</dt><dd>{formatMoment(definition.definition_reviewed_at)}</dd></div></dl></details> : null}
      </div>
    </section>
  );
}

function LifecycleControls({
  experiment,
  busy,
  onAction,
}: {
  readonly experiment: PersonalExperimentItem;
  readonly busy: boolean;
  readonly onAction: (event: PersonalExperimentLifecycleEvent) => void;
}): ReactElement {
  const state = experiment.lifecycle.state;
  return (
    <section className="pe-section" aria-labelledby="pe-lifecycle-title">
      <div className="pe-section-heading"><span className="pe-step-number">02</span><div><h3 id="pe-lifecycle-title">Жизненный цикл</h3><p>Каждый переход — отдельное явное действие владельца через проверку и применение.</p></div></div>
      <div className="pe-lifecycle-summary"><StatusPill tone={state === "invalid" ? "danger" : state === "active" ? "accent" : "neutral"}>{lifecycleLabel(state)}</StatusPill><span>{experiment.lifecycle.activation ? `Активация: ${formatMoment(experiment.lifecycle.activation.event_at)}` : "Активация ещё не подтверждена."}</span>{experiment.lifecycle.terminal ? <span>Терминальное событие: {formatMoment(experiment.lifecycle.terminal.event_at)}</span> : null}</div>
      {experiment.lifecycle.issues.length > 0 ? <p className="pe-warning">Цепочка жизненного цикла требует проверки; новые переходы остановлены.</p> : null}
      <div className="pe-action-row">
        {state === "planned" ? <button type="button" className="pe-button pe-button-primary" disabled={busy} onClick={() => onAction("activation")}>Активировать явно</button> : null}
        {state === "active" ? <><button type="button" className="pe-button pe-button-secondary" disabled={busy} onClick={() => onAction("completion")}>Завершить эксперимент</button><button type="button" className="pe-button pe-button-danger" disabled={busy} onClick={() => onAction("cancellation")}>Отменить эксперимент</button></> : null}
      </div>
    </section>
  );
}

function ObservationEnrollment({
  experiment,
  busy,
  onEnroll,
}: {
  readonly experiment: PersonalExperimentItem;
  readonly busy: boolean;
  readonly onEnroll: (observation: PersonalExperimentStage12Observation) => void;
}): ReactElement {
  return (
    <section className="pe-section" aria-labelledby="pe-observations-title">
      <div className="pe-section-heading"><span className="pe-step-number">04</span><div><h3 id="pe-observations-title">Наблюдения</h3><p>Наблюдения прогресса попадают в эксперимент только после твоего подтверждения.</p></div></div>
      <div className="pe-observation-columns">
        <div><h4>Уже включены</h4>{experiment.enrollments.length > 0 ? <ul className="pe-record-list">{experiment.enrollments.map((item) => <li key={item.id}><strong>{enrollmentSummary(item)}</strong><small>{formatMoment(item.observed_at)} · {item.stage12_observation_id}</small></li>)}</ul> : <p className="pe-empty">Пока нет включённых наблюдений.</p>}</div>
        <div><h4>Доступны для включения</h4>{experiment.eligible_stage12_observations.length > 0 ? <ul className="pe-record-list">{experiment.eligible_stage12_observations.map((item) => <li key={item.id}><div><strong>{observationSummary(item)}</strong><small>{formatMoment(item.observed_at)} · проверено {formatMoment(item.observation_reviewed_at)}</small></div><button type="button" className="pe-button pe-button-secondary" disabled={busy} onClick={() => onEnroll(item)}>Включить</button></li>)}</ul> : <p className="pe-empty">Нет новых наблюдений, которые точно попадают в окно эксперимента.</p>}</div>
      </div>
    </section>
  );
}

function ResultSection({
  definition,
  result,
  busy,
  onEvaluate,
}: {
  readonly definition: PersonalExperimentDefinition;
  readonly result: PersonalExperimentEvaluationResult | null;
  readonly busy: boolean;
  readonly onEvaluate: () => void;
}): ReactElement {
  return (
    <section className="pe-section" aria-labelledby="pe-result-title">
      <div className="pe-section-heading"><span className="pe-step-number">05</span><div><h3 id="pe-result-title">Описательный результат</h3><p>Срез строится по текущему хранилищу, точной идентичности и указанной точке отсечения.</p></div></div>
      <div className="pe-causality-warning">Изменение во время эксперимента не доказывает причинность.</div>
      <p className="pe-inline-note">Этот результат описывает наблюдаемый срез. Он не создаёт адаптацию, не обучает систему развития и не вызывает провайдер.</p>
      <button type="button" className="pe-button pe-button-primary" disabled={busy} onClick={onEvaluate}>{busy ? "Строим результат…" : "Построить результат сейчас"}</button>
      {result ? (
        <div className="pe-result" data-personal-experiments-result>
          <div className="pe-result-header"><StatusPill tone={result.status === "not_comparable" || result.status === "policy_mismatch" ? "danger" : "accent"}>{resultLabel(result.status)}</StatusPill><span>Срез на: {formatMoment(result.as_of)}</span></div>
          <dl className="pe-result-counts"><div><dt>Включено</dt><dd>{valueCount(result, "included_observations")}</dd></div><div><dt>Исключено</dt><dd>{valueCount(result, "excluded_observations")}</dd></div><div><dt>Состояние прогресса</dt><dd>{stage12StatusLabel(result.stage12_status)}</dd></div></dl>
          <p className="pe-result-caveat">Наблюдаемый срез не является доказательством причинного эффекта и не меняет другие разделы системы.</p>
          <details className="pe-provenance"><summary>Показать происхождение результата</summary><dl><div><dt>Источник</dt><dd>{result.provenance.source}</dd></div><div><dt>Провайдер</dt><dd>{result.provenance.provider}</dd></div><div><dt>Сеть</dt><dd>{result.provenance.network}</dd></div><div><dt>Запись</dt><dd>{result.provenance.write}</dd></div><div><dt>Отпечаток результата</dt><dd>{result.result_fingerprint}</dd></div><div><dt>Отпечаток определения</dt><dd>{definition.experiment_definition_fingerprint}</dd></div></dl></details>
        </div>
      ) : <p className="pe-empty">Результат появляется только после явного действия владельца.</p>}
    </section>
  );
}

function ReassessmentSection({
  experiment,
  result,
  busy,
  onPrepare,
}: {
  readonly experiment: PersonalExperimentItem;
  readonly result: PersonalExperimentEvaluationResult | null;
  readonly busy: boolean;
  readonly onPrepare: (request: PersonalExperimentReassessmentPrepareRequest) => void;
}): ReactElement {
  const [disposition, setDisposition] = useState<PersonalExperimentDisposition>("not_decided");
  const [rationale, setRationale] = useState("");
  const terminal = experiment.lifecycle.state === "completed" || experiment.lifecycle.state === "cancelled";
  const prior = experiment.reassessments[0];
  const canPrepare = terminal && result !== null && rationale.trim().length > 0;

  return (
    <section className="pe-section" aria-labelledby="pe-reassessment-title">
      <div className="pe-section-heading"><span className="pe-step-number">06</span><div><h3 id="pe-reassessment-title">Пересмотр</h3><p>Окончательное решение владельца хранится отдельно от производного результата и требует новой проверки.</p></div></div>
      {experiment.reassessments.length > 0 ? <ul className="pe-record-list pe-reassessment-list">{experiment.reassessments.map((item) => <li key={item.id}><strong>{DISPOSITION_LABELS[item.disposition]}</strong><small>{item.rationale} · {formatMoment(item.reassessment_reviewed_at)}</small></li>)}</ul> : null}
      {!terminal ? <p className="pe-empty">Сначала явно заверши или отмени эксперимент.</p> : null}
      {terminal && !result ? <p className="pe-empty">Сначала построй текущий описательный результат.</p> : null}
      {terminal && result ? <div className="pe-reassessment-form"><label className="pe-field"><span>Решение</span><select value={disposition} onChange={(event) => setDisposition(event.target.value as PersonalExperimentDisposition)}>{Object.entries(DISPOSITION_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="pe-field"><span>Обоснование</span><textarea value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="Что именно ты решил по этому описательному срезу?" /></label><button type="button" className="pe-button pe-button-primary" disabled={!canPrepare || busy} onClick={() => onPrepare({ experiment_definition_id: experiment.definition.id, experiment_definition_fingerprint: experiment.definition.experiment_definition_fingerprint, result_fingerprint: result.result_fingerprint, evaluation_as_of: result.as_of, evaluation_policy_fingerprint: experiment.definition.experiment_policy_fingerprint, disposition, rationale: rationale.trim(), supersedes_reassessment_id: prior?.id ?? null, supersedes_reassessment_fingerprint: prior?.reassessment_fingerprint ?? null })}>{busy ? "Готовим проверку…" : "Проверить пересмотр"}</button></div> : null}
    </section>
  );
}

function ExperimentDetail({
  state,
  experiment,
  result,
  busy,
  onLifecycle,
  onEnroll,
  onEvaluate,
  onReassessment,
}: {
  readonly state: PersonalExperimentState;
  readonly experiment: PersonalExperimentItem;
  readonly result: PersonalExperimentEvaluationResult | null;
  readonly busy: Operation | null;
  readonly onLifecycle: (event: PersonalExperimentLifecycleEvent) => void;
  readonly onEnroll: (observation: PersonalExperimentStage12Observation) => void;
  readonly onEvaluate: () => void;
  readonly onReassessment: (request: PersonalExperimentReassessmentPrepareRequest) => void;
}): ReactElement {
  const definition = experiment.definition;
  const stage12Definition = state.stage12_definitions.find((item) => item.id === definition.goal_progress_definition_id && item.definition_fingerprint === definition.goal_progress_definition_fingerprint);
  return (
    <div className="pe-detail" data-personal-experiments-detail>
      <header className="pe-detail-header">
        <div><h3>{definition.hypothesis}</h3><p>{experiment.goal?.goal_text ?? "Точная цель сейчас недоступна."}</p></div>
        <StatusPill tone={definition.state === "active" ? "accent" : "danger"}>{definition.state === "active" ? "Текущее определение" : "Нужна проверка"}</StatusPill>
      </header>
      <section className="pe-section pe-section-first" aria-labelledby="pe-intervention-title"><div className="pe-section-heading"><span className="pe-step-number">01</span><div><h3 id="pe-intervention-title">Гипотеза и вмешательство</h3><p>Содержимое задаётся владельцем и не преобразуется в цель или совет.</p></div></div><div className="pe-copy-grid"><div><h4>Гипотеза</h4><p>{definition.hypothesis}</p></div><div><h4>Вмешательство</h4><p>{definition.intervention}</p></div></div><p className="pe-baseline"><strong>База:</strong> {BASELINE_LABELS[definition.baseline_strategy]}{definition.baseline_observation_uuid ? ` · ${definition.baseline_observation_uuid}` : ""}</p></section>
      <LifecycleControls experiment={experiment} busy={busy === "lifecycle"} onAction={onLifecycle} />
      <Measurement definition={stage12Definition} />
      <ObservationEnrollment experiment={experiment} busy={busy === "observation"} onEnroll={onEnroll} />
      <ResultSection definition={definition} result={result} busy={busy === "evaluate"} onEvaluate={onEvaluate} />
      <ReassessmentSection experiment={experiment} result={result} busy={busy === "reassessment"} onPrepare={onReassessment} />
      <details className="pe-provenance pe-detail-provenance"><summary>Показать техническое происхождение эксперимента</summary><dl><div><dt>ID определения эксперимента</dt><dd>{definition.id}</dd></div><div><dt>Отпечаток эксперимента</dt><dd>{definition.experiment_definition_fingerprint}</dd></div><div><dt>Отпечаток цели</dt><dd>{definition.goal_identity_fingerprint}</dd></div><div><dt>Отпечаток правила измерения</dt><dd>{definition.goal_progress_definition_fingerprint}</dd></div><div><dt>Политика</dt><dd>{definition.experiment_policy_id} · {definition.experiment_policy_fingerprint}</dd></div></dl></details>
    </div>
  );
}

export function PersonalExperimentsSurface(): ReactElement {
  const [state, setState] = useState<PersonalExperimentState | null>(null);
  const [mode, setMode] = useState<SurfaceMode>("list");
  const [selectedId, setSelectedId] = useState("");
  const [result, setResult] = useState<PersonalExperimentEvaluationResult | null>(null);
  const [review, setReview] = useState<PersonalExperimentReviewResponse | null>(null);
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [busy, setBusy] = useState<Operation | null>(null);
  const [status, setStatus] = useState("Раздел не обращается к данным, пока владелец не нажмёт «Загрузить эксперименты». ");
  const [error, setError] = useState("");

  const selected = state?.experiments.find((item) => item.definition.id === selectedId) ?? null;

  const loadState = async (): Promise<void> => {
    setBusy("load");
    setError("");
    try {
      const loaded = await loadPersonalExperiments();
      setState(loaded);
      setSelectedId((current) => loaded.experiments.some((item) => item.definition.id === current) ? current : loaded.experiments[0]?.definition.id ?? "");
      setStatus(loaded.experiments.length > 0 ? `Загружено экспериментов: ${loaded.experiments.length}.` : "Экспериментов пока нет. Можно создать первый.");
    } catch (caught) {
      setError(presentError(caught, "Не удалось загрузить личные эксперименты."));
      setStatus("Состояние не загружено.");
    } finally {
      setBusy(null);
    }
  };

  const run = async (operation: Operation, action: () => Promise<void>): Promise<void> => {
    setBusy(operation);
    setError("");
    try {
      await action();
    } catch (caught) {
      setError(presentError(caught, "Операция личного эксперимента не выполнена."));
    } finally {
      setBusy(null);
    }
  };

  const prepareDefinition = (request: PersonalExperimentDefinitionPrepareRequest): void => {
    void run("definition", async () => {
      setReview(await preparePersonalExperimentDefinition(request));
      setReviewConfirmed(false);
      setStatus("Проверка подготовлена; хранилище не изменено.");
    });
  };

  const prepareLifecycle = (event: PersonalExperimentLifecycleEvent): void => {
    if (!selected) return;
    void run("lifecycle", async () => {
      setReview(await preparePersonalExperimentLifecycle({
        experiment_definition_id: selected.definition.id,
        experiment_definition_fingerprint: selected.definition.experiment_definition_fingerprint,
        lifecycle_event: event,
        event_at: new Date().toISOString(),
        supersedes_lifecycle_id: null,
        supersedes_lifecycle_fingerprint: null,
      }));
      setReviewConfirmed(false);
      setStatus("Переход подготовлен; активация или завершение ещё не применены.");
    });
  };

  const prepareEnrollment = (observation: PersonalExperimentStage12Observation): void => {
    if (!selected) return;
    void run("observation", async () => {
      setReview(await preparePersonalExperimentObservation({
        experiment_definition_id: selected.definition.id,
        experiment_definition_fingerprint: selected.definition.experiment_definition_fingerprint,
        stage12_observation_id: observation.id,
        stage12_observation_fingerprint: observation.observation_fingerprint,
        supersedes_observation_id: null,
        supersedes_observation_fingerprint: null,
      }));
      setReviewConfirmed(false);
      setStatus("Включение подготовлено; наблюдение ещё не добавлено в эксперимент.");
    });
  };

  const evaluate = (): void => {
    if (!selected) return;
    void run("evaluate", async () => {
      const evaluated = await evaluatePersonalExperiment(selected.definition.id, selected.definition.experiment_definition_fingerprint, new Date().toISOString());
      setResult(evaluated);
      setStatus("Описательный результат построен по текущему срезу; он не записан в хранилище.");
    });
  };

  const prepareReassessment = (request: PersonalExperimentReassessmentPrepareRequest): void => {
    void run("reassessment", async () => {
      setReview(await preparePersonalExperimentReassessment(request));
      setReviewConfirmed(false);
      setStatus("Пересмотр подготовлен; решение ещё не сохранено.");
    });
  };

  const applyReview = (): void => {
    if (!review) return;
    if (!reviewConfirmed) {
      setError("Сначала подтверди, что проверил запись и точный отпечаток плана.");
      return;
    }
    const currentReview = review;
    void run("apply", async () => {
      const applied: PersonalExperimentApplyResponse = await applyPersonalExperimentReview(currentReview.review_token, currentReview.plan_sha256, true, currentReview.record_kind);
      setReview(null);
      setReviewConfirmed(false);
      if (applied.record_kind === "definition") setSelectedId(applied.record_id);
      setResult(null);
      setStatus(applied.status === "saved" ? "Запись сохранена после явного подтверждения." : "Запись откатили после неудачной проверки.");
      await loadState();
    });
  };

  const openCreate = (): void => {
    setMode("create");
    setReview(null);
    setReviewConfirmed(false);
    setError("");
  };

  const selectExperiment = (id: string): void => {
    setMode("list");
    setSelectedId(id);
    setResult(null);
    setReview(null);
    setReviewConfirmed(false);
    setError("");
  };

  return (
    <section className="personal-experiments-surface" id="personal-experiments" aria-labelledby="personal-experiments-title" aria-busy={busy !== null} data-personal-experiments-state={state ? "loaded" : "idle"}>
      <header className="pe-heading">
        <div><h3 id="personal-experiments-title">Личные эксперименты</h3><p>Проверяй свои гипотезы и отслеживай прогресс цели. Наблюдаемое изменение само по себе не доказывает, что его вызвал эксперимент.</p></div>
        <div className="pe-heading-meta"><span>Только текущая страница</span><span>Без провайдера и автоадаптации</span></div>
      </header>
      <div className="pe-causality-warning pe-causality-warning-heading">Изменение во время эксперимента не доказывает причинность.</div>
      <div className="pe-toolbar"><p className="pe-status" role="status" aria-live="polite">{status}</p><button type="button" className="pe-button pe-button-secondary" disabled={busy !== null} onClick={() => void loadState()}>{busy === "load" ? "Загружаем…" : "Загрузить эксперименты"}</button>{state ? <button type="button" className="pe-button pe-button-primary" disabled={busy !== null} onClick={openCreate}>Создать эксперимент</button> : null}</div>
      <ErrorMessage message={error} />
      {state && state.experiments.length > 0 ? <nav className="pe-experiment-list" aria-label="Список личных экспериментов"><h4>Твои эксперименты</h4>{state.experiments.map((item) => <button type="button" className={`pe-experiment-choice${item.definition.id === selectedId ? " is-selected" : ""}`} key={item.definition.id} onClick={() => selectExperiment(item.definition.id)}><span><strong>{item.definition.hypothesis}</strong><small>{item.goal?.goal_text ?? "Цель недоступна"}</small></span><StatusPill tone={item.lifecycle.state === "active" ? "accent" : "neutral"}>{lifecycleLabel(item.lifecycle.state)}</StatusPill></button>)}</nav> : null}
      {state && mode === "create" ? <CreateExperiment state={state} busy={busy === "definition"} onPrepare={prepareDefinition} /> : null}
      {state && mode === "list" && selected ? <ExperimentDetail state={state} experiment={selected} result={result} busy={busy} onLifecycle={prepareLifecycle} onEnroll={prepareEnrollment} onEvaluate={evaluate} onReassessment={prepareReassessment} /> : null}
      {state && mode === "list" && state.experiments.length > 0 && !selected ? <div className="pe-empty-state"><h4>Эксперимент не выбран</h4><p>Выбери запись из списка, чтобы увидеть жизненный цикл, наблюдения и результат.</p></div> : null}
      {state && state.experiments.length === 0 && mode === "list" ? <div className="pe-empty-state"><h4>Экспериментов пока нет</h4><p>Создай первое определение после выбора точной цели и правила прогресса.</p><button type="button" className="pe-button pe-button-primary" disabled={busy !== null} onClick={openCreate}>Создать эксперимент</button></div> : null}
      {review ? <ReviewPanel review={review} confirmed={reviewConfirmed} busy={busy === "apply"} onConfirmedChange={setReviewConfirmed} onApply={applyReview} onDismiss={() => { setReview(null); setReviewConfirmed(false); }} /> : null}
      {state ? <details className="pe-provenance pe-state-provenance"><summary>Показать происхождение списка</summary><dl><div><dt>Контракт</dt><dd>{state.contract_id}</dd></div><div><dt>Идентификатор происхождения</dt><dd>{state.derivation_id}</dd></div><div><dt>Политика</dt><dd>{state.policy_id} · {state.policy_fingerprint}</dd></div><div><dt>Срез</dt><dd>{formatMoment(state.generated_at)}</dd></div></dl></details> : null}
    </section>
  );
}
