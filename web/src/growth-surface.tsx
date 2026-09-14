import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  applyPersonalMemory,
  confirmGrowthMapping,
  executeGrowthAdvisor,
  loadGrowth,
  loadGrowthGoals,
  loadGrowthMappingStatus,
  preparePersonalMemory,
  previewGrowthAdvisor,
  requestGrowthLearningQuestion,
  resolveGrowthLearningQuestion,
  reviewActiveLearningAnswer,
  reviewGrowthMapping,
  type GrowthAdvisorBranchResponse,
  type GrowthAdvisorPreviewResponse,
  type GrowthAdvisorRequest,
  type GrowthGoalsResponse,
  type GrowthLearningCandidate,
  type GrowthLearningRequest,
  type GrowthLearningResolutionResponse,
  type GrowthLearningResult,
  type GrowthMappingReviewResponse,
  type GrowthMappingStatusResponse,
  type GrowthRelation,
  type GrowthRelationResult,
  type GrowthResponse,
  type NoteDraft,
  type PersonalMemoryPayload,
  type SavePlanResponse,
  type SavedNoteResponse,
  type StatedObservedMappingSelector,
} from "./api";
import {
  PersonalMemoryMetadataFields,
  type PersonalMemoryFieldValues,
  type PersonalMemoryTimeMode,
} from "./personal-memory-metadata-fields";
import { GoalProgressSurface } from "./goal-progress-surface";
import { presentCode, presentError } from "./presentation";

const RELATION_LABELS: Record<GrowthRelation, string> = {
  supports_goal: "согласуется с целью",
  conflicts_with_goal: "конфликтует с целью",
  neutral_or_unknown: "нейтрально или неизвестно",
};

const LIFECYCLE_LABELS: Record<string, string> = {
  active: "активна",
  superseded: "заменена новой записью",
  invalidated: "признана недействительной",
  deleted: "удалена из активного представления",
};

const STATE_LABELS: Record<string, string> = {
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

const STATE_COPY: Record<string, string> = {
  supports_goal: "Для текущего точного сопоставимого варианта владелец ранее явно подтвердил связь «согласуется с целью». Это описание связи, а не оценка результата.",
  conflicts_with_goal: "Для текущего точного варианта явно подтверждена связь с выбранной целью как конфликтующая. Это не вывод о личности и не прогноз.",
  neutral_or_unknown: "Текущая связь не указывает ни на поддержку, ни на конфликт с выбранной целью.",
  goal_mapping_missing: "Для текущего варианта пока нет явной связи с выбранной целью. Отсутствие связи не означает конфликт.",
  mixed_behavior: "В сопоставимых контекстах наблюдались разные варианты. Одного победившего варианта нет.",
  changed_behavior: "В разных временных окнах наблюдались разные варианты. Это изменение наблюдаемого контекста, а не утверждение о личности.",
  behavioral_evidence_insufficient: "Сопоставимых поведенческих данных недостаточно. Безопасный результат — воздержаться от вывода.",
  not_comparable: "Источники нельзя сопоставить по текущей точной политике. Вывод не строится.",
  goal_source_missing: "Источник выбранной цели отсутствует. Связь и вывод недоступны.",
  goal_selection_required: "Сначала явно выбери одну текущую цель.",
};

const NO_CANDIDATE_COPY: Record<string, string> = {
  QUESTIONS_DISABLED: "Вопросы раздела «Развитие» отключены для этого запроса.",
  GOAL_SOURCE_MISSING: "Источник выбранной цели отсутствует; вопрос не создаётся.",
  GOAL_SELECTION_REQUIRED: "Для вопроса уточнения нужно явно выбрать одну цель.",
  NO_ACTIONABLE_GROWTH_GAP: "Сейчас нет пробела, который нужно уточнять.",
  GROWTH_STATE_NOT_COMPARABLE: "Текущее состояние нельзя уточнять по точной политике.",
  UNSUPPORTED_GROWTH_STATE: "Для этого состояния вопрос не предусмотрен.",
  CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY: "Вопрос уже показан на этой странице.",
};

const EMPTY_MEMORY_FIELDS: PersonalMemoryFieldValues = {
  evidenceKind: "",
  selfKind: "",
  timeMode: "unknown",
  evidenceAt: "unknown",
  domain: "",
};

type OperationKind = "refresh" | "growth" | "mapping-review" | "mapping-confirm" | "advisor" | "learning" | "memory";

function uuidv7(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  let timestamp = Date.now();
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = timestamp & 0xff;
    timestamp = Math.floor(timestamp / 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function isAbortError(error: unknown): boolean {
  return (error instanceof DOMException && error.name === "AbortError")
    || (typeof error === "object" && error !== null && "name" in error && error.name === "AbortError");
}

function stateLabel(value: string): string {
  return STATE_LABELS[value] ?? presentCode(value, "Состояние не определено");
}

function stateCopy(value: string): string {
  return STATE_COPY[value] ?? "Для этого состояния нет безопасного текстового вывода.";
}

function lifecycleLabel(value: string): string {
  return LIFECYCLE_LABELS[value] ?? presentCode(value, "состояние не определено");
}

function safeText(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function splitLines(value: string, max: number): string[] {
  return value.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean).slice(0, max);
}

function selectedSelector(relation: GrowthRelationResult | null): StatedObservedMappingSelector | null {
  if (!relation?.goal || !relation.cohort_fingerprint || !relation.behavioral_option) return null;
  return {
    source_note_uuid: relation.goal.source_note_uuid,
    behavioral_cohort_fingerprint: relation.cohort_fingerprint,
    behavioral_option_index: relation.behavioral_option.option_index,
    behavioral_option_fingerprint: relation.behavioral_option.option_fingerprint,
  };
}

function selectedOptionLabel(review: GrowthMappingReviewResponse): string {
  const option = review.ordered_options.find((item) => item.option_index === review.selected_option.option_index);
  return option ? option.label : `Вариант ${review.selected_option.option_index}`;
}

function advisorResultText(branch: GrowthAdvisorBranchResponse): string[] {
  const result = branch.assistant_result;
  if (!result) return [];
  const values: string[] = [];
  for (const key of ["output_label", "recommendation", "abstention_code"]) {
    const value = result[key];
    if (typeof value === "string" && value) values.push(value);
  }
  for (const key of ["rationale", "uncertainty"]) {
    const value = result[key];
    if (Array.isArray(value)) {
      values.push(...value.filter((item): item is string => typeof item === "string"));
    }
  }
  const selected = result.selected_option;
  if (typeof selected === "object" && selected !== null && "label" in selected && typeof selected.label === "string") {
    values.push(`Выбранный вариант: ${selected.label}`);
  }
  return values;
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="growth-error" role="alert" tabIndex={-1} ref={ref}>{message}</p> : null;
}

export function GrowthSurface(): ReactElement {
  const [goals, setGoals] = useState<GrowthGoalsResponse | null>(null);
  const [selectedGoalUuid, setSelectedGoalUuid] = useState("");
  const [growth, setGrowth] = useState<GrowthResponse | null>(null);
  const [mappingStatus, setMappingStatus] = useState<GrowthMappingStatusResponse | null>(null);
  const [mappingSelector, setMappingSelector] = useState<StatedObservedMappingSelector | null>(null);
  const [mappingRelation, setMappingRelation] = useState<GrowthRelation | "">("");
  const [mappingReview, setMappingReview] = useState<GrowthMappingReviewResponse | null>(null);
  const [mappingConfirmed, setMappingConfirmed] = useState(false);
  const [mappingAcceptedId, setMappingAcceptedId] = useState("");

  const [advisorTask, setAdvisorTask] = useState("");
  const [advisorOptions, setAdvisorOptions] = useState("");
  const [advisorConstraints, setAdvisorConstraints] = useState("");
  const [advisorFacts, setAdvisorFacts] = useState("");
  const [advisorBackground, setAdvisorBackground] = useState("");
  const [advisorRequest, setAdvisorRequest] = useState<GrowthAdvisorRequest | null>(null);
  const [advisorPreview, setAdvisorPreview] = useState<GrowthAdvisorPreviewResponse | null>(null);
  const [advisorBranch, setAdvisorBranch] = useState<GrowthAdvisorBranchResponse | null>(null);
  const [advisorConfirmed, setAdvisorConfirmed] = useState(false);

  const [learningResult, setLearningResult] = useState<GrowthLearningResult | null>(null);
  const [learningCandidate, setLearningCandidate] = useState<GrowthLearningCandidate | null>(null);
  const [learningAnswer, setLearningAnswer] = useState("");
  const [learningResolution, setLearningResolution] = useState<GrowthLearningResolutionResponse | null>(null);

  const [memoryDraft, setMemoryDraft] = useState<NoteDraft | null>(null);
  const [memoryReviewToken, setMemoryReviewToken] = useState<string | null>(null);
  const [memoryFields, setMemoryFields] = useState<PersonalMemoryFieldValues>(EMPTY_MEMORY_FIELDS);
  const [memoryConfirmed, setMemoryConfirmed] = useState(false);
  const [memoryPlan, setMemoryPlan] = useState<SavePlanResponse | null>(null);
  const [memorySaved, setMemorySaved] = useState<SavedNoteResponse | null>(null);

  const [busy, setBusy] = useState<OperationKind | null>(null);
  const [status, setStatus] = useState("Данные раздела «Развитие» загружаются только после явного действия.");
  const [error, setError] = useState("");
  const refreshController = useRef<AbortController | null>(null);
  const growthController = useRef<AbortController | null>(null);
  const mappingController = useRef<AbortController | null>(null);
  const advisorController = useRef<AbortController | null>(null);
  const learningController = useRef<AbortController | null>(null);
  const memoryController = useRef<AbortController | null>(null);
  const refreshSequence = useRef(0);
  const growthSequence = useRef(0);
  const mappingSequence = useRef(0);
  const advisorSequence = useRef(0);
  const learningSequence = useRef(0);
  const memorySequence = useRef(0);
  const mappingHandoffRef = useRef<HTMLParagraphElement>(null);

  const selectedGoal = goals?.goals.find((item) => item.goal.source_note_uuid === selectedGoalUuid) ?? null;
  const currentRelation = growth?.goal_results[0] ?? null;
  const currentSelector = mappingSelector ?? selectedSelector(currentRelation);
  const activeMapping = currentSelector && mappingStatus
    ? mappingStatus.mappings.find((item) => (
      item.lifecycle_state === "active"
      && item.goal.source_note_uuid === currentSelector.source_note_uuid
      && item.behavioral_target.cohort_fingerprint === currentSelector.behavioral_cohort_fingerprint
      && item.behavioral_target.option_index === currentSelector.behavioral_option_index
      && item.behavioral_target.option_fingerprint === currentSelector.behavioral_option_fingerprint
    )) ?? null
    : null;
  const sameMappingAlreadyActive = Boolean(activeMapping && mappingReview && activeMapping.mapping_fingerprint === mappingReview.candidate_mapping_fingerprint);
  const learningRequest: GrowthLearningRequest | null = selectedGoalUuid
    ? { contract_version: "growth-learning-v1", goal_source_uuid: selectedGoalUuid }
    : null;

  function cancelControllers(): void {
    refreshController.current?.abort();
    growthController.current?.abort();
    mappingController.current?.abort();
    advisorController.current?.abort();
    learningController.current?.abort();
    memoryController.current?.abort();
    refreshController.current = null;
    growthController.current = null;
    mappingController.current = null;
    advisorController.current = null;
    learningController.current = null;
    memoryController.current = null;
    refreshSequence.current += 1;
    growthSequence.current += 1;
    mappingSequence.current += 1;
    advisorSequence.current += 1;
    learningSequence.current += 1;
    memorySequence.current += 1;
  }

  function clearGoalState(): void {
    growthController.current?.abort();
    mappingController.current?.abort();
    advisorController.current?.abort();
    learningController.current?.abort();
    memoryController.current?.abort();
    growthSequence.current += 1;
    mappingSequence.current += 1;
    advisorSequence.current += 1;
    learningSequence.current += 1;
    memorySequence.current += 1;
    setGrowth(null);
    setMappingSelector(null);
    setMappingReview(null);
    setMappingRelation("");
    setMappingConfirmed(false);
    setMappingAcceptedId("");
    setAdvisorRequest(null);
    setAdvisorPreview(null);
    setAdvisorBranch(null);
    setAdvisorConfirmed(false);
    setLearningResult(null);
    setLearningCandidate(null);
    setLearningAnswer("");
    setLearningResolution(null);
    setMemoryDraft(null);
    setMemoryReviewToken(null);
    setMemoryPlan(null);
    setMemorySaved(null);
    setMemoryFields(EMPTY_MEMORY_FIELDS);
    setMemoryConfirmed(false);
  }

  async function refresh(): Promise<void> {
    if (busy) return;
    cancelControllers();
    const controller = new AbortController();
    refreshController.current = controller;
    const sequence = refreshSequence.current;
    setBusy("refresh");
    setError("");
    setStatus("Перестраиваю список текущих целей и состояние раздела «Развитие» из серверного источника…");
    setSelectedGoalUuid("");
    clearGoalState();
    try {
      const [nextGoals, nextMappings] = await Promise.all([
        loadGrowthGoals(undefined, controller.signal),
        loadGrowthMappingStatus(undefined, controller.signal),
      ]);
      if (controller.signal.aborted || sequence !== refreshSequence.current) return;
      setGoals(nextGoals);
      setMappingStatus(nextMappings);
      setStatus(nextGoals.goals.length > 0
        ? "Цели готовы. Выбери одну явно, затем отдельно построй результат развития."
        : "В текущем источнике нет доступной цели.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== refreshSequence.current) return;
      setError(presentError(caught, "Текущие цели для раздела «Развитие» недоступны."));
      setStatus("");
    } finally {
      if (sequence === refreshSequence.current) {
        refreshController.current = null;
        setBusy(null);
      }
    }
  }

  function selectGoal(value: string): void {
    if (busy) return;
    clearGoalState();
    setSelectedGoalUuid(value);
    setError("");
    setStatus(value ? "Цель выбрана. Нажми «Построить результат развития» для нового перестроения на сервере." : "Цель не выбрана.");
  }

  async function buildCurrentGrowth(): Promise<void> {
    if (!selectedGoalUuid || busy) {
      if (!selectedGoalUuid) setError("Сначала выбери одну текущую цель.");
      return;
    }
    growthController.current?.abort();
    const controller = new AbortController();
    growthController.current = controller;
    const sequence = ++growthSequence.current;
    setBusy("growth");
    setError("");
    setStatus("Перестраиваю текущий результат развития по выбранной цели…");
    setGrowth(null);
    setMappingSelector(null);
    setMappingReview(null);
    setMappingRelation("");
    setMappingConfirmed(false);
    setMappingAcceptedId("");
    setLearningResult(null);
    setLearningCandidate(null);
    setLearningResolution(null);
    try {
      const result = await loadGrowth(selectedGoalUuid, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== growthSequence.current) return;
      setGrowth(result);
      setStatus("Картина развития готова. Состояние описывает только текущий точный срез; автоматических действий нет.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== growthSequence.current) return;
      setError(presentError(caught, "Текущий результат развития недоступен."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === growthSequence.current) {
        growthController.current = null;
        setBusy(null);
      }
    }
  }

  async function startMappingReview(): Promise<void> {
    if (!currentSelector || !mappingRelation || busy) {
      if (!currentSelector) setError("Для текущего состояния нет точного варианта для проверки связи.");
      else if (!mappingRelation) setError("Выбери одну связь: согласуется, конфликтует или нейтрально.");
      return;
    }
    mappingController.current?.abort();
    const controller = new AbortController();
    mappingController.current = controller;
    const sequence = ++mappingSequence.current;
    setBusy("mapping-review");
    setError("");
    setMappingReview(null);
    setMappingConfirmed(false);
    setMappingAcceptedId("");
    setStatus("Сервер заново проверяет цель, точный контекст и выбранный вариант…");
    try {
      const review = await reviewGrowthMapping(currentSelector, mappingRelation, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== mappingSequence.current) return;
      setMappingReview(review);
      setStatus("Проверка готова. Проверь текст и контекст, затем явно подтверди связь.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== mappingSequence.current) return;
      setError(presentError(caught, "Не удалось подготовить проверку связи развития."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === mappingSequence.current) {
        mappingController.current = null;
        setBusy(null);
      }
    }
  }

  async function confirmMapping(): Promise<void> {
    if (!currentSelector || !mappingRelation || !mappingReview?.candidate_mapping_fingerprint || !mappingConfirmed || busy || sameMappingAlreadyActive) return;
    let operationId: string;
    try {
      operationId = uuidv7();
    } catch {
      setError("Не удалось создать UUID операции. Повтори подтверждение.");
      return;
    }
    mappingController.current?.abort();
    const controller = new AbortController();
    mappingController.current = controller;
    const sequence = ++mappingSequence.current;
    setBusy("mapping-confirm");
    setError("");
    setStatus("Сервер повторно проверяет данные и добавляет новую запись без изменения истории…");
    try {
      const accepted = await confirmGrowthMapping(
        currentSelector,
        mappingRelation,
        operationId,
        mappingReview.candidate_mapping_fingerprint,
        undefined,
        controller.signal,
      );
      if (controller.signal.aborted || sequence !== mappingSequence.current) return;
      setMappingAcceptedId(typeof accepted.mapping.mapping_id === "string" ? accepted.mapping.mapping_id : "принято");
      setMappingConfirmed(false);
      setStatus("Связь принята. Создана новая неизменяемая запись; исходная история не редактируется.");
      const [nextMappings, nextGrowth] = await Promise.all([
        loadGrowthMappingStatus(undefined, controller.signal),
        loadGrowth(selectedGoalUuid, undefined, controller.signal),
      ]);
      if (controller.signal.aborted || sequence !== mappingSequence.current) return;
      setMappingStatus(nextMappings);
      setGrowth(nextGrowth);
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== mappingSequence.current) return;
      setError(presentError(caught, "Связь не принята. Проверка могла устареть; начни её заново."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === mappingSequence.current) {
        mappingController.current = null;
        setBusy(null);
      }
    }
  }

  function updateAdvisor(value: { readonly field: "task" | "options" | "constraints" | "facts" | "background"; readonly text: string }): void {
    if (value.field === "task") setAdvisorTask(value.text);
    if (value.field === "options") setAdvisorOptions(value.text);
    if (value.field === "constraints") setAdvisorConstraints(value.text);
    if (value.field === "facts") setAdvisorFacts(value.text);
    if (value.field === "background") setAdvisorBackground(value.text);
    setAdvisorRequest(null);
    setAdvisorPreview(null);
    setAdvisorBranch(null);
    setAdvisorConfirmed(false);
  }

  function makeAdvisorRequest(): GrowthAdvisorRequest | null {
    if (!selectedGoal || !advisorTask.trim()) return null;
    return {
      contract_version: "growth-advisor-v1",
      goal_source_uuid: selectedGoal.goal.source_note_uuid,
      goal_identity_fingerprint: selectedGoal.goal_identity_fingerprint,
      task: advisorTask.trim(),
      options: splitLines(advisorOptions, 8).map((label, index) => ({ id: `growth-option-${index}`, label })),
      explicit_constraints: splitLines(advisorConstraints, 16),
      explicit_context: [
        ...splitLines(advisorFacts, 16).map((text) => ({ kind: "fact" as const, text })),
        ...splitLines(advisorBackground, 16).map((text) => ({ kind: "background" as const, text })),
      ],
      max_context_bytes: 64 * 1024,
      max_result_bytes: 64 * 1024,
    };
  }

  async function createAdvisorPreview(): Promise<void> {
    const request = makeAdvisorRequest();
    if (!request || busy) {
      if (!request) setError("Укажи задачу для советника и проверь, что цель всё ещё выбрана.");
      return;
    }
    advisorController.current?.abort();
    const controller = new AbortController();
    advisorController.current = controller;
    const sequence = ++advisorSequence.current;
    setBusy("advisor");
    setError("");
    setAdvisorRequest(request);
    setAdvisorPreview(null);
    setAdvisorBranch(null);
    setAdvisorConfirmed(false);
    setStatus("Готовлю предпросмотр текущей цели без вызова провайдера…");
    try {
      const preview = await previewGrowthAdvisor(request, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== advisorSequence.current) return;
      setAdvisorPreview(preview);
      setStatus("Предпросмотр готов. Текст цели показан отдельно; провайдер ещё не вызывался.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== advisorSequence.current) return;
      setError(presentError(caught, "Не удалось подготовить предпросмотр советника."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === advisorSequence.current) {
        advisorController.current = null;
        setBusy(null);
      }
    }
  }

  async function executeAdvisor(): Promise<void> {
    if (!advisorRequest || !advisorPreview || !advisorConfirmed || busy) return;
    advisorController.current?.abort();
    const controller = new AbortController();
    advisorController.current = controller;
    const sequence = ++advisorSequence.current;
    setBusy("advisor");
    setError("");
    setStatus("Выполняю только явно подтверждённую независимую рекомендацию…");
    try {
      const branch = await executeGrowthAdvisor(advisorRequest, advisorPreview, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== advisorSequence.current) return;
      setAdvisorBranch(branch);
      setAdvisorConfirmed(false);
      setStatus("Независимая рекомендация готова и остаётся временным результатом.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== advisorSequence.current) return;
      setError(presentError(caught, "Независимая рекомендация недоступна."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === advisorSequence.current) {
        advisorController.current = null;
        setBusy(null);
      }
    }
  }

  async function requestLearning(): Promise<void> {
    if (!learningRequest || learningCandidate || busy) {
      if (!learningRequest) setError("Для уточнения нужно явно выбрать цель.");
      return;
    }
    learningController.current?.abort();
    const controller = new AbortController();
    learningController.current = controller;
    const sequence = ++learningSequence.current;
    setBusy("learning");
    setError("");
    setLearningResolution(null);
    setLearningAnswer("");
    setStatus("Проверяю текущее состояние развития и запрашиваю не более одного вопроса по явному запросу…");
    try {
      const result = await requestGrowthLearningQuestion(learningRequest, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== learningSequence.current) return;
      setLearningResult(result);
      setLearningCandidate(result.candidate);
      setStatus(result.status === "candidate" ? "Один вопрос готов. Выбери ровно одно явное действие." : (NO_CANDIDATE_COPY[result.no_candidate_code ?? ""] ?? "Сейчас вопрос не нужен."));
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== learningSequence.current) return;
      setError(presentError(caught, "Не удалось подготовить вопрос для уточнения."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === learningSequence.current) {
        learningController.current = null;
        setBusy(null);
      }
    }
  }

  async function resolveLearning(disposition: "ignore" | "reject" | "review" | "answer"): Promise<void> {
    if (!learningRequest || !learningCandidate || busy) return;
    if (disposition === "answer" && !learningAnswer.trim()) {
      setError("Напиши ответ перед явным действием «Ответить».");
      return;
    }
    if (Date.parse(learningCandidate.expires_at) <= Date.now()) {
      setLearningCandidate(null);
      setError("Вопрос для уточнения устарел; запроси новый после обновления.");
      setStatus("");
      return;
    }
    learningController.current?.abort();
    const controller = new AbortController();
    learningController.current = controller;
    const sequence = ++learningSequence.current;
    setBusy("learning");
    setError("");
    setStatus("Сервер повторно проверяет вопрос и выбранное действие…");
    try {
      const result = await resolveGrowthLearningQuestion(
        learningRequest,
        learningCandidate,
        disposition,
        disposition === "answer" ? learningAnswer.trim() : null,
        undefined,
        controller.signal,
      );
      if (controller.signal.aborted || sequence !== learningSequence.current) return;
      setLearningResolution(result);
      setLearningCandidate(null);
      if (result.handoff?.kind === "relation_review" && result.handoff.selector) {
        setMappingSelector(result.handoff.selector);
        setMappingReview(null);
        setMappingRelation("");
        setStatus("Уточнение передало только точный выбор. Выбери связь и отдельно запусти её проверку.");
        mappingHandoffRef.current?.focus();
      } else if (result.answer_draft) {
        setMemoryDraft({
          title: "",
          note_type: "resource",
          content: result.answer_draft.text,
          tags: [],
          links: [],
        });
        setMemoryReviewToken(null);
        setMemoryPlan(null);
        setMemorySaved(null);
        setMemoryFields(EMPTY_MEMORY_FIELDS);
        setMemoryConfirmed(false);
        setStatus("Ответ подготовлен как временный черновик. Только следующая явная проверка может передать его в личную память.");
      } else {
        setStatus(disposition === "ignore" ? "Вопрос проигнорирован без записи." : disposition === "reject" ? "Вопрос отклонён без записи." : "Действие завершено без записи.");
      }
      setLearningAnswer("");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== learningSequence.current) return;
      setError(presentError(caught, "Вопрос для уточнения устарел или недоступен."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === learningSequence.current) {
        learningController.current = null;
        setBusy(null);
      }
    }
  }

  function updateMemoryDraft<K extends keyof NoteDraft>(key: K, value: NoteDraft[K]): void {
    setMemoryDraft((current) => current ? { ...current, [key]: value } : current);
    setMemoryReviewToken(null);
    setMemoryPlan(null);
    setMemorySaved(null);
    setMemoryConfirmed(false);
  }

  function memoryPayload(): PersonalMemoryPayload {
    return {
      evidence_kind: memoryFields.evidenceKind,
      self_kind: memoryFields.selfKind,
      evidence_at: memoryFields.timeMode === "exact" ? memoryFields.evidenceAt : "unknown",
      evidence_at_precision: memoryFields.timeMode,
      domain: memoryFields.domain.trim() ? memoryFields.domain.trim() : null,
    };
  }

  async function reviewMemoryDraft(): Promise<void> {
    if (!memoryDraft || busy) return;
    if (!memoryDraft.title.trim() || !memoryDraft.content.trim()) {
      setError("Заполни название и содержание ответа перед проверкой личной памяти.");
      return;
    }
    memoryController.current?.abort();
    const controller = new AbortController();
    memoryController.current = controller;
    const sequence = ++memorySequence.current;
    setBusy("memory");
    setError("");
    setStatus("Проверяю ответ через существующую проверку черновика без записи…");
    try {
      const reviewed = await reviewActiveLearningAnswer(memoryDraft, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== memorySequence.current) return;
      setMemoryDraft(reviewed.draft);
      setMemoryReviewToken(reviewed.review_token);
      setMemoryPlan(null);
      setMemorySaved(null);
      setMemoryConfirmed(false);
      setStatus("Проверка черновика готова. Проверь метаданные личной памяти перед подготовкой безопасного сохранения.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== memorySequence.current) return;
      setError(presentError(caught, "Не удалось проверить ответ для личной памяти."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === memorySequence.current) {
        memoryController.current = null;
        setBusy(null);
      }
    }
  }

  async function prepareMemory(): Promise<void> {
    if (!memoryDraft || !memoryReviewToken || busy) return;
    const payload = memoryPayload();
    if (!memoryConfirmed) {
      setError("Подтверди проверку метаданных личной памяти.");
      return;
    }
    if (!payload.evidence_kind || !payload.self_kind || (payload.evidence_at_precision === "exact" && !payload.evidence_at)) {
      setError("Заполни обязательные метаданные личной памяти.");
      return;
    }
    memoryController.current?.abort();
    const controller = new AbortController();
    memoryController.current = controller;
    const sequence = ++memorySequence.current;
    setBusy("memory");
    setError("");
    setStatus("Готовлю предпросмотр безопасного сохранения; канонической записи ещё нет…");
    try {
      const plan = await preparePersonalMemory(memoryReviewToken, memoryDraft, payload);
      if (controller.signal.aborted || sequence !== memorySequence.current) return;
      setMemoryPlan(plan);
      setStatus("Предпросмотр безопасного сохранения готов. Только явное следующее подтверждение создаст заметку.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== memorySequence.current) return;
      setError(presentError(caught, "Не удалось подготовить безопасное сохранение."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === memorySequence.current) {
        memoryController.current = null;
        setBusy(null);
      }
    }
  }

  async function saveMemory(): Promise<void> {
    if (!memoryDraft || !memoryReviewToken || !memoryPlan || busy) return;
    memoryController.current?.abort();
    const controller = new AbortController();
    memoryController.current = controller;
    const sequence = ++memorySequence.current;
    setBusy("memory");
    setError("");
    setStatus("Сохраняю только после явного подтверждения безопасного сохранения…");
    try {
      const saved = await applyPersonalMemory(memoryReviewToken, memoryPlan.confirmation_token, memoryDraft, memoryPayload());
      if (controller.signal.aborted || sequence !== memorySequence.current) return;
      setMemorySaved(saved);
      setStatus("Личная память сохранена через существующее безопасное сохранение.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== memorySequence.current) return;
      setError(presentError(caught, "Не удалось сохранить личную память."));
      setStatus("");
    } finally {
      if (!controller.signal.aborted && sequence === memorySequence.current) {
        memoryController.current = null;
        setBusy(null);
      }
    }
  }

  useEffect(() => () => cancelControllers(), []);

  const advisorText = advisorBranch ? advisorResultText(advisorBranch) : [];
  const learningButtonDisabled = !learningCandidate || busy !== null;

  return (
    <section className="growth-surface" id="growth-engine" aria-labelledby="growth-title" aria-busy={busy !== null}>
      <div className="growth-heading">
        <div>
          <p className="growth-eyebrow">Модель себя · этап 11E</p>
          <h3 id="growth-title">Развитие: цель → наблюдаемый выбор</h3>
          <p>Текущая цель, точная наблюдаемая связь и независимая рекомендация остаются отдельными слоями.</p>
        </div>
        <div className="growth-heading-meta"><span>управляется сервером</span><span>без сохранения · только по явному запросу</span></div>
      </div>

      <div className="growth-toolbar">
        <button className="growth-button growth-button-primary" type="button" disabled={busy !== null} aria-busy={busy === "refresh"} onClick={() => void refresh()}>
          {busy === "refresh" ? "Обновляю…" : "Обновить данные развития"}
        </button>
        <p className="growth-status" role="status" aria-live="polite">{status}</p>
      </div>
      <ErrorMessage message={error} />

      {!goals ? (
        <p className="growth-empty">Нажми «Обновить данные развития», чтобы явно загрузить список текущих целей с сервера.</p>
      ) : (
        <>
          <section className="growth-panel growth-goal-panel" aria-labelledby="growth-goal-title">
            <div className="growth-panel-heading"><span className="growth-index">01</span><div><h4 id="growth-goal-title">Текущая цель</h4><p>Выбор принадлежит владельцу; приложение не ранжирует цели и не создаёт синтетическую цель.</p></div></div>
            <label className="growth-label" htmlFor="growth-goal-select">Выбери одну цель явно</label>
            <select className="growth-input" id="growth-goal-select" value={selectedGoalUuid} disabled={busy !== null} onChange={(event) => selectGoal(event.target.value)}>
              <option value="">Выбери текущую цель</option>
              {goals.goals.map((item) => <option key={item.goal.source_note_uuid} value={item.goal.source_note_uuid}>{item.goal_text}</option>)}
            </select>
            {selectedGoal ? (
              <div className="growth-goal-preview"><p>{selectedGoal.goal_text}</p><dl className="growth-fields"><div><dt>Домен</dt><dd>{safeText(selectedGoal.goal.domain)}</dd></div><div><dt>Источник</dt><dd>{selectedGoal.goal.source_note_uuid}</dd></div></dl></div>
            ) : <p className="growth-muted">После выбора нажми отдельную кнопку построения. Сам выбор не запускает чтение источников.</p>}
             <button className="growth-button growth-button-secondary" type="button" disabled={!selectedGoalUuid || busy !== null} aria-busy={busy === "growth"} onClick={() => void buildCurrentGrowth()}>{busy === "growth" ? "Строю результат развития…" : "Построить результат развития"}</button>
          </section>

          {growth ? (
            <section className="growth-panel" aria-labelledby="growth-result-title">
               <div className="growth-panel-heading"><span className="growth-index">02</span><div><h4 id="growth-result-title">Текущее состояние</h4><p>Описательный точный результат без оценки, вероятности и скрытого вывода о личности.</p></div></div>
              <div className="growth-result-list">
                {growth.goal_results.map((relation, index) => (
                  <article className={`growth-result growth-result-${relation.state}`} key={`${relation.goal?.source_note_uuid ?? "missing"}-${index}`}>
                    <div className="growth-result-topline"><span className="growth-state-chip">{stateLabel(relation.state)}</span><span>{relation.goal?.source_note_uuid ?? "Источник цели не указан"}</span></div>
                    <p className="growth-result-copy">{stateCopy(relation.state)}</p>
                    {relation.behavioral_option ? <p className="growth-detail">Точный наблюдаемый вариант: {relation.behavioral_option.option_index}</p> : null}
                    <details className="growth-technical"><summary>Технические сведения</summary><dl className="growth-fields growth-fields-technical"><div><dt>Состояние</dt><dd>{presentCode(relation.state, "не определено")}</dd></div><div><dt>Код причины</dt><dd>{relation.reason_codes.map((code) => presentCode(code, "ограничение")).join(", ") || "—"}</dd></div><div><dt>Группа наблюдений</dt><dd>{safeText(relation.cohort_fingerprint)}</dd></div><div><dt>Ограничения</dt><dd>{relation.caveats.map((code) => presentCode(code, "ограничение")).join(", ") || "—"}</dd></div></dl></details>
                  </article>
                ))}
              </div>

              <section className="growth-action-panel" aria-labelledby="growth-mapping-title">
                 <h5 id="growth-mapping-title">Проверка точной связи</h5>
                 <p>Сервер заново проверит выбранный UUID, группу наблюдений и отпечаток варианта. Подписи нужны только для проверки человеком; автоматического сопоставления нет.</p>
                {currentSelector ? (
                  <>
                    <label className="growth-label" htmlFor="growth-relation-select">Как эта связь относится к цели?</label>
                    <select className="growth-input" id="growth-relation-select" value={mappingRelation} disabled={busy !== null} onChange={(event) => { setMappingRelation(event.target.value as GrowthRelation | ""); setMappingReview(null); setMappingConfirmed(false); }}>
                       <option value="">Выбери связь</option>
                      <option value="supports_goal">Согласуется с целью</option>
                      <option value="conflicts_with_goal">Конфликтует с целью</option>
                      <option value="neutral_or_unknown">Нейтрально или неизвестно</option>
                    </select>
                     <div className="growth-action-row"><button className="growth-button growth-button-secondary" type="button" disabled={!mappingRelation || busy !== null} aria-busy={busy === "mapping-review"} onClick={() => void startMappingReview()}>{busy === "mapping-review" ? "Проверяю…" : "Уточнить связь — показать проверку"}</button></div>
                  </>
                 ) : <p className="growth-muted">Для смешанных, изменившихся, недостаточных и несопоставимых состояний нет двоичного варианта для автоматического выбора. Можно только сохранить безопасное состояние.</p>}
                 {activeMapping ? <p className="growth-inline-state">Для этого точного варианта уже действует связь: {RELATION_LABELS[activeMapping.relation]}. Новая запись возможна только через новую проверку.</p> : null}
                {mappingReview ? (
                  <div className="growth-review" aria-labelledby="growth-review-title">
                     <h6 id="growth-review-title">Проверка текущего источника</h6>
                    <p className="growth-review-goal">{mappingReview.goal_text}</p>
                    <dl className="growth-fields"><div><dt>Домен цели</dt><dd>{mappingReview.goal_domain}</dd></div><div><dt>Ситуация</dt><dd>{mappingReview.situation}</dd></div><div><dt>Известная информация</dt><dd>{mappingReview.information_known_at_decision_time}</dd></div><div><dt>Текущий вариант</dt><dd>{selectedOptionLabel(mappingReview)}</dd></div></dl>
                    <p className="growth-review-note">Критерии: {mappingReview.criteria.join(" · ") || "—"}</p>
                    <p className="growth-review-note">Все варианты: {mappingReview.ordered_options.map((item) => `${item.option_index}: ${item.label}`).join(" · ")}</p>
                    <p className="growth-review-note">Предлагаемая связь: {mappingReview.proposed_relation ? RELATION_LABELS[mappingReview.proposed_relation] : "не задана"}</p>
                     <details className="growth-technical"><summary>Отпечатки для повторной проверки</summary><dl className="growth-fields growth-fields-technical"><div><dt>Отпечаток цели</dt><dd>{mappingReview.goal.claim_fingerprint}</dd></div><div><dt>Отпечаток предлагаемого сопоставления</dt><dd>{safeText(mappingReview.candidate_mapping_fingerprint)}</dd></div><div><dt>Отпечаток варианта</dt><dd>{mappingReview.selected_option.option_fingerprint}</dd></div></dl></details>
                     <label className="growth-confirm-label"><input type="checkbox" checked={mappingConfirmed} disabled={busy !== null || sameMappingAlreadyActive} onChange={(event) => setMappingConfirmed(event.target.checked)} />Я проверил цель, точный контекст и вариант и подтверждаю эту связь.</label>
                    <button className="growth-button growth-button-primary" type="button" disabled={!mappingConfirmed || busy !== null || sameMappingAlreadyActive} aria-busy={busy === "mapping-confirm"} onClick={() => void confirmMapping()}>{busy === "mapping-confirm" ? "Подтверждаю…" : sameMappingAlreadyActive ? "Эта связь уже действует" : "Подтвердить связь"}</button>
                  </div>
                ) : null}
                 {mappingAcceptedId ? <p className="growth-success" role="status">Принято: {mappingAcceptedId}. История не изменяется, добавляется новая запись.</p> : null}
                 <details className="growth-technical"><summary>Хранилище жизненного цикла сопоставлений</summary>{mappingStatus?.mappings.length ? <ul className="growth-lifecycle-list">{mappingStatus.mappings.map((item) => <li key={item.mapping_id}><span>{lifecycleLabel(item.lifecycle_state)}</span><span>{item.mapping_id}</span><span>{RELATION_LABELS[item.relation]}</span></li>)}</ul> : <p>Записей сопоставления пока нет.</p>}</details>
              </section>

              <section className="growth-action-panel" aria-labelledby="growth-learning-title">
                 <h5 id="growth-learning-title">Уточнение развития</h5>
                 <p>Один вопрос по явному запросу, без вызова провайдера и без автоматической записи. Советник и уточнение не вызывают друг друга.</p>
                 <button className="growth-button growth-button-secondary" type="button" disabled={busy !== null || learningCandidate !== null || !learningRequest} aria-busy={busy === "learning"} onClick={() => void requestLearning()}>{busy === "learning" ? "Проверяю…" : "Уточнить развитие — задать один вопрос"}</button>
                {learningResult?.status === "no_candidate" ? <p className="growth-muted">{NO_CANDIDATE_COPY[learningResult.no_candidate_code ?? ""] ?? "Сейчас вопрос не нужен."}</p> : null}
                {learningCandidate ? (
                  <div className="growth-question">
                    <p className="growth-eyebrow">{presentCode(learningCandidate.reason_code, "Вопрос для уточнения")}</p>
                    <h6>{learningCandidate.question}</h6>
                    <p className="growth-review-note">Вопрос действует ограниченное время и будет заново проверен сервером перед любым ответом.</p>
                    <textarea className="growth-input" rows={4} value={learningAnswer} aria-label="Ответ на вопрос для уточнения" placeholder="Ответ нужен только для явного действия «Ответить»" onChange={(event) => setLearningAnswer(event.target.value)} />
                    <div className="growth-action-row"><button className="growth-button growth-button-primary" type="button" disabled={learningButtonDisabled || !learningAnswer.trim()} onClick={() => void resolveLearning("answer")}>Ответить</button><button className="growth-button growth-button-secondary" type="button" disabled={learningButtonDisabled} onClick={() => void resolveLearning("review")}>Проверить связь</button><button className="growth-button growth-button-quiet" type="button" disabled={learningButtonDisabled} onClick={() => void resolveLearning("ignore")}>Игнорировать</button><button className="growth-button growth-button-quiet" type="button" disabled={learningButtonDisabled} onClick={() => void resolveLearning("reject")}>Отклонить</button></div>
                  </div>
                ) : null}
                 {learningResolution?.handoff?.kind === "relation_review" ? <p className="growth-handoff" ref={mappingHandoffRef} tabIndex={-1}>Уточнение передало только точный выбор. Выбери связь выше и запусти проверку вручную.</p> : null}
              </section>

              {memoryDraft ? (
                <section className="growth-action-panel growth-memory-panel" aria-labelledby="growth-memory-title">
                  <h5 id="growth-memory-title">Ответ → проверка в личной памяти</h5>
                  <p>Ответ уточнения не является канонической памятью. Сначала отредактируй текст и пройди существующие проверки и безопасное сохранение.</p>
                  <div className="growth-memory-fields"><label className="growth-label" htmlFor="growth-memory-draft-title">Название</label><input className="growth-input" id="growth-memory-draft-title" value={memoryDraft.title} disabled={busy !== null || memorySaved !== null} onChange={(event) => updateMemoryDraft("title", event.target.value)} /><label className="growth-label" htmlFor="growth-memory-draft-content">Содержание ответа</label><textarea className="growth-input" id="growth-memory-draft-content" rows={8} value={memoryDraft.content} disabled={busy !== null || memorySaved !== null} onChange={(event) => updateMemoryDraft("content", event.target.value)} /></div>
                  {!memoryReviewToken ? <button className="growth-button growth-button-secondary" type="button" disabled={busy !== null || !memoryDraft.title.trim() || !memoryDraft.content.trim()} aria-busy={busy === "memory"} onClick={() => void reviewMemoryDraft()}>{busy === "memory" ? "Проверяю…" : "Проверить ответ"}</button> : null}
                  {memoryReviewToken ? <div className="growth-memory-review"><h6>Метаданные личной памяти</h6><p>Ничего не классифицируется автоматически. Выбери каждое значение сам.</p><PersonalMemoryMetadataFields idPrefix="growth-personal-memory" disabled={busy !== null || memorySaved !== null} values={memoryFields} onEvidenceKindChange={(value) => setMemoryFields((current) => ({ ...current, evidenceKind: value }))} onSelfKindChange={(value) => setMemoryFields((current) => ({ ...current, selfKind: value }))} onTimeModeChange={(value: PersonalMemoryTimeMode) => setMemoryFields((current) => ({ ...current, timeMode: value, evidenceAt: value === "exact" ? "" : "unknown" }))} onEvidenceAtChange={(value) => setMemoryFields((current) => ({ ...current, evidenceAt: value }))} onDomainChange={(value) => setMemoryFields((current) => ({ ...current, domain: value }))} onNow={() => setMemoryFields((current) => ({ ...current, timeMode: "exact", evidenceAt: new Date().toISOString() }))} /><label className="growth-confirm-label"><input type="checkbox" checked={memoryConfirmed} disabled={busy !== null || memorySaved !== null} onChange={(event) => { setMemoryConfirmed(event.target.checked); setMemoryPlan(null); }} />Я проверил метаданные и понимаю, что следующее подтверждение создаст каноническое свидетельство.</label><div className="growth-action-row"><button className="growth-button growth-button-secondary" type="button" disabled={busy !== null || memorySaved !== null} aria-busy={busy === "memory"} onClick={() => void prepareMemory()}>{busy === "memory" ? "Готовлю…" : "Подготовить безопасное сохранение"}</button>{memoryPlan ? <button className="growth-button growth-button-primary" type="button" disabled={busy !== null || memorySaved !== null} aria-busy={busy === "memory"} onClick={() => void saveMemory()}>{busy === "memory" ? "Сохраняю…" : "Подтвердить сохранение"}</button> : null}</div>{memoryPlan ? <details className="growth-technical" open><summary>Список изменений до записи</summary><pre>{memoryPlan.diff}</pre></details> : null}{memorySaved ? <p className="growth-success" role="status">Личная память создана: {memorySaved.note.id}</p> : null}</div> : null}
                </section>
              ) : null}

              <section className="growth-action-panel growth-advisor-panel" aria-labelledby="growth-advisor-title">
                <h5 id="growth-advisor-title">Независимая рекомендация / анализ</h5>
                <p>Советник получает только явно введённые данные и текущую точную цель. Развитие, этап 9, этап 10 и память автоматически не передаются.</p>
                <div className="growth-memory-fields"><label className="growth-label" htmlFor="growth-advisor-task">Задача для советника</label><textarea className="growth-input" id="growth-advisor-task" rows={3} value={advisorTask} disabled={busy !== null} onChange={(event) => updateAdvisor({ field: "task", text: event.target.value })} /><label className="growth-label" htmlFor="growth-advisor-options">Варианты (по одному на строку, необязательно)</label><textarea className="growth-input" id="growth-advisor-options" rows={3} value={advisorOptions} disabled={busy !== null} onChange={(event) => updateAdvisor({ field: "options", text: event.target.value })} /><label className="growth-label" htmlFor="growth-advisor-constraints">Ограничения (по одному на строку)</label><textarea className="growth-input" id="growth-advisor-constraints" rows={3} value={advisorConstraints} disabled={busy !== null} onChange={(event) => updateAdvisor({ field: "constraints", text: event.target.value })} /><label className="growth-label" htmlFor="growth-advisor-facts">Явные факты (по одному на строку)</label><textarea className="growth-input" id="growth-advisor-facts" rows={3} value={advisorFacts} disabled={busy !== null} onChange={(event) => updateAdvisor({ field: "facts", text: event.target.value })} /><label className="growth-label" htmlFor="growth-advisor-background">Явный фон (по одному на строку)</label><textarea className="growth-input" id="growth-advisor-background" rows={3} value={advisorBackground} disabled={busy !== null} onChange={(event) => updateAdvisor({ field: "background", text: event.target.value })} /></div>
                <button className="growth-button growth-button-secondary" type="button" disabled={busy !== null || !selectedGoalUuid || !advisorTask.trim()} aria-busy={busy === "advisor"} onClick={() => void createAdvisorPreview()}>{busy === "advisor" ? "Готовлю…" : "Показать предпросмотр цели"}</button>
                {advisorPreview ? <div className="growth-advisor-preview"><h6>Точная цель, которая будет передана советнику</h6><p>{advisorPreview.goal_text}</p><label className="growth-confirm-label"><input type="checkbox" checked={advisorConfirmed} disabled={busy !== null} onChange={(event) => setAdvisorConfirmed(event.target.checked)} />Я проверил цель и явно разрешаю один независимый вызов советника.</label><div className="growth-action-row"><button className="growth-button growth-button-primary" type="button" disabled={!advisorConfirmed || busy !== null} aria-busy={busy === "advisor"} onClick={() => void executeAdvisor()}>Выполнить независимый анализ</button><button className="growth-button growth-button-quiet" type="button" disabled={busy !== "advisor"} onClick={() => { advisorController.current?.abort(); advisorSequence.current += 1; setBusy(null); setStatus("Операция советника отменена; вызов провайдера не продолжается на клиенте."); }}>Отменить</button></div></div> : null}
                {advisorBranch ? <div className="growth-advisor-result" role="status"><p className="growth-state-chip">{advisorBranch.state === "abstention" ? "Безопасное воздержание советника" : advisorBranch.state === "error" ? "Советник завершился ошибкой" : "Результат советника"}</p>{advisorBranch.error ? <p>{advisorBranch.error.message}</p> : null}{advisorText.map((item, index) => <p key={`${item}-${index}`}>{item}</p>)}<p className="growth-review-note">Временный результат не изменяет развитие, память или прогноз.</p></div> : null}
              </section>
            </section>
          ) : selectedGoal ? <p className="growth-empty">Цель выбрана. Построй результат развития отдельной кнопкой, чтобы увидеть только текущий результат, сформированный сервером.</p> : null}
          {selectedGoal ? <GoalProgressSurface key={selectedGoalUuid} selectedGoal={selectedGoal} /> : null}
        </>
      )}
    </section>
  );
}
