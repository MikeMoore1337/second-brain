import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  buildDecisionCompass,
  executeDecisionCompassAdvisor,
  loadBehavioralSelfModel,
  loadGrowthGoals,
  previewDecisionCompassAdvisor,
  type BehavioralPattern,
  type DecisionCompassAdvisorBranch,
  type DecisionCompassBehaviorBranch,
  type DecisionCompassCriterion,
  type DecisionCompassRequest,
  type DecisionCompassResponse,
  type DecisionCompassSimulateBranch,
  type GrowthAdvisorPreviewResponse,
  type GrowthGoalsResponse,
} from "./api";

type BusyAction = "goals" | "behavior" | "build" | "advisor-preview" | "advisor-execute" | null;

interface EditableOption {
  readonly id: string;
  readonly label: string;
}

interface EditableCriterion extends DecisionCompassCriterion {
  readonly description: string | null;
}

const MAX_OPTIONS = 8;
const MAX_CRITERIA = 8;

const BEHAVIOR_STATE_LABELS: Record<string, string> = {
  not_selected: "Контекст не выбран",
  result: "Наблюдаемый контекст доступен",
  insufficient: "Недостаточно наблюдений",
  not_comparable: "Контекст нельзя сопоставить",
  unavailable: "Наблюдаемый контекст недоступен",
  error: "Наблюдаемый контекст завершился безопасной ошибкой",
};

const SIMULATE_STATE_LABELS: Record<string, string> = {
  result: "Вероятный выбор построен",
  abstention: "Вероятный выбор не определён",
  error: "Вероятный выбор недоступен",
};

const ADVISOR_STATE_LABELS: Record<string, string> = {
  not_requested: "Независимая рекомендация ещё не запрошена",
  result: "Независимая рекомендация получена",
  abstention: "Независимая рекомендация воздержалась",
  error: "Независимая рекомендация завершилась безопасной ошибкой",
};

const PROGRESS_STATE_LABELS: Record<string, string> = {
  target_met: "Цель достигнута по заданному правилу",
  toward_target: "Движение к заданной цели",
  away_from_target: "Движение от заданной цели",
  unchanged: "Изменение не обнаружено",
  milestone_observations_available: "Есть наблюдения по этапам",
  insufficient_observations: "Недостаточно наблюдений",
  definition_missing: "Правило измерения не задано",
  goal_source_changed: "Источник цели изменился",
  not_comparable: "Прогресс нельзя сопоставить",
};

const RELATION_COPY: Record<string, string> = {
  supports_goal: "Связь согласуется с целью",
  conflicts_with_goal: "Связь конфликтует с целью",
  neutral_or_unknown: "Связь нейтральна или неизвестна",
  goal_mapping_missing: "Явная связь ещё не задана",
  mixed_behavior: "Наблюдались разные варианты",
  changed_behavior: "Наблюдаемое состояние изменилось",
  behavioral_evidence_insufficient: "Недостаточно сопоставимых данных",
  not_comparable: "Связь нельзя сопоставить",
  goal_source_missing: "Источник цели недоступен",
};

const RELATION_EXPLANATION: Record<string, string> = {
  supports_goal: "Это сохранённая владельцем описательная связь, а не доказательство результата.",
  conflicts_with_goal: "Это сохранённая владельцем описательная связь, а не моральная оценка или причинный вывод.",
  neutral_or_unknown: "Отсутствие поддержки не означает конфликт.",
  goal_mapping_missing: "Без явной связи вывод о согласовании или конфликте не строится.",
  mixed_behavior: "Разные наблюдения сохраняются рядом; единый вывод не создаётся.",
  changed_behavior: "Это описание наблюдаемой динамики, а не утверждение о личности.",
  behavioral_evidence_insufficient: "Без сопоставимых данных безопасный результат — воздержаться.",
  not_comparable: "Точная политика не позволяет сопоставить эти источники.",
  goal_source_missing: "Связь недоступна без подтверждённого текущего источника цели.",
};

const RELATION_LABELS: Record<string, string> = {
  simulate_advisor_same_option: "Simulate Me и совет указали один exact ID",
  simulate_advisor_different_options: "Simulate Me и совет указали разные exact ID",
  simulate_advisor_not_comparable: "Simulate Me и совет нельзя сопоставить",
  simulate_behavior_same_option: "Simulate Me и поведение указали один exact ID",
  simulate_behavior_different_options: "Simulate Me и поведение указали разные exact ID",
  simulate_behavior_not_comparable: "Simulate Me и поведение нельзя сопоставить",
  advisor_behavior_same_option: "Совет и поведение указали один exact ID",
  advisor_behavior_different_options: "Совет и поведение указали разные exact ID",
  advisor_behavior_not_comparable: "Совет и поведение нельзя сопоставить",
  behavior_binding_missing: "Для поведенческого сравнения не задана exact-связка",
  behavior_scope_not_selected: "Поведенческий контекст владельцем не выбран",
  advisor_not_requested: "Совет ещё не запрашивался",
};

function stateLabel(value: string, labels: Record<string, string>, fallback: string): string {
  return labels[value] ?? fallback;
}

function safeText(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function safeError(value: unknown, fallback: string): string {
  const message = value instanceof Error ? value.message : "";
  return /[А-Яа-яЁё]/u.test(message) ? message : fallback;
}

function nowIso(): string {
  return new Date().toISOString();
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  if (!message) return null;
  return (
    <p className="decision-compass-error" role="alert" tabIndex={-1} ref={ref}>
      {message}
    </p>
  );
}

function branchError(branch: { readonly error: { readonly message?: string } | null }): string {
  const message = branch.error?.message;
  return typeof message === "string" && /[А-Яа-яЁё]/u.test(message)
    ? message
    : "Эта ветка временно недоступна.";
}

function selectedOption(branch: DecisionCompassSimulateBranch): string | null {
  const value = branch.result?.selected_option;
  if (!value || typeof value.id !== "string") return null;
  return value.id;
}

function advisorResultLines(branch: DecisionCompassAdvisorBranch): string[] {
  const result = branch.result;
  if (!result) return [];
  const lines: string[] = [];
  for (const key of ["output_label", "recommendation"]) {
    const value = result[key];
    if (typeof value === "string" && value) lines.push(value);
  }
  for (const key of ["rationale", "uncertainty"]) {
    const value = result[key];
    if (Array.isArray(value)) {
      lines.push(...value.filter((item): item is string => typeof item === "string" && item.length > 0));
    }
  }
  const option = result.selected_option;
  if (typeof option === "object" && option !== null && "label" in option && typeof option.label === "string") {
    lines.push(`Выбранный вариант: ${option.label}`);
  }
  return lines.slice(0, 8);
}

function patternsWithCohorts(response: { readonly patterns: readonly BehavioralPattern[] } | null): BehavioralPattern[] {
  if (!response) return [];
  const seen = new Set<string>();
  return response.patterns.filter((pattern) => {
    const fingerprint = pattern.cohort?.cohort_fingerprint;
    if (!fingerprint || seen.has(fingerprint)) return false;
    seen.add(fingerprint);
    return true;
  });
}

function makeRequest(
  goals: GrowthGoalsResponse,
  selectedGoalUuid: string,
  task: string,
  options: readonly EditableOption[],
  criteria: readonly EditableCriterion[],
  progressAsOf: string,
  scopeFingerprint: string,
  binding: DecisionCompassRequest["behavioral_option_binding"],
): DecisionCompassRequest {
  const goal = goals.goals.find((item) => item.goal.source_note_uuid === selectedGoalUuid);
  return {
    contract_version: "growth-compare-v1",
    task,
    options,
    selected_goal: goal
      ? {
          source_uuid: goal.goal.source_note_uuid,
          identity_fingerprint: goal.goal_identity_fingerprint,
        }
      : null,
    criteria,
    explicit_constraints: [],
    explicit_context: [],
    progress_as_of: progressAsOf,
    behavioral_scope: scopeFingerprint
      ? { behavioral_cohort_fingerprint: scopeFingerprint }
      : null,
    behavioral_option_binding: binding,
    max_result_bytes: 65536,
  };
}

export function DecisionCompassSurface(): ReactElement {
  const [goals, setGoals] = useState<GrowthGoalsResponse | null>(null);
  const [selectedGoalUuid, setSelectedGoalUuid] = useState("");
  const [task, setTask] = useState("");
  const [options, setOptions] = useState<EditableOption[]>([
    { id: "decision-option-1", label: "" },
  ]);
  const [criteria, setCriteria] = useState<EditableCriterion[]>([]);
  const [progressAsOf, setProgressAsOf] = useState(nowIso);
  const [behavior, setBehavior] = useState<{ readonly patterns: readonly BehavioralPattern[] } | null>(null);
  const [scopeFingerprint, setScopeFingerprint] = useState("");
  const [binding, setBinding] = useState<DecisionCompassRequest["behavioral_option_binding"]>(null);
  const [bindingOptionId, setBindingOptionId] = useState("");
  const [compass, setCompass] = useState<DecisionCompassResponse | null>(null);
  const [advisorPreview, setAdvisorPreview] = useState<GrowthAdvisorPreviewResponse | null>(null);
  const [advisorConfirmed, setAdvisorConfirmed] = useState(false);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [status, setStatus] = useState("Данные Decision Compass загружаются только после явного действия.");
  const [error, setError] = useState("");
  const optionCounter = useRef(2);
  const criterionCounter = useRef(1);

  const cohortPatterns = patternsWithCohorts(behavior);
  const selectedPattern = cohortPatterns.find(
    (pattern) => pattern.cohort?.cohort_fingerprint === scopeFingerprint,
  );
  const selectedGoal = goals?.goals.find((item) => item.goal.source_note_uuid === selectedGoalUuid) ?? null;

  function resetDerivedState(): void {
    setCompass(null);
    setAdvisorPreview(null);
    setAdvisorConfirmed(false);
    setBinding(null);
    setBindingOptionId("");
    setError("");
  }

  function updateOption(index: number, label: string): void {
    setOptions((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label } : item));
    setBinding(null);
  }

  function addOption(): void {
    if (options.length >= MAX_OPTIONS) return;
    const id = `decision-option-${optionCounter.current}`;
    optionCounter.current += 1;
    setOptions((current) => [...current, { id, label: "" }]);
  }

  function removeOption(index: number): void {
    if (options.length <= 1) return;
    setOptions((current) => current.filter((_item, itemIndex) => itemIndex !== index));
    setBinding(null);
  }

  function addCriterion(): void {
    if (criteria.length >= MAX_CRITERIA) return;
    const id = `decision-criterion-${criterionCounter.current}`;
    criterionCounter.current += 1;
    setCriteria((current) => [...current, { id, label: "", description: null }]);
  }

  function updateCriterion(index: number, field: "label" | "description", value: string): void {
    setCriteria((current) => current.map((item, itemIndex) => itemIndex === index
      ? { ...item, [field]: value || (field === "description" ? null : "") }
      : item));
  }

  function removeCriterion(index: number): void {
    setCriteria((current) => current.filter((_item, itemIndex) => itemIndex !== index));
  }

  async function refreshGoals(): Promise<void> {
    setBusy("goals");
    setError("");
    try {
      const response = await loadGrowthGoals();
      setGoals(response);
      setSelectedGoalUuid("");
      resetDerivedState();
      setStatus("Текущие цели загружены. Выбери ровно одну явно.");
    } catch (caught) {
      setError(safeError(caught, "Не удалось загрузить текущие цели."));
      setStatus("Список целей не загружен.");
    } finally {
      setBusy(null);
    }
  }

  async function loadBehaviorContexts(): Promise<void> {
    setBusy("behavior");
    setError("");
    try {
      const response = await loadBehavioralSelfModel();
      setBehavior(response);
      setStatus("Наблюдаемые контексты загружены. Ни один из них не выбран автоматически.");
    } catch (caught) {
      setError(safeError(caught, "Не удалось загрузить наблюдаемые контексты."));
    } finally {
      setBusy(null);
    }
  }

  function chooseGoal(value: string): void {
    setSelectedGoalUuid(value);
    setScopeFingerprint("");
    resetDerivedState();
    setStatus(value ? "Цель выбрана. Нажми отдельную кнопку построения." : "Цель не выбрана.");
  }

  function chooseScope(value: string): void {
    setScopeFingerprint(value);
    setBinding(null);
    setBindingOptionId("");
    setCompass(null);
    setAdvisorPreview(null);
    setAdvisorConfirmed(false);
  }

  function bindBehaviorOption(): void {
    const selectedOption = selectedPattern?.selected_option;
    if (!selectedOption || !scopeFingerprint || !bindingOptionId) return;
    setBinding({
      request_option_id: bindingOptionId,
      behavioral_cohort_fingerprint: scopeFingerprint,
      behavioral_option_index: selectedOption.option_index,
      behavioral_option_fingerprint: selectedOption.option_fingerprint,
    });
    setStatus("Exact-связка варианта и наблюдаемого контекста задана явно.");
  }

  function currentRequest(): DecisionCompassRequest | null {
    if (!goals || !selectedGoalUuid) {
      setError("Сначала загрузи и явно выбери одну текущую цель.");
      return null;
    }
    if (!task.trim()) {
      setError("Добавь формулировку задачи владельца.");
      return null;
    }
    if (options.some((option) => !option.label.trim())) {
      setError("Заполни подпись каждого варианта или удали пустой вариант.");
      return null;
    }
    if (options.length < 1 || options.length > MAX_OPTIONS) {
      setError("Нужно от одного до восьми вариантов.");
      return null;
    }
    if (criteria.some((criterion) => !criterion.label.trim())) {
      setError("Заполни подпись каждого критерия или удали пустой критерий.");
      return null;
    }
    if (binding && (!scopeFingerprint || !options.some((option) => option.id === binding.request_option_id))) {
      setError("Exact-связка устарела; выбери поведенческий контекст и вариант заново.");
      return null;
    }
    return makeRequest(
      goals,
      selectedGoalUuid,
      task.trim(),
      options.map((option) => ({ id: option.id, label: option.label.trim() })),
      criteria.map((criterion) => ({
        id: criterion.id,
        label: criterion.label.trim(),
        description: criterion.description?.trim() || null,
      })),
      progressAsOf,
      scopeFingerprint.trim(),
      binding,
    );
  }

  async function buildCompass(): Promise<void> {
    const request = currentRequest();
    if (!request) return;
    setBusy("build");
    setError("");
    setAdvisorPreview(null);
    setAdvisorConfirmed(false);
    try {
      const response = await buildDecisionCompass(request);
      setCompass(response);
      setProgressAsOf(response.provenance.progress_as_of as string ?? progressAsOf);
      setStatus("Decision Compass построен без обращения к провайдеру.");
    } catch (caught) {
      setError(safeError(caught, "Не удалось построить Decision Compass."));
      setStatus("Decision Compass не построен.");
    } finally {
      setBusy(null);
    }
  }

  async function previewAdvisor(): Promise<void> {
    const request = currentRequest();
    if (!request || !compass) {
      if (!compass) setError("Сначала построй provider-free Compass.");
      return;
    }
    setBusy("advisor-preview");
    setError("");
    try {
      const response = await previewDecisionCompassAdvisor(request);
      setAdvisorPreview(response);
      setAdvisorConfirmed(false);
      setStatus("Предпросмотр готов. Совет ещё не вызывался.");
    } catch (caught) {
      setError(safeError(caught, "Не удалось подготовить независимую рекомендацию."));
    } finally {
      setBusy(null);
    }
  }

  async function executeAdvisor(): Promise<void> {
    const request = currentRequest();
    if (!request || !compass || !advisorPreview || !advisorConfirmed) return;
    setBusy("advisor-execute");
    setError("");
    try {
      const response = await executeDecisionCompassAdvisor(request, advisorPreview);
      setCompass(response);
      setStatus("Независимая рекомендация добавлена отдельной веткой; остальные ветки сохранены.");
    } catch (caught) {
      setError(safeError(caught, "Независимая рекомендация недоступна."));
    } finally {
      setBusy(null);
    }
  }

  function renderSimulate(branch: DecisionCompassSimulateBranch): ReactElement {
    const option = branch.result?.selected_option;
    return (
      <section className="decision-compass-panel decision-compass-branch" aria-labelledby="decision-compass-simulate-title">
        <div className="decision-compass-panel-heading"><span>04</span><div><h4 id="decision-compass-simulate-title">Вероятный выбор</h4><p>Simulate Me показывает прогноз вероятного выбора, а не рекомендацию.</p></div></div>
        <p className="decision-compass-state">{stateLabel(branch.state, SIMULATE_STATE_LABELS, "Вероятный выбор не определён")}</p>
        {option?.id ? <p className="decision-compass-emphasis">Exact ID вероятного выбора: <code>{option.id}</code></p> : null}
        {branch.state === "error" ? <p className="decision-compass-branch-error" role="status">{branchError(branch)}</p> : null}
        {branch.state === "abstention" ? <p className="decision-compass-muted">Данных недостаточно для безопасного прогноза.</p> : null}
      </section>
    );
  }

  function renderBehavior(branch: DecisionCompassBehaviorBranch): ReactElement {
    const selected = branch.pattern?.selected_option;
    return (
      <section className="decision-compass-panel decision-compass-branch" aria-labelledby="decision-compass-behavior-title">
        <div className="decision-compass-panel-heading"><span>05</span><div><h4 id="decision-compass-behavior-title">Наблюдаемое поведение</h4><p>Показывается только выбранный владельцем exact-контекст; автосопоставления нет.</p></div></div>
        <p className="decision-compass-state">{stateLabel(branch.state, BEHAVIOR_STATE_LABELS, "Состояние не определено")}</p>
        {selected ? <dl className="decision-compass-facts"><div><dt>Индекс варианта</dt><dd>{selected.option_index}</dd></div><div><dt>Отпечаток варианта</dt><dd><code>{selected.option_fingerprint}</code></dd></div></dl> : null}
        {branch.state === "error" ? <p className="decision-compass-branch-error" role="status">{branchError(branch)}</p> : null}
      </section>
    );
  }

  function renderCompassResult(result: DecisionCompassResponse): ReactElement {
    const relation = result.growth_progress.growth_result.goal_results[0];
    const progress = result.growth_progress.goal_progress_result;
    const advisorLines = advisorResultLines(result.advisor);
    return (
      <div className="decision-compass-result-list">
        {renderSimulate(result.simulate_me)}
        {renderBehavior(result.behavioral)}
        <section className="decision-compass-panel decision-compass-branch" aria-labelledby="decision-compass-growth-title">
          <div className="decision-compass-panel-heading"><span>06</span><div><h4 id="decision-compass-growth-title">Связь поведения с целью</h4><p>Это существующая reviewed-связь Growth, не оценка варианта и не причинный вывод.</p></div></div>
          <p className="decision-compass-state">{stateLabel(relation?.state ?? "", RELATION_COPY, "Связь не определена")}</p>
          <p className="decision-compass-muted">{RELATION_EXPLANATION[relation?.state ?? ""] ?? "Связь остаётся описательной и ограниченной текущей политикой."}</p>
          {relation?.cohort_fingerprint ? <p className="decision-compass-technical-line">Exact-контекст: <code>{relation.cohort_fingerprint}</code></p> : null}
        </section>
        <section className="decision-compass-panel decision-compass-branch" aria-labelledby="decision-compass-progress-title">
          <div className="decision-compass-panel-heading"><span>07</span><div><h4 id="decision-compass-progress-title">Измеряемый прогресс</h4><p>Stage 12 измеряет состояние выбранной цели на указанную дату, а не эффективность варианта.</p></div></div>
          <p className="decision-compass-state">{stateLabel(progress.status, PROGRESS_STATE_LABELS, "Состояние прогресса не определено")}</p>
          <p className="decision-compass-muted">Дата среза: {safeText(progress.as_of)}</p>
        </section>
        <section className="decision-compass-panel decision-compass-branch decision-compass-advisor" aria-labelledby="decision-compass-advisor-title">
          <div className="decision-compass-panel-heading"><span>08</span><div><h4 id="decision-compass-advisor-title">Независимая рекомендация</h4><p>Отдельная ветка появляется только после явного предпросмотра и подтверждения владельца.</p></div></div>
          <p className="decision-compass-state">{ADVISOR_STATE_LABELS[result.advisor.state] ?? "Состояние рекомендации не определено"}</p>
          {advisorLines.length ? <ul className="decision-compass-copy-list">{advisorLines.map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}</ul> : null}
          {result.advisor.state === "error" ? <p className="decision-compass-branch-error" role="status">{branchError(result.advisor)}</p> : null}
          {result.advisor.state === "not_requested" ? (
            <div className="decision-compass-advisor-action">
              <button className="decision-compass-button decision-compass-button-secondary" type="button" disabled={busy !== null} aria-busy={busy === "advisor-preview"} onClick={() => void previewAdvisor()}>
                {busy === "advisor-preview" ? "Готовлю предпросмотр…" : "Получить независимую рекомендацию"}
              </button>
              {advisorPreview ? (
                <div className="decision-compass-preview" aria-labelledby="decision-compass-preview-title">
                  <h5 id="decision-compass-preview-title">Проверка перед отдельным запросом</h5>
                  <p>{advisorPreview.goal_text}</p>
                  <label className="decision-compass-confirm"><input type="checkbox" checked={advisorConfirmed} onChange={(event) => setAdvisorConfirmed(event.target.checked)} /> Подтверждаю явный запрос независимой рекомендации по этой точной цели.</label>
                  <button className="decision-compass-button decision-compass-button-secondary" type="button" disabled={!advisorConfirmed || busy !== null} aria-busy={busy === "advisor-execute"} onClick={() => void executeAdvisor()}>
                    {busy === "advisor-execute" ? "Запрашиваю…" : "Подтвердить запрос"}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
        <section className="decision-compass-panel decision-compass-relations" aria-labelledby="decision-compass-relations-title">
          <div className="decision-compass-panel-heading"><span>09</span><div><h4 id="decision-compass-relations-title">Структурные связи</h4><p>Только механические exact-ID отношения между ветками; каждая ветка остаётся самостоятельной.</p></div></div>
          <ul className="decision-compass-copy-list">{result.structural_relations.map((item, index) => <li key={`${item.code}-${index}`}>{RELATION_LABELS[item.code] ?? "Связь не определена безопасной политикой."}</li>)}</ul>
        </section>
        <details className="decision-compass-technical" aria-label="Техническая информация Decision Compass">
          <summary>Технические сведения и происхождение данных</summary>
          <dl className="decision-compass-facts decision-compass-facts-technical"><div><dt>Версия производного результата</dt><dd>{safeText(result.derivation_version)}</dd></div><div><dt>Отпечаток политики</dt><dd><code>{safeText(result.policy_fingerprint)}</code></dd></div><div><dt>Источник цели</dt><dd><code>{result.selected_goal.source_uuid}</code></dd></div><div><dt>Свежесть цели</dt><dd><code>{result.selected_goal.identity_fingerprint}</code></dd></div><div><dt>Провайдер / запись</dt><dd>{safeText(result.provenance.provider)} / {safeText(result.provenance.write)}</dd></div></dl>
        </details>
      </div>
    );
  }

  return (
    <section id="decision-compass" className="decision-compass-surface" aria-labelledby="decision-compass-title" aria-busy={busy !== null} data-decision-compass-surface>
      <div className="decision-compass-heading">
        <div><p className="decision-compass-eyebrow">Cognitive Twin v3 · Stage 13C</p><h3 id="decision-compass-title">Компас решения</h3><p>Собери одну точную цель, свои варианты и контекст. Каждая ветка останется самостоятельной и проверяемой.</p></div>
        <div className="decision-compass-heading-meta"><span>только для владельца</span><span>без сохранения · явный запрос</span></div>
      </div>
      <div className="decision-compass-toolbar">
        <button className="decision-compass-button decision-compass-button-primary" type="button" disabled={busy !== null} aria-busy={busy === "goals"} onClick={() => void refreshGoals()}>{busy === "goals" ? "Загружаю цели…" : "Загрузить текущие цели"}</button>
        <p className="decision-compass-status" role="status" aria-live="polite">{status}</p>
      </div>
      <ErrorMessage message={error} />
      {!goals ? <p className="decision-compass-empty">Нажми «Загрузить текущие цели», чтобы явно получить список целей с сервера. Сам раздел не выполняет запросы при открытии.</p> : null}
      <section className="decision-compass-panel" aria-labelledby="decision-compass-goal-title">
        <div className="decision-compass-panel-heading"><span>01</span><div><h4 id="decision-compass-goal-title">Точная текущая цель</h4><p>Выбор принадлежит владельцу. Текст не используется для поиска или переназначения.</p></div></div>
        <label className="decision-compass-label" htmlFor="decision-compass-goal-select">Выбери одну цель явно</label>
        <select className="decision-compass-input" id="decision-compass-goal-select" value={selectedGoalUuid} disabled={!goals || busy !== null} onChange={(event) => chooseGoal(event.target.value)}>
          <option value="">Не выбрана</option>
          {(goals?.goals ?? []).map((item) => <option value={item.goal.source_note_uuid} key={item.goal.source_note_uuid}>{item.goal_text}</option>)}
        </select>
        {selectedGoal ? <div className="decision-compass-goal-preview"><p>{selectedGoal.goal_text}</p><dl className="decision-compass-facts"><div><dt>Источник UUID</dt><dd><code>{selectedGoal.goal.source_note_uuid}</code></dd></div><div><dt>Отпечаток личности цели</dt><dd><code>{selectedGoal.goal_identity_fingerprint}</code></dd></div></dl></div> : <p className="decision-compass-muted">После выбора цель ещё не читается повторно; нажми отдельную кнопку построения.</p>}
      </section>
      <section className="decision-compass-panel" aria-labelledby="decision-compass-request-title">
        <div className="decision-compass-panel-heading"><span>02</span><div><h4 id="decision-compass-request-title">Задача, варианты и критерии владельца</h4><p>Варианты принадлежат только этому запросу. Критерии справочные: у них нет весов, ранжирования или скрытого счёта.</p></div></div>
        <label className="decision-compass-label" htmlFor="decision-compass-task">Задача</label>
        <textarea className="decision-compass-input decision-compass-textarea" id="decision-compass-task" value={task} disabled={busy !== null} placeholder="Например: выбрать следующий шаг на этой неделе" onChange={(event) => { setTask(event.target.value); resetDerivedState(); }} />
        <div className="decision-compass-editor-heading"><h5>Варианты ({options.length}/{MAX_OPTIONS})</h5><button className="decision-compass-button decision-compass-button-quiet" type="button" disabled={options.length >= MAX_OPTIONS || busy !== null} onClick={addOption}>Добавить вариант</button></div>
        <div className="decision-compass-option-list">{options.map((option, index) => <div className="decision-compass-option-row" key={option.id}><label className="decision-compass-label" htmlFor={`decision-compass-option-${index}`}>Вариант {index + 1}<input className="decision-compass-input" id={`decision-compass-option-${index}`} value={option.label} disabled={busy !== null} onChange={(event) => updateOption(index, event.target.value)} /></label>{options.length > 1 ? <button className="decision-compass-button decision-compass-button-quiet" type="button" disabled={busy !== null} onClick={() => removeOption(index)}>Удалить</button> : null}</div>)}</div>
        <div className="decision-compass-editor-heading"><h5>Критерии ({criteria.length}/{MAX_CRITERIA})</h5><button className="decision-compass-button decision-compass-button-quiet" type="button" disabled={criteria.length >= MAX_CRITERIA || busy !== null} onClick={addCriterion}>Добавить критерий</button></div>
        {criteria.length ? <div className="decision-compass-criterion-list">{criteria.map((criterion, index) => <div className="decision-compass-criterion-row" key={criterion.id}><label className="decision-compass-label" htmlFor={`decision-compass-criterion-${index}`}>Название<input className="decision-compass-input" id={`decision-compass-criterion-${index}`} value={criterion.label} disabled={busy !== null} onChange={(event) => updateCriterion(index, "label", event.target.value)} /></label><label className="decision-compass-label" htmlFor={`decision-compass-criterion-description-${index}`}>Описание, если нужно<input className="decision-compass-input" id={`decision-compass-criterion-description-${index}`} value={criterion.description ?? ""} disabled={busy !== null} onChange={(event) => updateCriterion(index, "description", event.target.value)} /></label><button className="decision-compass-button decision-compass-button-quiet" type="button" disabled={busy !== null} onClick={() => removeCriterion(index)}>Удалить</button></div>)}</div> : <p className="decision-compass-muted">Критерии не обязательны и не превращаются в оценку.</p>}
      </section>
      <section className="decision-compass-panel" aria-labelledby="decision-compass-behavior-input-title">
        <div className="decision-compass-panel-heading"><span>03</span><div><h4 id="decision-compass-behavior-input-title">Необязательный наблюдаемый контекст</h4><p>Загрузи контексты отдельным действием и выбери exact fingerprint вручную. Даже единственный контекст не выбирается автоматически.</p></div></div>
        <button className="decision-compass-button decision-compass-button-secondary" type="button" disabled={busy !== null} aria-busy={busy === "behavior"} onClick={() => void loadBehaviorContexts()}>{busy === "behavior" ? "Загружаю контексты…" : "Показать доступные контексты"}</button>
        <label className="decision-compass-label" htmlFor="decision-compass-cohort">Exact fingerprint контекста</label>
        <input className="decision-compass-input" id="decision-compass-cohort" value={scopeFingerprint} disabled={busy !== null} placeholder="Не выбирать автоматически" onChange={(event) => chooseScope(event.target.value)} />
        {cohortPatterns.length ? <label className="decision-compass-label" htmlFor="decision-compass-cohort-select">Или выбери один загруженный exact-контекст<select className="decision-compass-input" id="decision-compass-cohort-select" value={scopeFingerprint} disabled={busy !== null} onChange={(event) => chooseScope(event.target.value)}><option value="">Не выбран</option>{cohortPatterns.map((pattern, index) => <option value={pattern.cohort?.cohort_fingerprint} key={pattern.cohort?.cohort_fingerprint}>Контекст {index + 1} · {safeText(pattern.cohort?.domain, "домен не указан")}</option>)}</select></label> : null}
        {selectedPattern?.selected_option ? <div className="decision-compass-binding"><p className="decision-compass-muted">Доступный наблюдаемый вариант показывается только как exact identity: индекс {selectedPattern.selected_option.option_index}, отпечаток <code>{selectedPattern.selected_option.option_fingerprint}</code>.</p><label className="decision-compass-label" htmlFor="decision-compass-binding-option">Связать с вариантом этого запроса<select className="decision-compass-input" id="decision-compass-binding-option" value={bindingOptionId} disabled={busy !== null} onChange={(event) => setBindingOptionId(event.target.value)}><option value="">Не связывать</option>{options.filter((option) => option.label.trim()).map((option) => <option value={option.id} key={option.id}>{option.id}</option>)}</select></label><button className="decision-compass-button decision-compass-button-quiet" type="button" disabled={!bindingOptionId || busy !== null} onClick={bindBehaviorOption}>{binding ? "Обновить exact-связку" : "Задать exact-связку"}</button></div> : null}
        {binding ? <p className="decision-compass-inline-success" role="status">Exact-связка задана для ID <code>{binding.request_option_id}</code>. Равенство подписей не используется.</p> : null}
      </section>
      <div className="decision-compass-build-row"><button className="decision-compass-button decision-compass-button-primary" type="button" disabled={busy !== null || !selectedGoalUuid} aria-busy={busy === "build"} onClick={() => void buildCompass()}>{busy === "build" ? "Строю Compass…" : "Смоделировать меня"}</button><label className="decision-compass-date-label" htmlFor="decision-compass-as-of">Срез прогресса<input className="decision-compass-input" id="decision-compass-as-of" type="datetime-local" value={progressAsOf.slice(0, 16)} disabled={busy !== null} onChange={(event) => setProgressAsOf(event.target.value ? new Date(event.target.value).toISOString() : nowIso())} /></label></div>
      {compass ? <section className="decision-compass-result" aria-labelledby="decision-compass-result-title"><div className="decision-compass-result-heading"><div><p className="decision-compass-eyebrow">Проверенный срез</p><h4 id="decision-compass-result-title">Результаты по выбранной цели</h4></div><p>Собрано: {safeText(compass.provenance.progress_as_of)}</p></div><p className="decision-compass-freshness">Цель перепроверена по exact UUID и identity fingerprint перед выдачей результата.</p>{renderCompassResult(compass)}</section> : null}
    </section>
  );
}
