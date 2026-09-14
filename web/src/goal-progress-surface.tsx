import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  applyGoalProgressDefinition,
  applyGoalProgressObservation,
  loadGrowthGoalProgress,
  prepareGoalProgressDefinition,
  prepareGoalProgressObservation,
  type GoalProgressApplyResponse,
  type GoalProgressDefinitionPrepareRequest,
  type GoalProgressDefinitionProjection,
  type GoalProgressDirection,
  type GoalProgressMilestone,
  type GoalProgressModel,
  type GoalProgressObservationPrepareRequest,
  type GoalProgressObservationProjection,
  type GoalProgressResult,
  type GoalProgressReviewResponse,
  type GrowthGoalProgressCompositionResponse,
  type GrowthGoalOwnerItem,
} from "./api";
import { presentError } from "./presentation";

type ProgressOperation = "check" | "definition" | "observation";
type TimeMode = "" | "exact" | "unknown";
type DefinitionModel = "" | GoalProgressModel;
type MilestoneDraft = { id: string; label: string; ordinal: string };

const EMPTY_MILESTONE: MilestoneDraft = { id: "", label: "", ordinal: "1" };

const PROGRESS_STATUS_COPY: Record<string, string> = {
  target_met: "Текущее значение соответствует заданной цели.",
  toward_target: "Последнее сопоставимое значение движется в сторону цели.",
  away_from_target: "Последнее сопоставимое значение движется от цели.",
  unchanged: "Сопоставимое значение не изменилось относительно предыдущего.",
  milestone_observations_available: "Есть записи по этапам; их состояния показаны отдельно.",
  insufficient_observations: "Сопоставимых записей пока недостаточно для описания изменения.",
  definition_missing: "Для этой цели пока не определено, как измерять прогресс.",
  goal_source_changed: "Источник цели изменился; требуется новая проверка.",
  not_comparable: "Текущие записи нельзя сопоставить по действующему правилу.",
};

const PROGRESS_STATUS_LABEL: Record<string, string> = {
  target_met: "Целевое значение достигнуто",
  toward_target: "Движение к цели",
  away_from_target: "Движение от цели",
  unchanged: "Без изменения",
  milestone_observations_available: "Есть записи по этапам",
  insufficient_observations: "Недостаточно сопоставимых записей",
  definition_missing: "Правило измерения не задано",
  goal_source_changed: "Источник цели изменился",
  not_comparable: "Сопоставление недоступно",
};

const GROWTH_STATE_LABEL: Record<string, string> = {
  supports_goal: "Согласуется с целью",
  conflicts_with_goal: "Конфликтует с целью",
  neutral_or_unknown: "Нейтрально или неизвестно",
  goal_mapping_missing: "Связь ещё не задана",
  mixed_behavior: "Смешанные варианты",
  changed_behavior: "Состояние изменилось",
  behavioral_evidence_insufficient: "Недостаточно сопоставимых данных",
  not_comparable: "Нельзя сопоставить",
  goal_source_missing: "Источник цели недоступен",
  goal_selection_required: "Нужно выбрать цель",
};

const DIRECTION_LABEL: Record<string, string> = {
  increase_to: "увеличение до цели",
  decrease_to: "снижение до цели",
  reach_exact: "достижение точного значения",
};

const MILESTONE_STATE_LABEL: Record<string, string> = {
  completed: "Выполнено",
  not_completed: "Не выполнено",
};

function safeString(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function isAbortError(error: unknown): boolean {
  return (error instanceof DOMException && error.name === "AbortError")
    || (typeof error === "object" && error !== null && "name" in error && error.name === "AbortError");
}

function eventTimeToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function valuesFromResult(result: GoalProgressResult | null): {
  readonly metric: string;
  readonly unit: string;
  readonly baseline: string;
  readonly target: string;
  readonly direction: GoalProgressDirection | "";
  readonly current: string;
} {
  const explanation = result?.explanation ?? {};
  return {
    metric: typeof explanation.metric_id === "string" ? explanation.metric_id : "",
    unit: typeof explanation.unit === "string" ? explanation.unit : "",
    baseline: typeof explanation.baseline === "string" ? explanation.baseline : "",
    target: typeof explanation.target === "string" ? explanation.target : "",
    direction: typeof explanation.direction === "string" ? explanation.direction as GoalProgressDirection : "",
    current: typeof explanation.current_value === "string" ? explanation.current_value : "",
  };
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="growth-error" role="alert" tabIndex={-1} ref={ref}>{message}</p> : null;
}

function DefinitionDetails({ definition }: { readonly definition: GoalProgressDefinitionProjection }): ReactElement {
  if (definition.progress_model === "numeric_target") {
    return (
      <dl className="goal-progress-fields">
        <div><dt>Метрика</dt><dd>{safeString(definition.metric_id)}</dd></div>
        <div><dt>Единица</dt><dd>{safeString(definition.unit)}</dd></div>
        <div><dt>Базовое значение</dt><dd>{safeString(definition.baseline)}</dd></div>
        <div><dt>Целевое значение</dt><dd>{safeString(definition.target)}</dd></div>
        <div><dt>Направление</dt><dd>{DIRECTION_LABEL[definition.direction ?? ""] ?? "не задано"}</dd></div>
        <div><dt>Время проверки правила</dt><dd>{safeString(definition.definition_reviewed_at)}</dd></div>
        {definition.lower_bound ? <div><dt>Нижняя граница</dt><dd>{definition.lower_bound}</dd></div> : null}
        {definition.upper_bound ? <div><dt>Верхняя граница</dt><dd>{definition.upper_bound}</dd></div> : null}
      </dl>
    );
  }
  return (
    <>
      <p className="goal-progress-note">Правило проверено владельцем: {safeString(definition.definition_reviewed_at)}</p>
      <ol className="goal-progress-milestone-list">
        {(definition.milestones ?? []).map((milestone) => (
          <li key={milestone.id}><span>{milestone.label}</span><small>{milestone.id}</small></li>
        ))}
      </ol>
    </>
  );
}

function CurrentObservation({ observation }: { readonly observation: GoalProgressObservationProjection }): ReactElement {
  return (
    <li className="goal-progress-observation">
      {observation.progress_model === "numeric_target"
        ? <span>{safeString(observation.value)} {safeString(observation.unit, "")}</span>
        : <span>{safeString(observation.milestone_id)} — {MILESTONE_STATE_LABEL[observation.state ?? ""] ?? "состояние не задано"}</span>}
      <small>Событие: {observation.observed_at_precision === "unknown" ? "Неизвестно" : safeString(observation.observed_at)} · Проверено: {safeString(observation.observation_reviewed_at)}</small>
    </li>
  );
}

function recordText(record: Readonly<Record<string, unknown>>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function recordMilestones(record: Readonly<Record<string, unknown>>): readonly GoalProgressMilestone[] {
  const value = record.milestones;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const candidate = item as Record<string, unknown>;
    return typeof candidate.id === "string" && typeof candidate.label === "string" && typeof candidate.ordinal === "number"
      ? [{ id: candidate.id, label: candidate.label, ordinal: candidate.ordinal }]
      : [];
  });
}

function ReviewedRecordDetails({ review }: { readonly review: GoalProgressReviewResponse }): ReactElement {
  const record = review.record;
  const model = recordText(record, "progress_model");
  if (review.record_kind === "definition" && model === "numeric_target") {
    return (
      <dl className="goal-progress-fields goal-progress-review-fields">
        <div><dt>Показатель</dt><dd>{safeString(recordText(record, "metric_id"))}</dd></div>
        <div><dt>Единица</dt><dd>{safeString(recordText(record, "unit"))}</dd></div>
        <div><dt>Исходное значение</dt><dd>{safeString(recordText(record, "baseline"))}</dd></div>
        <div><dt>Целевое значение</dt><dd>{safeString(recordText(record, "target"))}</dd></div>
        <div><dt>Направление</dt><dd>{DIRECTION_LABEL[recordText(record, "direction") ?? ""] ?? "не задано"}</dd></div>
        {recordText(record, "lower_bound") ? <div><dt>Нижняя граница</dt><dd>{recordText(record, "lower_bound")}</dd></div> : null}
        {recordText(record, "upper_bound") ? <div><dt>Верхняя граница</dt><dd>{recordText(record, "upper_bound")}</dd></div> : null}
      </dl>
    );
  }
  if (review.record_kind === "definition" && model === "milestone_set") {
    return (
      <div>
        <p className="goal-progress-review-label">Этапы</p>
        <ol className="goal-progress-milestone-list goal-progress-review-fields">
          {recordMilestones(record).map((milestone) => <li key={milestone.id}><span>{milestone.label}</span><small>{milestone.id}</small></li>)}
        </ol>
      </div>
    );
  }
  if (review.record_kind === "observation" && model === "numeric_target") {
    return (
      <dl className="goal-progress-fields goal-progress-review-fields">
        <div><dt>Показатель</dt><dd>{safeString(recordText(record, "metric_id"))}</dd></div>
        <div><dt>Единица</dt><dd>{safeString(recordText(record, "unit"))}</dd></div>
        <div><dt>Значение</dt><dd>{safeString(recordText(record, "value"))}</dd></div>
      </dl>
    );
  }
  return (
    <dl className="goal-progress-fields goal-progress-review-fields">
      <div><dt>Этап</dt><dd>{safeString(recordText(record, "milestone_id"))}</dd></div>
      <div><dt>Состояние</dt><dd>{MILESTONE_STATE_LABEL[recordText(record, "state") ?? ""] ?? "состояние не задано"}</dd></div>
    </dl>
  );
}

function ProgressDetails({
  result,
  definition,
  observations,
}: {
  readonly result: GoalProgressResult;
  readonly definition: GoalProgressDefinitionProjection | null;
  readonly observations: readonly GoalProgressObservationProjection[];
}): ReactElement {
  const values = valuesFromResult(result);
  if (result.status === "definition_missing") {
    return (
      <div className="goal-progress-empty-state">
        <p>Для этой цели пока не определено, как измерять прогресс.</p>
      </div>
    );
  }
  if (definition?.progress_model === "numeric_target") {
    return (
      <>
        <dl className="goal-progress-fields goal-progress-numeric-fields">
          <div><dt>Метрика</dt><dd>{safeString(values.metric)}</dd></div>
          <div><dt>Единица</dt><dd>{safeString(values.unit)}</dd></div>
          <div><dt>База</dt><dd>{safeString(values.baseline)}</dd></div>
          <div><dt>Текущее значение</dt><dd>{safeString(values.current, "нет сопоставимого значения")}</dd></div>
          <div><dt>Цель</dt><dd>{safeString(values.target)}</dd></div>
          <div><dt>Направление</dt><dd>{DIRECTION_LABEL[values.direction] ?? "не задано"}</dd></div>
          <div><dt>Дата среза</dt><dd>{safeString(result.as_of)}</dd></div>
        </dl>
        {result.unknown_time_count > 0 ? <p className="goal-progress-note">Записи с неизвестным временем сохранены, но не включены в текущее сравнение.</p> : null}
      </>
    );
  }
  if (definition?.progress_model === "milestone_set") {
    const completed = result.completed_milestone_ids.length;
    const notCompleted = result.not_completed_milestone_ids.length;
    const missing = result.missing_milestone_ids.length;
    return (
      <>
        <p className="goal-progress-counts">Выполнено: {completed}. Не выполнено: {notCompleted}. Нет измерения: {missing}.</p>
        <ul className="goal-progress-milestone-list goal-progress-state-list">
          {(definition.milestones ?? []).map((milestone) => {
            const state = result.completed_milestone_ids.includes(milestone.id)
              ? "completed"
              : result.not_completed_milestone_ids.includes(milestone.id)
                ? "not_completed"
                : "missing";
            return <li key={milestone.id}><span>{milestone.label}</span><small>{state === "missing" ? "Нет измерения" : MILESTONE_STATE_LABEL[state]}</small></li>;
          })}
        </ul>
        {result.unknown_time_count > 0 ? <p className="goal-progress-note">Записи с неизвестным временем сохранены, но не включены в текущий срез.</p> : null}
      </>
    );
  }
  return <p className="goal-progress-note">{PROGRESS_STATUS_COPY[result.status] ?? "Результат не определён по текущей политике."}</p>;
}

function CompositionView({
  composition,
  onConfigure,
}: {
  readonly composition: GrowthGoalProgressCompositionResponse;
  readonly onConfigure: () => void;
}): ReactElement {
  const progress = composition.goal_progress_result;
  const relation = composition.growth_result.goal_results[0];
  return (
    <div className="goal-progress-composition">
      <section className="goal-progress-independent-block" aria-labelledby="goal-progress-growth-title">
        <h5 id="goal-progress-growth-title">Связь поведения с целью</h5>
        <p className="goal-progress-state-label">{GROWTH_STATE_LABEL[relation?.state ?? ""] ?? "Состояние не определено"}</p>
        <p>{relation?.state === "goal_mapping_missing" ? "Явная связь ещё не задана. Это не означает конфликт." : "Этот блок описывает только точную связь поведения с выбранной целью."}</p>
        <details className="growth-technical"><summary>Технические сведения о связи</summary><dl className="goal-progress-fields goal-progress-technical-fields"><div><dt>Источник цели</dt><dd>{composition.selected_goal_source_uuid}</dd></div><div><dt>Идентификатор связи</dt><dd>{safeString(relation?.mapping?.mapping_id)}</dd></div><div><dt>Отпечаток цели</dt><dd>{composition.current_goal_identity_fingerprint}</dd></div></dl></details>
      </section>
      <section className="goal-progress-independent-block" aria-labelledby="goal-progress-measure-title">
        <div className="goal-progress-block-heading"><h5 id="goal-progress-measure-title">Измеряемый прогресс</h5><span className="goal-progress-state-label">{PROGRESS_STATUS_LABEL[progress.status] ?? "Состояние не определено"}</span></div>
        <p>{PROGRESS_STATUS_COPY[progress.status] ?? "Результат не определён по текущей политике."}</p>
        {progress.excluded_observations.length > 0 ? <p className="goal-progress-note">Не использовано измерений: {progress.excluded_observations.length}.</p> : null}
        {progress.status === "definition_missing" ? <button className="growth-button growth-button-secondary" type="button" onClick={onConfigure}>Настроить измерение</button> : null}
        <ProgressDetails result={progress} definition={composition.definition} observations={composition.observations} />
        {composition.observations.length > 0 ? <><h6 className="goal-progress-subheading">Текущие записи</h6><ul className="goal-progress-observation-list">{composition.observations.map((observation) => <CurrentObservation key={observation.id} observation={observation} />)}</ul></> : null}
        <details className="growth-technical"><summary>Технические сведения о прогрессе</summary><dl className="goal-progress-fields goal-progress-technical-fields"><div><dt>Идентификатор цели</dt><dd>{progress.selected_goal_source_uuid}</dd></div><div><dt>Правило измерения</dt><dd>{safeString(progress.active_definition_uuid)}</dd></div><div><dt>Отпечаток правила</dt><dd>{safeString(progress.definition_fingerprint)}</dd></div><div><dt>Время среза</dt><dd>{progress.as_of}</dd></div><div><dt>Исключено записей</dt><dd>{progress.excluded_observations.length}</dd></div></dl></details>
      </section>
    </div>
  );
}

export function GoalProgressSurface({ selectedGoal }: { readonly selectedGoal: GrowthGoalOwnerItem }): ReactElement {
  const [composition, setComposition] = useState<GrowthGoalProgressCompositionResponse | null>(null);
  const [operation, setOperation] = useState<ProgressOperation | null>(null);
  const [status, setStatus] = useState("Проверка связи и измеряемого прогресса запускается только по явной кнопке.");
  const [error, setError] = useState("");
  const [showDefinitionEditor, setShowDefinitionEditor] = useState(false);
  const [definitionModel, setDefinitionModel] = useState<DefinitionModel>("");
  const [metricId, setMetricId] = useState("");
  const [unit, setUnit] = useState("");
  const [baseline, setBaseline] = useState("");
  const [target, setTarget] = useState("");
  const [direction, setDirection] = useState<GoalProgressDirection | "">("");
  const [lowerBound, setLowerBound] = useState("");
  const [upperBound, setUpperBound] = useState("");
  const [milestones, setMilestones] = useState<MilestoneDraft[]>([{ ...EMPTY_MILESTONE }]);
  const [supersedesDefinitionId, setSupersedesDefinitionId] = useState<string | null>(null);
  const [definitionReview, setDefinitionReview] = useState<GoalProgressReviewResponse | null>(null);
  const [definitionConfirmed, setDefinitionConfirmed] = useState(false);
  const [observationValue, setObservationValue] = useState("");
  const [observationMilestoneId, setObservationMilestoneId] = useState("");
  const [observationState, setObservationState] = useState<"" | "completed" | "not_completed">("");
  const [observationTimeMode, setObservationTimeMode] = useState<TimeMode>("");
  const [observationAt, setObservationAt] = useState("");
  const [supersedesObservationId, setSupersedesObservationId] = useState<string | null>(null);
  const [observationReview, setObservationReview] = useState<GoalProgressReviewResponse | null>(null);
  const [observationConfirmed, setObservationConfirmed] = useState(false);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const currentProgress = composition?.goal_progress_result ?? null;
  const currentDefinition = composition?.definition ?? null;

  function startOperation(next: ProgressOperation): AbortController {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setOperation(next);
    setError("");
    return nextController;
  }

  async function checkProgress(force = false): Promise<void> {
    if (operation && !force) return;
    const nextController = startOperation("check");
    setStatus("Сервер заново проверяет текущую цель, связь и измеряемый прогресс…");
    try {
      const result = await loadGrowthGoalProgress(
        selectedGoal.goal.source_note_uuid,
        new Date().toISOString(),
        undefined,
        nextController.signal,
      );
      if (nextController.signal.aborted) return;
      setComposition(result);
      setStatus("Два независимых блока готовы. Они не объединяются в общий вывод о цели.");
    } catch (caught) {
      if (isAbortError(caught) || nextController.signal.aborted) return;
      setError(presentError(caught, "Связь и измеряемый прогресс сейчас недоступны."));
      setStatus("");
    } finally {
      if (!nextController.signal.aborted) {
        controller.current = null;
        setOperation(null);
      }
    }
  }

  function openDefinitionEditor(): void {
    const definition = currentDefinition;
    setDefinitionModel(definition?.progress_model ?? "");
    setMetricId(definition?.metric_id ?? "");
    setUnit(definition?.unit ?? "");
    setBaseline(definition?.baseline ?? "");
    setTarget(definition?.target ?? "");
    setDirection(definition?.direction ?? "");
    setLowerBound(definition?.lower_bound ?? "");
    setUpperBound(definition?.upper_bound ?? "");
    setMilestones(definition?.milestones?.map((item) => ({ id: item.id, label: item.label, ordinal: String(item.ordinal) })) ?? [{ ...EMPTY_MILESTONE }]);
    setSupersedesDefinitionId(definition?.id ?? null);
    setDefinitionReview(null);
    setDefinitionConfirmed(false);
    setShowDefinitionEditor(true);
  }

  function updateMilestone(index: number, field: keyof MilestoneDraft, value: string): void {
    setMilestones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: value } : item));
    setDefinitionReview(null);
    setDefinitionConfirmed(false);
  }

  function definitionRequest(): GoalProgressDefinitionPrepareRequest | null {
    if (!definitionModel) return null;
    if (definitionModel === "numeric_target") {
      return {
        goal_source_uuid: selectedGoal.goal.source_note_uuid,
        progress_model: definitionModel,
        metric_id: metricId,
        unit,
        baseline,
        target,
        direction: direction || null,
        lower_bound: lowerBound || null,
        upper_bound: upperBound || null,
        milestones: null,
        supersedes_definition_id: supersedesDefinitionId,
      };
    }
    return {
      goal_source_uuid: selectedGoal.goal.source_note_uuid,
      progress_model: definitionModel,
      metric_id: null,
      unit: null,
      baseline: null,
      target: null,
      direction: null,
      lower_bound: null,
      upper_bound: null,
      milestones: milestones.map((item) => ({ id: item.id, label: item.label, ordinal: Number(item.ordinal) })),
      supersedes_definition_id: supersedesDefinitionId,
    };
  }

  async function prepareDefinition(): Promise<void> {
    const request = definitionRequest();
    if (!request || operation) {
      if (!request) setError("Выбери модель и заполни её поля перед проверкой.");
      return;
    }
    const nextController = startOperation("definition");
    setDefinitionReview(null);
    setDefinitionConfirmed(false);
    setStatus("Сервер готовит точный план правила измерения без записи в хранилище…");
    try {
      const review = await prepareGoalProgressDefinition(request, undefined, nextController.signal);
      if (nextController.signal.aborted) return;
      setDefinitionReview(review);
      setStatus("План готов. Проверь каждое значение и явно подтверди запись.");
    } catch (caught) {
      if (isAbortError(caught) || nextController.signal.aborted) return;
      setError(presentError(caught, "Не удалось подготовить правило измерения."));
      setStatus("");
    } finally {
      if (!nextController.signal.aborted) {
        controller.current = null;
        setOperation(null);
      }
    }
  }

  async function applyDefinition(): Promise<void> {
    if (!definitionReview || !definitionConfirmed || operation) return;
    const nextController = startOperation("definition");
    setStatus("Сервер повторно проверяет план и выполняет только подтверждённую запись…");
    try {
      const saved: GoalProgressApplyResponse = await applyGoalProgressDefinition(definitionReview.review_token, definitionReview.plan_sha256, undefined, nextController.signal);
      if (nextController.signal.aborted) return;
      setDefinitionReview(null);
      setDefinitionConfirmed(false);
      setShowDefinitionEditor(false);
      controller.current = null;
      setOperation(null);
      setStatus(`Правило измерения сохранено. Идентификатор записи: ${saved.record_id}. Сервер обновляет текущий срез.`);
      await checkProgress(true);
    } catch (caught) {
      if (isAbortError(caught) || nextController.signal.aborted) return;
      setError(presentError(caught, "Правило измерения не сохранено. Начни проверку заново."));
      setStatus("");
    } finally {
      if (!nextController.signal.aborted) {
        controller.current = null;
        setOperation(null);
      }
    }
  }

  function startObservationCorrection(observationId: string): void {
    setSupersedesObservationId(observationId);
    setObservationReview(null);
    setObservationConfirmed(false);
    setStatus("Новая запись будет явно связана с выбранной исходной записью; история останется без изменений.");
  }

  function observationRequest(): GoalProgressObservationPrepareRequest | null {
    if (!currentDefinition || !observationTimeMode) return null;
    const observedAt = observationTimeMode === "unknown" ? "unknown" : eventTimeToIso(observationAt);
    if (!observedAt) return null;
    if (currentDefinition.progress_model === "numeric_target") {
      if (!observationValue) return null;
      return {
        goal_source_uuid: selectedGoal.goal.source_note_uuid,
        progress_definition_id: currentDefinition.id,
        value: observationValue,
        milestone_id: null,
        state: null,
        observed_at: observedAt,
        observed_at_precision: observationTimeMode,
        supersedes_observation_id: supersedesObservationId,
      };
    }
    if (!observationMilestoneId || !observationState) return null;
    return {
      goal_source_uuid: selectedGoal.goal.source_note_uuid,
      progress_definition_id: currentDefinition.id,
      value: null,
      milestone_id: observationMilestoneId,
      state: observationState,
      observed_at: observedAt,
      observed_at_precision: observationTimeMode,
      supersedes_observation_id: supersedesObservationId,
    };
  }

  async function prepareObservation(): Promise<void> {
    const request = observationRequest();
    if (!request || operation) {
      if (!request) setError("Выбери точность времени и заполни поля наблюдения перед проверкой.");
      return;
    }
    const nextController = startOperation("observation");
    setObservationReview(null);
    setObservationConfirmed(false);
    setStatus("Сервер готовит точный план записи наблюдения без записи в хранилище…");
    try {
      const review = await prepareGoalProgressObservation(request, undefined, nextController.signal);
      if (nextController.signal.aborted) return;
      setObservationReview(review);
      setStatus("План наблюдения готов. Проверь событие, время и связь с правилом.");
    } catch (caught) {
      if (isAbortError(caught) || nextController.signal.aborted) return;
      setError(presentError(caught, "Не удалось подготовить запись наблюдения."));
      setStatus("");
    } finally {
      if (!nextController.signal.aborted) {
        controller.current = null;
        setOperation(null);
      }
    }
  }

  async function applyObservation(): Promise<void> {
    if (!observationReview || !observationConfirmed || operation) return;
    const nextController = startOperation("observation");
    setStatus("Сервер повторно проверяет план и выполняет только подтверждённую запись…");
    try {
      const saved: GoalProgressApplyResponse = await applyGoalProgressObservation(observationReview.review_token, observationReview.plan_sha256, undefined, nextController.signal);
      if (nextController.signal.aborted) return;
      setObservationReview(null);
      setObservationConfirmed(false);
      setSupersedesObservationId(null);
      setObservationValue("");
      setObservationMilestoneId("");
      setObservationState("");
      setObservationTimeMode("");
      setObservationAt("");
      controller.current = null;
      setOperation(null);
      setStatus(`Наблюдение сохранено. Идентификатор записи: ${saved.record_id}. Сервер обновляет текущий срез.`);
      await checkProgress(true);
    } catch (caught) {
      if (isAbortError(caught) || nextController.signal.aborted) return;
      setError(presentError(caught, "Запись наблюдения не сохранена. Начни проверку заново."));
      setStatus("");
    } finally {
      if (!nextController.signal.aborted) {
        controller.current = null;
        setOperation(null);
      }
    }
  }

  function reviewRecord(review: GoalProgressReviewResponse): ReactElement {
    return (
      <div className="goal-progress-review" aria-labelledby="goal-progress-review-title">
        <h6 id="goal-progress-review-title">Проверка перед записью</h6>
        <p>Сервер подготовил новую неизменяемую запись. Проверь точные значения, а затем явно подтверди действие.</p>
        <dl className="goal-progress-fields">
          <div><dt>Тип записи</dt><dd>{review.record_kind === "definition" ? "Правило измерения" : "Наблюдение"}</dd></div>
          <div><dt>Цель</dt><dd>{review.goal.text}</dd></div>
          <div><dt>Идентификатор записи</dt><dd>{review.record_id}</dd></div>
          <div><dt>Время создания</dt><dd>{review.write.created}</dd></div>
          {review.record_kind === "observation" && typeof review.record.observed_at === "string" ? <div><dt>Время события</dt><dd>{review.record.observed_at === "unknown" ? "Неизвестно" : review.record.observed_at}</dd></div> : null}
          {review.record_kind === "observation" && typeof review.record.supersedes_observation_id === "string" ? <div><dt>Исправляет запись</dt><dd>{review.record.supersedes_observation_id}</dd></div> : null}
          {review.record_kind === "definition" && typeof review.record.supersedes_definition_id === "string" ? <div><dt>Заменяет правило</dt><dd>{review.record.supersedes_definition_id}</dd></div> : null}
        </dl>
        <ReviewedRecordDetails review={review} />
        {review.record_kind === "definition" && typeof review.record.supersedes_definition_id === "string" ? <p className="goal-progress-note">Предыдущая запись правила останется в истории.</p> : null}
        {review.record_kind === "observation" && typeof review.record.supersedes_observation_id === "string" ? <p className="goal-progress-note">Исходная запись наблюдения останется в истории.</p> : null}
        <details className="growth-technical"><summary>Отпечаток точного плана</summary><dl className="goal-progress-fields goal-progress-technical-fields"><div><dt>Отпечаток плана</dt><dd>{review.plan_sha256}</dd></div><div><dt>Отпечаток содержимого</dt><dd>{review.write.content_sha256}</dd></div></dl></details>
      </div>
    );
  }

  return (
    <section className="goal-progress-surface" aria-labelledby="goal-progress-title">
      <div className="goal-progress-heading">
        <div><p className="growth-eyebrow">Этап 12</p><h4 id="goal-progress-title">Связь цели и измеряемый прогресс</h4><p>Выбранная цель: {selectedGoal.goal_text}</p></div>
        <button className="growth-button growth-button-secondary" type="button" disabled={operation !== null} aria-busy={operation === "check"} onClick={() => void checkProgress()}>{operation === "check" ? "Проверяю…" : "Проверить прогресс"}</button>
      </div>
      <p className="growth-status">{status}</p>
      <ErrorMessage message={error} />

      {composition ? <CompositionView composition={composition} onConfigure={openDefinitionEditor} /> : <p className="goal-progress-empty-state">Нажми «Проверить прогресс», чтобы получить текущие два независимых блока от сервера.</p>}

      {currentProgress?.status === "definition_missing" && !showDefinitionEditor ? <button className="growth-button growth-button-secondary" type="button" disabled={operation !== null} onClick={openDefinitionEditor}>Настроить измерение</button> : null}
      {currentDefinition && !showDefinitionEditor ? <button className="growth-button growth-button-quiet" type="button" disabled={operation !== null} onClick={openDefinitionEditor}>Изменить правило измерения</button> : null}

      {showDefinitionEditor ? (
        <section className="goal-progress-editor" aria-labelledby="goal-progress-definition-title">
          <h5 id="goal-progress-definition-title">Правило измерения</h5>
          <p>Выбери только смысл правила. Идентификаторы цели, отпечаток политики, время проверки и путь записи принадлежат серверу.</p>
          <label className="growth-label" htmlFor="goal-progress-definition-model">Модель измерения</label>
          <select className="growth-input" id="goal-progress-definition-model" value={definitionModel} disabled={operation !== null} onChange={(event) => { setDefinitionModel(event.target.value as DefinitionModel); setDefinitionReview(null); setDefinitionConfirmed(false); }}>
            <option value="">Выбери модель</option>
            <option value="numeric_target">Числовая цель</option>
            <option value="milestone_set">Набор этапов</option>
          </select>
          {definitionModel === "numeric_target" ? <div className="goal-progress-form-grid">
            <label className="growth-label" htmlFor="goal-progress-metric">Метрика<input className="growth-input" id="goal-progress-metric" value={metricId} disabled={operation !== null} onChange={(event) => setMetricId(event.target.value)} /></label>
            <label className="growth-label" htmlFor="goal-progress-unit">Единица измерения<input className="growth-input" id="goal-progress-unit" value={unit} disabled={operation !== null} onChange={(event) => setUnit(event.target.value)} /></label>
            <label className="growth-label" htmlFor="goal-progress-baseline">Базовое значение<input className="growth-input" id="goal-progress-baseline" value={baseline} disabled={operation !== null} onChange={(event) => setBaseline(event.target.value)} /></label>
            <label className="growth-label" htmlFor="goal-progress-target">Целевое значение<input className="growth-input" id="goal-progress-target" value={target} disabled={operation !== null} onChange={(event) => setTarget(event.target.value)} /></label>
            <label className="growth-label" htmlFor="goal-progress-direction">Направление<select className="growth-input" id="goal-progress-direction" value={direction} disabled={operation !== null} onChange={(event) => setDirection(event.target.value as GoalProgressDirection | "")}><option value="">Выбери направление</option><option value="increase_to">Увеличение до цели</option><option value="decrease_to">Снижение до цели</option><option value="reach_exact">Точное значение</option></select></label>
            <label className="growth-label" htmlFor="goal-progress-lower">Нижняя граница<input className="growth-input" id="goal-progress-lower" value={lowerBound} disabled={operation !== null} onChange={(event) => setLowerBound(event.target.value)} /></label>
            <label className="growth-label" htmlFor="goal-progress-upper">Верхняя граница<input className="growth-input" id="goal-progress-upper" value={upperBound} disabled={operation !== null} onChange={(event) => setUpperBound(event.target.value)} /></label>
          </div> : null}
          {definitionModel === "milestone_set" ? <div className="goal-progress-milestone-editor"><p className="growth-review-note">Этапы не имеют весов; порядок нужен только для отображения.</p>{milestones.map((milestone, index) => <div className="goal-progress-milestone-row" key={`${index}-${milestone.id}`}><label className="growth-label" htmlFor={`goal-progress-milestone-id-${index}`}>Идентификатор<input className="growth-input" id={`goal-progress-milestone-id-${index}`} value={milestone.id} disabled={operation !== null} onChange={(event) => updateMilestone(index, "id", event.target.value)} /></label><label className="growth-label" htmlFor={`goal-progress-milestone-label-${index}`}>Название<input className="growth-input" id={`goal-progress-milestone-label-${index}`} value={milestone.label} disabled={operation !== null} onChange={(event) => updateMilestone(index, "label", event.target.value)} /></label><label className="growth-label" htmlFor={`goal-progress-milestone-ordinal-${index}`}>Порядок<input className="growth-input" id={`goal-progress-milestone-ordinal-${index}`} type="number" min="1" value={milestone.ordinal} disabled={operation !== null} onChange={(event) => updateMilestone(index, "ordinal", event.target.value)} /></label>{milestones.length > 1 ? <button className="growth-button growth-button-quiet" type="button" disabled={operation !== null} onClick={() => setMilestones((current) => current.filter((_item, itemIndex) => itemIndex !== index))}>Удалить этап</button> : null}</div>)}<button className="growth-button growth-button-secondary" type="button" disabled={operation !== null} onClick={() => setMilestones((current) => [...current, { ...EMPTY_MILESTONE, ordinal: String(current.length + 1) }])}>Добавить этап</button></div> : null}
          <div className="goal-progress-action-row"><button className="growth-button growth-button-secondary" type="button" disabled={operation !== null || !definitionModel} aria-busy={operation === "definition"} onClick={() => void prepareDefinition()}>{operation === "definition" ? "Готовлю…" : "Проверить правило"}</button><button className="growth-button growth-button-quiet" type="button" disabled={operation !== null} onClick={() => setShowDefinitionEditor(false)}>Отменить</button></div>
          {definitionReview ? <>{reviewRecord(definitionReview)}<label className="growth-confirm-label"><input type="checkbox" checked={definitionConfirmed} disabled={operation !== null} onChange={(event) => setDefinitionConfirmed(event.target.checked)} />Я проверил правило и подтверждаю добавление новой записи.</label><button className="growth-button growth-button-primary" type="button" disabled={!definitionConfirmed || operation !== null} aria-busy={operation === "definition"} onClick={() => void applyDefinition()}>Подтвердить запись правила</button></> : null}
        </section>
      ) : null}

      {currentDefinition ? (
        <section className="goal-progress-editor" aria-labelledby="goal-progress-observation-title">
          <h5 id="goal-progress-observation-title">Новое наблюдение</h5>
          <p>Наблюдение добавляется отдельной записью. Исходная история не редактируется.</p>
          <DefinitionDetails definition={currentDefinition} />
          {currentDefinition.progress_model === "numeric_target" ? <label className="growth-label" htmlFor="goal-progress-observation-value">Текущее значение<input className="growth-input" id="goal-progress-observation-value" value={observationValue} disabled={operation !== null} onChange={(event) => setObservationValue(event.target.value)} /></label> : <><label className="growth-label" htmlFor="goal-progress-observation-milestone">Этап<select className="growth-input" id="goal-progress-observation-milestone" value={observationMilestoneId} disabled={operation !== null} onChange={(event) => setObservationMilestoneId(event.target.value)}><option value="">Выбери этап</option>{(currentDefinition.milestones ?? []).map((milestone) => <option value={milestone.id} key={milestone.id}>{milestone.label}</option>)}</select></label><label className="growth-label" htmlFor="goal-progress-observation-state">Состояние этапа<select className="growth-input" id="goal-progress-observation-state" value={observationState} disabled={operation !== null} onChange={(event) => setObservationState(event.target.value as "" | "completed" | "not_completed")}><option value="">Выбери состояние</option><option value="completed">Выполнено</option><option value="not_completed">Не выполнено</option></select></label></>}
          <label className="growth-label" htmlFor="goal-progress-observation-time">Время события<select className="growth-input" id="goal-progress-observation-time" value={observationTimeMode} disabled={operation !== null} onChange={(event) => { const value = event.target.value as TimeMode; setObservationTimeMode(value); setObservationAt(value === "exact" ? observationAt : ""); }}><option value="">Выбери точность времени</option><option value="exact">Точное время</option><option value="unknown">Неизвестно</option></select></label>
          {observationTimeMode === "exact" ? <div className="goal-progress-time-row"><label className="growth-label" htmlFor="goal-progress-observed-at">Дата и время<input className="growth-input" id="goal-progress-observed-at" type="datetime-local" value={observationAt} disabled={operation !== null} onChange={(event) => setObservationAt(event.target.value)} /></label><button className="growth-button growth-button-secondary" type="button" disabled={operation !== null} onClick={() => { setObservationTimeMode("exact"); setObservationAt(new Date().toISOString().slice(0, 16)); }}>Сейчас</button></div> : null}
          {observationTimeMode === "unknown" ? <p className="goal-progress-note">Событие будет сохранено с временем «Неизвестно» и не попадёт в текущее сравнение.</p> : null}
          {supersedesObservationId ? <p className="goal-progress-note">Исправляется запись: {supersedesObservationId}. Будет создана новая запись.</p> : null}
          <div className="goal-progress-action-row"><button className="growth-button growth-button-secondary" type="button" disabled={operation !== null} aria-busy={operation === "observation"} onClick={() => void prepareObservation()}>{operation === "observation" ? "Готовлю…" : "Проверить наблюдение"}</button>{supersedesObservationId ? <button className="growth-button growth-button-quiet" type="button" disabled={operation !== null} onClick={() => setSupersedesObservationId(null)}>Отменить исправление</button> : null}</div>
          {observationReview ? <>{reviewRecord(observationReview)}<label className="growth-confirm-label"><input type="checkbox" checked={observationConfirmed} disabled={operation !== null} onChange={(event) => setObservationConfirmed(event.target.checked)} />Я проверил наблюдение и подтверждаю добавление новой записи.</label><button className="growth-button growth-button-primary" type="button" disabled={!observationConfirmed || operation !== null} aria-busy={operation === "observation"} onClick={() => void applyObservation()}>Подтвердить запись наблюдения</button></> : null}
          {composition?.observations.length ? <div className="goal-progress-correction-list"><h6>Исправления</h6>{composition.observations.map((observation) => <button className="growth-button growth-button-quiet" type="button" key={observation.id} disabled={operation !== null} onClick={() => startObservationCorrection(observation.id)}>Исправить запись {observation.id}</button>)}</div> : null}
        </section>
      ) : null}
    </section>
  );
}
