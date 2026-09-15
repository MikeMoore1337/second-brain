import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  activateAdaptiveCandidate,
  buildAdaptiveCandidate,
  evaluateAdaptiveProfile,
  loadAdaptiveState,
  newAdaptiveOperationId,
  rejectAdaptiveCandidate,
  revertAdaptiveProfile,
  reviewAdaptiveCandidate,
  supersedeAdaptiveProfile,
  type AdaptiveCandidate,
  type AdaptiveEvaluation,
  type AdaptiveEvaluateRequest,
  type AdaptiveExperimentSelector,
  type AdaptiveGoal,
  type AdaptiveMutationResponse,
  type AdaptiveProfile,
  type AdaptiveProjection,
  type AdaptiveRevertRequest,
  type AdaptiveSelectionRequest,
  type AdaptiveSourceReadiness,
  type AdaptiveStateResponse,
  type AdaptiveSupersedeRequest,
} from "./adaptive-cognitive-twin-api";
import { Icon } from "./icons";
import { presentError } from "./presentation";

type BusyOperation = "load" | "candidate" | "review" | "activate" | "reject" | "evaluate" | "supersede" | "revert" | null;

const READINESS_LABELS: Readonly<Record<string, string>> = {
  stage9_calibration: "Связь с ретроспективной проверкой",
  stage10_behavioral: "Наблюдаемый контекст выбора",
  stage12_progress: "Точное правило прогресса",
  stage14_experiment: "Результат личного эксперимента",
};

const READINESS_COPY: Readonly<Record<string, string>> = {
  exact_current: "Источник доступен и совпадает с выбранным срезом.",
  source_missing: "Точный источник не найден.",
  unavailable: "Источник сейчас недоступен.",
  stale: "Источник устарел относительно текущего состояния.",
  source_changed: "Источник изменился после проверки.",
  not_comparable: "Источник нельзя безопасно сопоставить.",
  policy_mismatch: "Версия политики источника не совпадает.",
};

const CANDIDATE_LABELS: Readonly<Record<string, string>> = {
  candidate: "Предложение готово к проверке",
  hold: "Изменение не предложено",
  insufficient: "Недостаточно точных данных",
  not_comparable: "Источники нельзя сопоставить",
  source_changed: "Источник изменился",
  policy_mismatch: "Нужна проверка политики",
};

const CANDIDATE_COPY: Readonly<Record<string, string>> = {
  candidate: "Система собрала ограниченное предложение из точных источников. Оно ещё не меняет текущий профиль.",
  hold: "Точные источники не дают безопасного изменения. Текущий профиль остаётся без изменений.",
  insufficient: "Предложение не строится, пока не появится полный проверенный набор источников.",
  not_comparable: "Предложение остановлено: выбранные источники нельзя безопасно сопоставить.",
  source_changed: "Предложение устарело. Сначала собери новый срез источников.",
  policy_mismatch: "Предложение остановлено до проверки версии политики.",
};

const PROFILE_FOCUS_LABELS: Readonly<Record<string, string>> = {
  hold_current_profile: "Сохранить текущий фокус",
  progress_context: "Сначала показать прогресс",
  behavioral_context: "Сначала показать наблюдаемый контекст",
  tradeoff_context: "Сначала показать компромиссы",
  experiment_context: "Сначала показать результат эксперимента",
  calibration_context: "Сначала показать ретроспективную проверку",
};

const INTERACTION_LABELS: Readonly<Record<string, string>> = {
  balanced_evidence: "Сбалансированная последовательность свидетельств",
  evidence_sequence: "Последовательность проверенных свидетельств",
  explicit_tradeoff: "Явное сравнение компромиссов",
  foreground_review: "Проверка перед следующим действием",
};

const MEASURE_LABELS: Readonly<Record<string, string>> = {
  progress_state: "Состояние прогресса",
  behavioral_state: "Наблюдаемое состояние выбора",
  experiment_state: "Состояние эксперимента",
  calibration_linkage: "Связь с ретроспективной проверкой",
};

const REASON_LABELS: Readonly<Record<string, string>> = {
  stage9_calibration_available: "Есть точная ретроспективная связка.",
  stage9_calibration_not_evaluable: "Ретроспективная связка пока не оценивается.",
  stage10_supports_goal: "Наблюдаемый контекст поддерживает выбранный фокус.",
  stage10_conflicts_with_goal: "Наблюдаемый контекст расходится с выбранным фокусом.",
  stage10_state_not_comparable: "Наблюдаемый контекст нельзя сопоставить.",
  stage12_target_met: "Правило прогресса показывает достигнутую цель.",
  stage12_toward_target: "Правило прогресса показывает движение к цели.",
  stage12_away_from_target: "Правило прогресса показывает движение от цели.",
  stage12_state_not_comparable: "Состояние прогресса нельзя сопоставить.",
  stage14_terminal_review_available: "Есть завершённый проверенный эксперимент.",
  stage14_result_not_comparable: "Результат эксперимента нельзя сопоставить.",
  stage14_owner_hold: "Владелец оставил результат эксперимента на паузе.",
  sources_agree_on_focus: "Точные источники сходятся на одном фокусе.",
  sources_conflict: "Точные источники расходятся; автоматического выбора нет.",
  no_safe_delta: "Безопасного изменения текущего профиля не найдено.",
  missing_exact_source: "Не хватает точного проверенного источника.",
  source_drift: "Один из источников изменился.",
  policy_mismatch: "Версия политики не совпадает.",
};

const LIFECYCLE_LABELS: Readonly<Record<string, string>> = {
  no_active_profile: "Активного профиля пока нет",
  candidate_pending_review: "Предложение ждёт проверки",
  active_profile_valid: "Текущий профиль подтверждён источниками",
  active_profile_stale: "Профиль требует нового среза",
};

const EVALUATION_LABELS: Readonly<Record<string, string>> = {
  evaluated: "Описательное сравнение готово",
  insufficient: "Недостаточно данных для сравнения",
  not_comparable: "Срезы нельзя сопоставить",
  source_changed: "Источник изменился",
  policy_mismatch: "Нужна проверка политики",
};

const SOURCE_FAMILY_KEYS = new Set([
  "stage9_calibration",
  "stage10_behavioral",
  "stage12_progress",
  "stage14_experiment",
]);

function isAbortError(error: unknown): boolean {
  return (error instanceof DOMException && error.name === "AbortError")
    || (typeof error === "object" && error !== null && "name" in error && error.name === "AbortError");
}

function safeLabel(value: string | undefined, labels: Readonly<Record<string, string>>, fallback: string): string {
  return value ? labels[value] ?? fallback : fallback;
}

function formatMoment(value: string | null | undefined): string {
  if (!value) return "время не указано";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function shorten(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function stringList(value: unknown): readonly string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function selectedKey(selector: AdaptiveExperimentSelector): string {
  return `${selector.experiment_definition_id}:${selector.experiment_definition_fingerprint}`;
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="adaptive-error" role="alert" tabIndex={-1} ref={ref}>{message}</p> : null;
}

function StateBadge({ value, labels, fallback, tone = "neutral" }: {
  readonly value: string;
  readonly labels: Readonly<Record<string, string>>;
  readonly fallback: string;
  readonly tone?: "neutral" | "accent" | "danger";
}): ReactElement {
  return <span className={`adaptive-badge adaptive-badge-${tone}`} data-state={value}>{safeLabel(value, labels, fallback)}</span>;
}

function ReadinessList({ items }: { readonly items: readonly AdaptiveSourceReadiness[] }): ReactElement {
  return (
    <section className="adaptive-section adaptive-readiness" aria-labelledby="adaptive-readiness-title">
      <div className="adaptive-section-heading">
        <Icon name="relation" size={28} aria-hidden="true" />
        <div>
          <h4 id="adaptive-readiness-title">Точность источников</h4>
          <p>Перед предложением каждый источник проверяется отдельно.</p>
        </div>
      </div>
      <ul className="adaptive-readiness-list">
        {items.filter((item) => SOURCE_FAMILY_KEYS.has(item.family)).map((item) => {
          const exact = item.readiness === "exact_current";
          return (
            <li className={`adaptive-readiness-item${exact ? " is-exact" : " is-blocked"}`} key={item.family}>
              <Icon name={exact ? "success" : "warning"} size={22} aria-hidden="true" />
              <span className="adaptive-readiness-copy">
                <strong>{READINESS_LABELS[item.family] ?? "Проверяемый источник"}</strong>
                <span>{READINESS_COPY[item.readiness] ?? "Источник требует проверки."}</span>
              </span>
              <span className="adaptive-readiness-state">{exact ? "Точно" : "Остановлено"}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ProfileSummary({ profile, title }: { readonly profile: AdaptiveProfile | null; readonly title: string }): ReactElement {
  if (!profile) {
    return <section className="adaptive-section adaptive-empty-profile" aria-labelledby={`${title}-title`}><h4 id={`${title}-title`}>{title}</h4><p>Активного профиля нет. Предложение станет профилем только после проверки и явного подтверждения.</p></section>;
  }
  return (
    <section className="adaptive-section adaptive-profile-summary" aria-labelledby={`${title}-title`}>
      <div className="adaptive-section-heading">
        <Icon name="copy" size={28} aria-hidden="true" />
        <div><h4 id={`${title}-title`}>{title}</h4><p>{safeLabel(profile.projection_focus, PROFILE_FOCUS_LABELS, "Фокус требует уточнения.")}</p></div>
      </div>
      <dl className="adaptive-definition-grid">
        <div><dt>Порядок показа</dt><dd>{safeLabel(profile.interaction_mode, INTERACTION_LABELS, "Порядок требует уточнения.")}</dd></div>
        <div><dt>Правило сравнения</dt><dd>{safeLabel(profile.evaluation_measure, MEASURE_LABELS, "Правило требует уточнения.")}</dd></div>
      </dl>
    </section>
  );
}

function CandidateSection({ candidate }: { readonly candidate: AdaptiveCandidate }): ReactElement {
  const proposal = candidate.proposed_profile;
  const candidateTone = candidate.status === "candidate" ? "accent" : candidate.status === "hold" ? "neutral" : "danger";
  return (
    <section className="adaptive-section adaptive-candidate-section" aria-labelledby="adaptive-candidate-title">
      <div className="adaptive-section-heading">
        <Icon name={candidate.status === "candidate" ? "success" : "warning"} size={28} aria-hidden="true" />
        <div>
          <h4 id="adaptive-candidate-title">Предложение изменения</h4>
          <p>{CANDIDATE_COPY[candidate.status] ?? "Предложение требует безопасной проверки."}</p>
        </div>
        <StateBadge value={candidate.status} labels={CANDIDATE_LABELS} fallback="Состояние требует уточнения." tone={candidateTone} />
      </div>
      {proposal ? (
        <dl className="adaptive-definition-grid adaptive-proposal-grid">
          <div><dt>Новый фокус</dt><dd>{safeLabel(proposal.projection_focus, PROFILE_FOCUS_LABELS, "Фокус требует уточнения.")}</dd></div>
          <div><dt>Новый порядок показа</dt><dd>{safeLabel(proposal.interaction_mode, INTERACTION_LABELS, "Порядок требует уточнения.")}</dd></div>
          <div><dt>Новое правило сравнения</dt><dd>{safeLabel(proposal.evaluation_measure, MEASURE_LABELS, "Правило требует уточнения.")}</dd></div>
        </dl>
      ) : null}
      {candidate.reasons.length > 0 ? (
        <ul className="adaptive-reason-list" aria-label="Почему сформировалось это состояние">
          {candidate.reasons.map((reason) => <li key={reason}><Icon name="info" size={18} aria-hidden="true" /><span>{REASON_LABELS[reason] ?? "Причина требует уточнения."}</span></li>)}
        </ul>
      ) : null}
      <p className="adaptive-caveat">{candidate.caveats.includes("descriptive_non_causal") ? "Изменение в выбранном периоде не доказывает, что его вызвал профиль." : "Все изменения остаются описательными и требуют проверки владельца."}</p>
    </section>
  );
}

function EvaluationSection({ evaluation, phrase }: { readonly evaluation: AdaptiveEvaluation | null; readonly phrase: string }): ReactElement {
  return (
    <section className="adaptive-section adaptive-evaluation-section" aria-labelledby="adaptive-evaluation-title">
      <div className="adaptive-section-heading">
        <Icon name="timeline" size={28} aria-hidden="true" />
        <div><h4 id="adaptive-evaluation-title">Описательное сравнение</h4><p>Сравнение строится только по новому точному срезу и не доказывает причинность.</p></div>
      </div>
      <p className="adaptive-causality-note">{phrase}</p>
      {evaluation ? (
        <div className="adaptive-evaluation-result" role="status">
          <StateBadge value={evaluation.state} labels={EVALUATION_LABELS} fallback="Состояние сравнения требует уточнения." tone={evaluation.state === "evaluated" ? "accent" : "danger"} />
          <dl className="adaptive-definition-grid">
            <div><dt>Срез сравнения</dt><dd>{formatMoment(evaluation.as_of)}</dd></div>
            <div><dt>Изменившаяся часть</dt><dd>{stringList(evaluation.changed_sources).length > 0 ? stringList(evaluation.changed_sources).map((item) => READINESS_LABELS[item] ?? "Общий срез").join(", ") : "Изменений не отмечено"}</dd></div>
          </dl>
          <ul className="adaptive-caveat-list">{evaluation.caveats.map((caveat) => <li key={caveat}>{caveat === "descriptive_non_causal" ? "Сравнение остаётся описательным." : "Сравнение требует осторожного прочтения."}</li>)}</ul>
          <details className="adaptive-technical-disclosure"><summary>Показать отпечаток сравнения</summary><dl><div><dt>Идентификатор профиля</dt><dd><code>{evaluation.profile_id}</code></dd></div><div><dt>Отпечаток результата</dt><dd><code>{evaluation.evaluation_fingerprint}</code></dd></div><div><dt>Поздний источник</dt><dd><code>{evaluation.later_snapshot_fingerprint}</code></dd></div></dl></details>
        </div>
      ) : <p className="adaptive-empty-copy">Сравнение появится после явного действия владельца.</p>}
    </section>
  );
}

function TechnicalDisclosure({ projection }: { readonly projection: AdaptiveProjection }): ReactElement {
  const active = projection.active_profile;
  return (
    <details className="adaptive-technical-disclosure adaptive-state-disclosure">
      <summary>Показать точные идентификаторы</summary>
      <dl>
        <div><dt>Срез источников</dt><dd><code>{projection.source_snapshot_fingerprint}</code></dd></div>
        <div><dt>Идентификатор цели</dt><dd><code>{projection.goal_source_uuid}</code></dd></div>
        <div><dt>Отпечаток цели</dt><dd><code>{projection.goal_identity_fingerprint}</code></dd></div>
        <div><dt>Отпечаток предложения</dt><dd><code>{projection.candidate.candidate_fingerprint}</code></dd></div>
        {active ? <div><dt>Активный профиль</dt><dd><code>{active.profile_id} · {active.profile_fingerprint}</code></dd></div> : null}
        <div><dt>Построено</dt><dd>{formatMoment(projection.as_of)}</dd></div>
      </dl>
    </details>
  );
}

function ProfileHistory({
  history,
  active,
  disabled,
  onRevert,
}: {
  readonly history: readonly AdaptiveProfile[];
  readonly active: AdaptiveProfile | null;
  readonly disabled: boolean;
  readonly onRevert: (profile: AdaptiveProfile) => void;
}): ReactElement | null {
  const previous = history.filter((profile) => profile.profile_id !== active?.profile_id);
  if (previous.length === 0) return null;
  return (
    <section className="adaptive-section adaptive-history" aria-labelledby="adaptive-history-title">
      <div className="adaptive-section-heading"><Icon name="refresh" size={28} aria-hidden="true" /><div><h4 id="adaptive-history-title">Предыдущие версии</h4><p>Вернуть можно только точную сохранённую версию после повторного подтверждения.</p></div></div>
      <ul className="adaptive-history-list">
        {previous.map((profile) => (
          <li key={profile.profile_id}>
            <div><strong>{safeLabel(profile.projection_focus, PROFILE_FOCUS_LABELS, "Фокус требует уточнения.")}</strong><span>{safeLabel(profile.interaction_mode, INTERACTION_LABELS, "Порядок требует уточнения.")}</span></div>
            <button type="button" className="adaptive-button adaptive-button-secondary" disabled={disabled} onClick={() => onRevert(profile)}>Вернуть эту версию</button>
            <details className="adaptive-history-details"><summary>Идентификаторы версии</summary><code>{profile.profile_id} · {profile.profile_fingerprint}</code></details>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function AdaptiveCognitiveTwinSurface(): ReactElement {
  const [state, setState] = useState<AdaptiveStateResponse | null>(null);
  const [projection, setProjection] = useState<AdaptiveProjection | null>(null);
  const [evaluation, setEvaluation] = useState<AdaptiveEvaluation | null>(null);
  const [selectedGoalId, setSelectedGoalId] = useState("");
  const [selectedExperiment, setSelectedExperiment] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [reviewedCandidateFingerprint, setReviewedCandidateFingerprint] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyOperation>(null);
  const [status, setStatus] = useState("Раздел не обращается к данным, пока владелец не нажмёт кнопку загрузки.");
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const selectedGoal: AdaptiveGoal | null = state?.goals.find((goal) => goal.source_note_uuid === selectedGoalId) ?? null;
  const selectors = state?.stage14_experiment_selectors ?? [];
  const selector = selectors.find((item) => selectedKey(item) === selectedExperiment);
  const shownProjection = projection ?? state?.projection ?? null;
  const activeProfile = shownProjection?.active_profile ?? null;
  const candidate = shownProjection?.candidate ?? null;
  const candidateReviewed = candidate !== null && (
    reviewedCandidateFingerprint === candidate.candidate_fingerprint
    || state?.reviewed_candidate_fingerprints.includes(candidate.candidate_fingerprint) === true
  );
  const nonCausalPhrase = state?.non_causal_phrase ?? "Наблюдаемое изменение в выбранном периоде не является доказательством того, что профиль вызвал это изменение.";

  const selectionForRequest = (): AdaptiveSelectionRequest | null => {
    if (!selectedGoal) return null;
    return {
      goal_source_uuid: selectedGoal.source_note_uuid,
      goal_identity_fingerprint: selectedGoal.goal_identity_fingerprint,
      as_of: new Date().toISOString(),
      ...(selector ? {
        stage14_experiment_definition_id: selector.experiment_definition_id,
        stage14_experiment_definition_fingerprint: selector.experiment_definition_fingerprint,
      } : {}),
    };
  };

  const run = async (operation: Exclude<BusyOperation, null>, action: (signal: AbortSignal) => Promise<void>): Promise<void> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(operation);
    setError("");
    try {
      await action(controller.signal);
    } catch (caught) {
      if (!isAbortError(caught)) setError(presentError(caught, "Операция адаптивного профиля не выполнена."));
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setBusy(null);
      }
    }
  };

  const loadDiscovery = (): void => {
    void run("load", async (signal) => {
      const loaded = await loadAdaptiveState({}, undefined, signal);
      setState(loaded);
      setProjection(null);
      setEvaluation(null);
      setSelectedGoalId((current) => loaded.goals.some((goal) => goal.source_note_uuid === current) ? current : loaded.goals[0]?.source_note_uuid ?? "");
      setSelectedExperiment("");
      setConfirmed(false);
      setReviewedCandidateFingerprint(null);
      setStatus(loaded.goals.length > 0 ? "Цели загружены. Выбери одну цель и запроси её точный срез." : "Точных текущих целей пока нет.");
    });
  };

  const loadSelectedState = (): void => {
    const selection = selectionForRequest();
    if (!selection) return;
    void run("load", async (signal) => {
      const loaded = await loadAdaptiveState(selection, undefined, signal);
      setState(loaded);
      setProjection(loaded.projection);
      setEvaluation(null);
      setConfirmed(false);
      setReviewedCandidateFingerprint(null);
      setStatus(loaded.projection ? "Точный срез цели загружен; предложение ещё не собрано." : "Точная цель загружена; теперь можно проверить источники.");
    });
  };

  const buildCandidateAction = (): void => {
    const selection = selectionForRequest();
    if (!selection) return;
    void run("candidate", async (signal) => {
      const result = await buildAdaptiveCandidate(selection, undefined, signal);
      setProjection(result.projection);
      setEvaluation(null);
      setConfirmed(false);
      setReviewedCandidateFingerprint(null);
      setStatus(result.projection.candidate.status === "candidate" ? "Предложение собрано; проверь изменения и подтверди действие." : "Предложение собрано, но безопасного изменения пока нет.");
    });
  };

  const refreshAfterMutation = async (result: AdaptiveMutationResponse, message: string, selection: AdaptiveSelectionRequest, signal: AbortSignal): Promise<void> => {
    setProjection(result.projection);
    if (result.evaluation) setEvaluation(result.evaluation);
    setStatus(message);
    try {
      const loaded = await loadAdaptiveState(selection, undefined, signal);
      setState(loaded);
      setProjection(loaded.projection ?? result.projection);
      setReviewedCandidateFingerprint(
        loaded.projection && loaded.reviewed_candidate_fingerprints.includes(loaded.projection.candidate.candidate_fingerprint)
          ? loaded.projection.candidate.candidate_fingerprint
          : null,
      );
    } catch (caught) {
      if (!isAbortError(caught)) setStatus(`${message} Обнови состояние цели, чтобы увидеть историю версий.`);
    }
  };

  const operationPayload = (): {
    readonly selection: AdaptiveSelectionRequest;
    readonly candidate: AdaptiveCandidate;
  } | null => {
    const selection = selectionForRequest();
    if (!selection || !candidate) return null;
    if (candidate.status !== "candidate") return null;
    return { selection, candidate };
  };

  const review = (): void => {
    const payload = operationPayload();
    if (!payload || !confirmed) return;
    void run("review", async (signal) => {
      const result = await reviewAdaptiveCandidate({
        ...payload.selection,
        candidate_fingerprint: payload.candidate.candidate_fingerprint,
        source_snapshot_fingerprint: payload.candidate.source_snapshot_fingerprint,
        operation_id: newAdaptiveOperationId(),
        confirmed: true,
      }, undefined, signal);
      setReviewedCandidateFingerprint(payload.candidate.candidate_fingerprint);
      await refreshAfterMutation(result, "Предложение отмечено проверенным. Теперь можно явно активировать или отклонить его.", payload.selection, signal);
    });
  };

  const activate = (): void => {
    const payload = operationPayload();
    if (!payload || !confirmed || !candidateReviewed || activeProfile) return;
    void run("activate", async (signal) => {
      const result = await activateAdaptiveCandidate({
        ...payload.selection,
        candidate_fingerprint: payload.candidate.candidate_fingerprint,
        source_snapshot_fingerprint: payload.candidate.source_snapshot_fingerprint,
        operation_id: newAdaptiveOperationId(),
        confirmed: true,
      }, undefined, signal);
      await refreshAfterMutation(result, "Новый профиль активирован после явного подтверждения.", payload.selection, signal);
    });
  };

  const reject = (): void => {
    const payload = operationPayload();
    if (!payload || !confirmed || !candidateReviewed) return;
    void run("reject", async (signal) => {
      const result = await rejectAdaptiveCandidate({
        ...payload.selection,
        candidate_fingerprint: payload.candidate.candidate_fingerprint,
        source_snapshot_fingerprint: payload.candidate.source_snapshot_fingerprint,
        operation_id: newAdaptiveOperationId(),
        confirmed: true,
      }, undefined, signal);
      await refreshAfterMutation(result, "Предложение отклонено; текущий профиль не изменён.", payload.selection, signal);
    });
  };

  const supersede = (): void => {
    const payload = operationPayload();
    if (!payload || !confirmed || !candidateReviewed || !activeProfile || !payload.candidate.prior_profile_id || !payload.candidate.prior_profile_fingerprint) return;
    const request: AdaptiveSupersedeRequest = {
      ...payload.selection,
      candidate_fingerprint: payload.candidate.candidate_fingerprint,
      source_snapshot_fingerprint: payload.candidate.source_snapshot_fingerprint,
      prior_profile_id: payload.candidate.prior_profile_id,
      prior_profile_fingerprint: payload.candidate.prior_profile_fingerprint,
      operation_id: newAdaptiveOperationId(),
      confirmed: true,
    };
    void run("supersede", async (signal) => {
      const result = await supersedeAdaptiveProfile(request, undefined, signal);
      await refreshAfterMutation(result, "Профиль заменён после явного подтверждения.", payload.selection, signal);
    });
  };

  const evaluate = (): void => {
    const selection = selectionForRequest();
    if (!selection || !activeProfile || !confirmed) return;
    const request: AdaptiveEvaluateRequest = {
      ...selection,
      active_profile_id: activeProfile.profile_id,
      active_profile_fingerprint: activeProfile.profile_fingerprint,
      activation_source_snapshot_fingerprint: activeProfile.source_snapshot_fingerprint,
      operation_id: newAdaptiveOperationId(),
      confirmed: true,
    };
    void run("evaluate", async (signal) => {
      const result = await evaluateAdaptiveProfile(request, undefined, signal);
      await refreshAfterMutation(result, "Описательное сравнение построено; оно не меняет профиль.", selection, signal);
    });
  };

  const revert = (profile: AdaptiveProfile): void => {
    const selection = selectionForRequest();
    if (!selection || !confirmed) return;
    const request: AdaptiveRevertRequest = {
      ...selection,
      target_profile_id: profile.profile_id,
      target_profile_fingerprint: profile.profile_fingerprint,
      operation_id: newAdaptiveOperationId(),
      confirmed: true,
    };
    void run("revert", async (signal) => {
      const result = await revertAdaptiveProfile(request, undefined, signal);
      await refreshAfterMutation(result, "Предыдущая версия возвращена после явного подтверждения.", selection, signal);
    });
  };

  return (
    <section className="adaptive-cognitive-twin-surface" id="adaptive-cognitive-twin" aria-labelledby="adaptive-cognitive-twin-title" aria-busy={busy !== null} data-adaptive-state={state ? "loaded" : "idle"}>
      <header className="adaptive-heading">
        <div>
          <span className="adaptive-eyebrow">Проверяемое изменение</span>
          <h3 id="adaptive-cognitive-twin-title">Адаптивный профиль</h3>
          <p>Собери ограниченное предложение для одной цели, проверь точные источники и реши сам, менять ли порядок работы с контекстом.</p>
        </div>
        <div className="adaptive-heading-meta"><span><Icon name="copy" size={18} aria-hidden="true" /> Только текущая страница</span><span><Icon name="info" size={18} aria-hidden="true" /> Без провайдера и автоактивации</span></div>
      </header>

      <div className="adaptive-toolbar">
        <p className="adaptive-status" role="status" aria-live="polite">{status}</p>
        <button type="button" className="adaptive-button adaptive-button-secondary" disabled={busy !== null} onClick={loadDiscovery}>{busy === "load" ? "Загружаем…" : "Загрузить текущие цели"}</button>
      </div>
      <ErrorMessage message={error} />

      {state ? (
        <section className="adaptive-selection" aria-labelledby="adaptive-selection-title">
          <div className="adaptive-section-heading"><Icon name="growth" size={28} aria-hidden="true" /><div><h4 id="adaptive-selection-title">Выбери точную цель</h4><p>Источник цели и её отпечаток проверяются заново на сервере.</p></div></div>
          <div className="adaptive-field-grid">
            <label className="adaptive-field"><span>Текущая цель</span><select value={selectedGoalId} onChange={(event) => { setSelectedGoalId(event.target.value); setSelectedExperiment(""); setProjection(null); setEvaluation(null); setConfirmed(false); }} disabled={busy !== null || state.goals.length === 0}><option value="">Выбери цель</option>{state.goals.map((goal) => <option value={goal.source_note_uuid} key={goal.source_note_uuid}>{goal.goal_text}</option>)}</select><small>{selectedGoal?.domain ? `Область: ${selectedGoal.domain}.` : "Выбирается одна точная текущая цель."}</small></label>
            <label className="adaptive-field"><span>Дополнительный точный источник</span><select value={selectedExperiment} onChange={(event) => { setSelectedExperiment(event.target.value); setProjection(null); setEvaluation(null); setConfirmed(false); }} disabled={busy !== null || !selectedGoal}><option value="">Без дополнительного источника</option>{selectors.map((item, index) => <option value={selectedKey(item)} key={selectedKey(item)}>{`Проверяемый эксперимент ${index + 1}`}</option>)}</select><small>{selectors.length > 0 ? "Источник выбирается по идентификатору и отпечатку; содержимое в браузер не передаётся." : "Для этой цели нет доступного дополнительного точного источника."}</small></label>
          </div>
          <div className="adaptive-action-row">
            <button type="button" className="adaptive-button adaptive-button-secondary" disabled={busy !== null || !selectedGoal} onClick={loadSelectedState}>{busy === "load" ? "Проверяем…" : "Загрузить состояние цели"}</button>
            <button type="button" className="adaptive-button adaptive-button-primary" disabled={busy !== null || !selectedGoal} onClick={buildCandidateAction}>{busy === "candidate" ? "Собираем…" : "Собрать предложение"}</button>
          </div>
        </section>
      ) : null}

      {!state ? <div className="adaptive-empty-state"><Icon name="copy" size={34} aria-hidden="true" /><h4>Состояние пока не загружено</h4><p>Нажми «Загрузить текущие цели», когда будешь готов показать точный срез на этой странице.</p></div> : null}
      {state && state.goals.length === 0 ? <div className="adaptive-empty-state"><Icon name="warning" size={34} aria-hidden="true" /><h4>Точных целей пока нет</h4><p>Предложение нельзя собрать без одной текущей цели с проверенным источником.</p></div> : null}

      {shownProjection ? (
        <div className="adaptive-result-stack">
          <div className="adaptive-overview-strip"><StateBadge value={shownProjection.lifecycle_state} labels={LIFECYCLE_LABELS} fallback="Состояние профиля требует уточнения." tone={shownProjection.profile_validity === "stale" ? "danger" : "accent"} /><span>{safeLabel(shownProjection.profile_validity, { none: "Проверка ещё не проведена", valid: "Профиль совпадает с источниками", stale: "Профиль устарел" }, "Проверка требует уточнения.")}</span><span>Срез: {formatMoment(shownProjection.as_of)}</span></div>
          <ReadinessList items={shownProjection.source_readiness} />
          <CandidateSection candidate={shownProjection.candidate} />
          <ProfileSummary profile={shownProjection.active_profile} title="Текущий активный профиль" />

          <section className="adaptive-section adaptive-confirmation-section" aria-labelledby="adaptive-actions-title">
            <div className="adaptive-section-heading"><Icon name="success" size={28} aria-hidden="true" /><div><h4 id="adaptive-actions-title">Решение владельца</h4><p>Ни одно действие не выполняется автоматически. Подтверди точный срез перед изменением.</p></div></div>
            <label className="adaptive-confirmation"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy !== null} /><span>Я проверил цель, источники и предлагаемое изменение; разрешаю выполнить выбранное действие.</span></label>
            <div className="adaptive-action-row adaptive-action-row-wrap">
              <button type="button" className="adaptive-button adaptive-button-secondary" disabled={busy !== null || !confirmed || shownProjection.candidate.status !== "candidate" || candidateReviewed} onClick={review}>{busy === "review" ? "Проверяем…" : candidateReviewed ? "Предложение уже проверено" : "Отметить проверенным"}</button>
              {!activeProfile ? <button type="button" className="adaptive-button adaptive-button-primary" disabled={busy !== null || !confirmed || !candidateReviewed || shownProjection.candidate.status !== "candidate"} onClick={activate}>{busy === "activate" ? "Активируем…" : "Активировать профиль"}</button> : <button type="button" className="adaptive-button adaptive-button-primary" disabled={busy !== null || !confirmed || !candidateReviewed || shownProjection.candidate.status !== "candidate" || !shownProjection.candidate.prior_profile_id} onClick={supersede}>{busy === "supersede" ? "Заменяем…" : "Заменить текущий профиль"}</button>}
              <button type="button" className="adaptive-button adaptive-button-danger" disabled={busy !== null || !confirmed || !candidateReviewed || shownProjection.candidate.status !== "candidate"} onClick={reject}>{busy === "reject" ? "Отклоняем…" : "Отклонить предложение"}</button>
            </div>
            {activeProfile ? <button type="button" className="adaptive-button adaptive-button-secondary adaptive-evaluate-button" disabled={busy !== null || !confirmed} onClick={evaluate}>{busy === "evaluate" ? "Сравниваем…" : "Сравнить с текущим источником"}</button> : null}
          </section>

          <EvaluationSection evaluation={evaluation} phrase={nonCausalPhrase} />
          <ProfileHistory history={state?.profile_history ?? []} active={activeProfile} disabled={busy !== null || !confirmed} onRevert={revert} />
          <TechnicalDisclosure projection={shownProjection} />
        </div>
      ) : null}
    </section>
  );
}
