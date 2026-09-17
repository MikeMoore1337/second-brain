import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactElement } from "react";

import { Icon } from "./icons";
import {
  acceptPersonalAgentRun,
  abandonPersonalAgentRun,
  answerPersonalAgentRun,
  buildPersonalAgentContext,
  buildPersonalAgentRun,
  completePersonalAgentRun,
  confirmPersonalAgentAction,
  continuePersonalAgentRun,
  loadPersonalAgentState,
  pausePersonalAgentRun,
  preparePersonalAgentAction,
  reconcilePersonalAgentAction,
  resumePersonalAgentRun,
  reviewPersonalAgentRun,
  skipPersonalAgentRun,
  startPersonalAgentRun,
  supersedePersonalAgentRun,
  type AgentBuildResponse,
  type AgentContextResponse,
  type AgentMission,
  type AgentPlanningItem,
  type AgentProposal,
  type AgentRun,
  type AgentRunResponse,
  type AgentRunStep,
  type AgentStage19Capability,
  type AgentStateResponse,
  type AgentStep,
  type AgentPreparedAction,
} from "./personal-agent-api";
import { presentError } from "./presentation";

type BusyOperation = "state" | "context" | "build" | "review" | "accept" | "run" | null;

type MissionDraft = {
  readonly task: string;
  readonly constraints: string;
  readonly currentContext: string;
  readonly repository: string;
  readonly actionKind: string;
  readonly issueNumber: string;
};

const INITIAL_DRAFT: MissionDraft = {
  task: "",
  constraints: "",
  currentContext: "",
  repository: "",
  actionKind: "github.issue.create",
  issueNumber: "",
};

const TERMINAL_RUN_STATES = new Set(["completed", "abandoned", "superseded", "rejected"]);
const EXECUTABLE_ITEM_KINDS = new Set(["commitment", "next_action"]);

const RUN_STATE_LABELS: Readonly<Record<string, string>> = {
  proposal: "Предложение",
  accepted: "Принят владельцем",
  active: "Активен",
  waiting_owner: "Ждёт решения владельца",
  waiting_stage19: "Ждёт внешнего действия",
  paused: "На паузе",
  ready_to_complete: "Готов к завершению",
  completed: "Завершён",
  abandoned: "Остановлен",
  superseded: "Заменён новым запуском",
  rejected: "Отклонён",
};

const STEP_KIND_LABELS: Readonly<Record<string, string>> = {
  clarify: "Уточнение",
  checkpoint: "Проверка",
  stage19_action: "Внешнее действие",
  hold: "Ограничение",
};

const ACTION_LABELS: Readonly<Record<string, string>> = {
  "github.issue.create": "создать задачу GitHub",
  "github.issue.comment": "добавить комментарий в GitHub",
  "github.issue.set_state": "изменить состояние задачи GitHub",
};

function newUuidV7(): string {
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

function newOperationId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `personal-agent-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function splitLines(value: string): readonly string[] {
  return value.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textField(step: AgentStep, key: string): string {
  const value = step[key];
  return typeof value === "string" ? value : "";
}

function actionField(step: AgentStep): Record<string, unknown> | null {
  const value = step.action;
  return isRecord(value) ? value : null;
}

function actionTarget(action: Record<string, unknown>): string {
  const repository = typeof action.repository === "string" ? action.repository : "точная цель не указана";
  const issueNumber = typeof action.issue_number === "number" ? ` · задача #${action.issue_number}` : "";
  return `${repository}${issueNumber}`;
}

function stepDescription(step: AgentStep): string {
  if (step.kind === "clarify") return textField(step, "question");
  if (step.kind === "checkpoint") return textField(step, "summary");
  if (step.kind === "hold") return textField(step, "reason");
  const action = actionField(step);
  return action ? `${ACTION_LABELS[String(action.action_kind)] ?? "внешнее действие"} · ${actionTarget(action)}` : "Точная цель будет проверена перед подтверждением.";
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "время не указано";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "время не указано" : date.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function stateLabel(value: string | undefined): string {
  return value ? RUN_STATE_LABELS[value] ?? value : "Состояние не указано";
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  if (!message) return null;
  return <p className="personal-agent-error" role="alert" tabIndex={-1} ref={ref}><Icon name="error" size={18} aria-hidden="true" />{message}</p>;
}

function StatusPill({ state }: { readonly state: AgentStage19Capability["status"] }): ReactElement {
  const label = state === "ready" ? "Готова к отдельному подтверждению" : state === "credential_unavailable" ? "Доступ не настроен" : state === "disabled" ? "Выключена" : state;
  return <span className="personal-agent-status-pill" data-status={state}>{label}</span>;
}

function StepCard({
  step,
  index,
  total,
  draft,
  disabled,
  onDraft,
  onMove,
  onRemove,
}: {
  readonly step: AgentStep;
  readonly index: number;
  readonly total: number;
  readonly draft: AgentStep;
  readonly disabled: boolean;
  readonly onDraft: (value: string) => void;
  readonly onMove: (direction: -1 | 1) => void;
  readonly onRemove: () => void;
}): ReactElement {
  const action = actionField(step);
  const editable = step.kind === "clarify" ? "question" : step.kind === "checkpoint" ? "summary" : step.kind === "hold" ? "reason" : null;
  const editLabel = step.kind === "clarify" ? "Вопрос владельцу" : step.kind === "checkpoint" ? "Что проверить" : "Почему нужен останов";
  return (
    <li className="personal-agent-step" data-step-kind={step.kind}>
      <div className="personal-agent-step-index" aria-hidden="true">{index + 1}</div>
      <div className="personal-agent-step-body">
        <div className="personal-agent-step-heading">
          <div>
            <span className="personal-agent-step-kind">{STEP_KIND_LABELS[step.kind] ?? step.kind}</span>
            <strong>{stepDescription(step)}</strong>
          </div>
          <span className="personal-agent-step-id">{step.step_id}</span>
        </div>
        {step.kind === "clarify" && <p className="personal-agent-step-note">Причина: {textField(step, "reason")}. Формат ответа: {textField(step, "answer_shape") || "короткий ответ владельца"}.</p>}
        {step.kind === "stage19_action" && action && (
          <dl className="personal-agent-action-facts">
            <div><dt>Цель</dt><dd>{actionTarget(action)}</dd></div>
            <div><dt>Риск</dt><dd>Контролируемое изменение</dd></div>
            <div><dt>Семантика</dt><dd>{ACTION_LABELS[String(action.action_kind)] ?? "внешнее действие"}</dd></div>
            <div><dt>Обратимость</dt><dd>Определяется внешним шлюзом и показывается перед подтверждением</dd></div>
          </dl>
        )}
        {editable && (
          <label className="personal-agent-inline-field">
            <span>{editLabel}</span>
            <textarea value={textField(draft, editable)} onChange={(event) => onDraft(event.target.value)} disabled={disabled} rows={2} />
          </label>
        )}
        <div className="personal-agent-step-actions">
          <button type="button" className="personal-agent-quiet-button" onClick={() => onMove(-1)} disabled={disabled || index === 0} aria-label={`Переместить шаг ${index + 1} выше`}><Icon name="play" size={15} className="personal-agent-rotate-icon" />Выше</button>
          <button type="button" className="personal-agent-quiet-button" onClick={() => onMove(1)} disabled={disabled || index === total - 1} aria-label={`Переместить шаг ${index + 1} ниже`}><Icon name="play" size={15} />Ниже</button>
          <button type="button" className="personal-agent-quiet-button personal-agent-danger-button" onClick={onRemove} disabled={disabled || total <= 1}>Удалить шаг</button>
        </div>
      </div>
    </li>
  );
}

function RunStepCard({ item }: { readonly item: AgentRunStep }): ReactElement {
  const step = item.step;
  const action = actionField(step);
  return (
    <li className="personal-agent-run-step" data-step-state={item.state}>
      <div className="personal-agent-run-step-marker"><span>{step.position}</span></div>
      <div>
        <div className="personal-agent-step-heading">
          <div><span className="personal-agent-step-kind">{STEP_KIND_LABELS[step.kind] ?? step.kind}</span><strong>{stepDescription(step)}</strong></div>
          <span className="personal-agent-step-state">{stateLabel(item.state)}</span>
        </div>
        {step.kind === "stage19_action" && action && <p className="personal-agent-step-note">Цель: {actionTarget(action)}. Изменение GitHub не выполняется без отдельной подготовки и подтверждения.</p>}
        {item.owner_answer && <p className="personal-agent-step-note">Ответ владельца: {item.owner_answer}</p>}
        {item.resolution_note && <p className="personal-agent-step-note">Результат: {item.resolution_note}</p>}
      </div>
    </li>
  );
}

function PlanProvenance({
  plan,
  mission,
  context,
  proposal,
}: {
  readonly plan: AgentStateResponse["current_plan"];
  readonly mission: AgentMission | null;
  readonly context: AgentContextResponse | null;
  readonly proposal: AgentProposal | null;
}): ReactElement | null {
  if (!plan) return null;
  return (
    <details className="personal-agent-provenance">
      <summary>Показать происхождение и технические проверки</summary>
      <dl>
        <div><dt>Принятый план</dt><dd>{plan.plan_id} · версия {plan.revision}</dd></div>
        <div><dt>Отпечаток плана</dt><dd>{plan.plan_fingerprint}</dd></div>
        {mission && <div><dt>Отпечаток миссии</dt><dd>{context?.provider_preview.mission_fingerprint ?? "будет рассчитан сервером"}</dd></div>}
        {context && <div><dt>Отпечаток контекстного пакета</dt><dd>{context.provider_preview.context_pack_fingerprint}</dd></div>}
        {proposal && <div><dt>Отпечаток предложения</dt><dd>{proposal.proposal_fingerprint}</dd></div>}
      </dl>
    </details>
  );
}

export function PersonalAgentSurface(): ReactElement {
  const [agentState, setAgentState] = useState<AgentStateResponse | null>(null);
  const [draft, setDraft] = useState<MissionDraft>(INITIAL_DRAFT);
  const [selectedItemIds, setSelectedItemIds] = useState<readonly string[]>([]);
  const [mission, setMission] = useState<AgentMission | null>(null);
  const [contextResult, setContextResult] = useState<AgentContextResponse | null>(null);
  const [buildResult, setBuildResult] = useState<AgentBuildResponse | null>(null);
  const [proposal, setProposal] = useState<AgentProposal | null>(null);
  const [proposalDraft, setProposalDraft] = useState<readonly AgentStep[]>([]);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [prepared, setPrepared] = useState<AgentPreparedAction | null>(null);
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [skipReason, setSkipReason] = useState("");
  const [busy, setBusy] = useState<BusyOperation>("state");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("Загружаю только текущий принятый план; провайдер пока не вызывается…");
  const controller = useRef<AbortController | null>(null);

  const currentPlan = agentState?.current_plan ?? null;
  const executionById = useMemo(() => new Map((agentState?.execution_items ?? []).map((item) => [item.item_id, item.projection])), [agentState?.execution_items]);
  const executableItems = useMemo(() => (
    currentPlan?.items.filter((item) => EXECUTABLE_ITEM_KINDS.has(item.kind) && executionById.has(item.item_id)) ?? []
  ), [currentPlan, executionById]);
  const currentStep = run?.steps.find((item) => ["current", "waiting_owner", "waiting_stage19"].includes(item.state)) ?? null;
  const openRun = run !== null && !TERMINAL_RUN_STATES.has(run.state);
  const busyNow = busy !== null;

  function applyProposal(next: AgentProposal | null): void {
    setProposal(next);
    setProposalDraft(next?.steps ?? []);
  }

  async function invoke<T>(
    operation: Exclude<BusyOperation, null>,
    request: (signal: AbortSignal) => Promise<T>,
    onSuccess: (value: T) => void,
    fallback: string,
  ): Promise<void> {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setBusy(operation);
    setError("");
    try {
      const value = await request(nextController.signal);
      if (!nextController.signal.aborted && controller.current === nextController) onSuccess(value);
    } catch (caught) {
      if (!nextController.signal.aborted && controller.current === nextController) setError(presentError(caught, fallback));
    } finally {
      if (controller.current === nextController) {
        controller.current = null;
        setBusy(null);
      }
    }
  }

  async function refresh(): Promise<void> {
    await invoke("state", (signal) => loadPersonalAgentState(fetch, signal), (next) => {
      setAgentState(next);
      setRun(next.current_run);
      setMission(next.current_run?.mission ?? null);
      const allowed = new Set(next.current_plan?.selected_item_ids ?? []);
      const executable = new Set(next.current_plan?.items.filter((item) => EXECUTABLE_ITEM_KINDS.has(item.kind)).map((item) => item.item_id) ?? []);
      setSelectedItemIds(next.current_run?.mission.selected_items.map((item) => item.item_id) ?? [...allowed].filter((id) => executable.has(id)).slice(0, 16));
      setNotice(next.current_plan ? "План загружен. Выбери точные исполнимые элементы и сформулируй миссию." : "Принятый план недоступен: сначала создай и прими план в «Личном планировании».");
    }, "Текущее состояние агента недоступно.");
  }

  useEffect(() => {
    void refresh();
    return () => controller.current?.abort();
  }, []);

  function makeMission(): AgentMission | null {
    if (!currentPlan || !draft.task.trim() || selectedItemIds.length < 1 || selectedItemIds.length > 16) return null;
    const selectedItems = selectedItemIds.flatMap((itemId) => {
      const item = currentPlan.items.find((candidate) => candidate.item_id === itemId);
      const projection = executionById.get(itemId);
      if (!item || !projection) return [];
      return [{
        item_id: item.item_id,
        accepted_item_fingerprint: projection.accepted_item_fingerprint,
        item_kind: item.kind,
        goal_refs: item.goal_refs,
        action_refs: item.action_refs,
      }];
    });
    if (selectedItems.length !== selectedItemIds.length) return null;
    const needsIssue = draft.actionKind !== "github.issue.create";
    const parsedIssue = draft.issueNumber.trim() ? Number.parseInt(draft.issueNumber, 10) : null;
    if (draft.repository.trim() && (needsIssue !== (parsedIssue !== null && Number.isSafeInteger(parsedIssue) && parsedIssue > 0))) return null;
    return {
      contract_version: "personal-agent-mission-v1",
      mission_id: newUuidV7(),
      planning_snapshot_id: currentPlan.plan_id,
      planning_snapshot_fingerprint: currentPlan.plan_fingerprint,
      planning_policy_id: currentPlan.policy_id,
      planning_policy_fingerprint: currentPlan.policy_fingerprint,
      selected_items: selectedItems,
      task: draft.task.trim(),
      constraints: splitLines(draft.constraints).slice(0, 16),
      current_context: splitLines(draft.currentContext).slice(0, 8),
      external_targets: draft.repository.trim() ? [{ action_kind: draft.actionKind, repository: draft.repository.trim(), issue_number: needsIssue ? parsedIssue : null }] : [],
      created_at: new Date().toISOString(),
      reviewed_at: new Date().toISOString(),
    };
  }

  function toggleItem(itemId: string): void {
    if (busyNow || openRun) return;
    setSelectedItemIds((current) => current.includes(itemId) ? current.filter((id) => id !== itemId) : current.length >= 16 ? current : [...current, itemId]);
    setMission(null);
    setContextResult(null);
    setBuildResult(null);
    applyProposal(null);
  }

  async function buildContext(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (openRun) {
      setError("Сначала заверши, останови или замени текущий запуск.");
      return;
    }
    const nextMission = makeMission();
    if (!nextMission) {
      setError("Выбери 1–16 исполнимых элементов, укажи миссию и корректную точную внешнюю цель.");
      return;
    }
    setMission(nextMission);
    setContextResult(null);
    setBuildResult(null);
    applyProposal(null);
    setNotice("Собираю точный контекст принятого плана и выполнения без вызова провайдера…");
    await invoke("context", (signal) => buildPersonalAgentContext(nextMission, fetch, signal), (next) => {
      setContextResult(next);
      setNotice("Контекст собран. Проверь предпросмотр запроса для провайдера; только следующая кнопка вызовет советник.");
    }, "Не удалось собрать точный контекст агента.");
  }

  async function buildRun(): Promise<void> {
    if (!mission || !contextResult) {
      setError("Сначала собери точный контекст и проверь предпросмотр.");
      return;
    }
    setNotice("Строю предложение запуска по ровно тому предпросмотру, который ты подтвердил просмотром…");
    await invoke("build", (signal) => buildPersonalAgentRun(mission, contextResult.context_pack, contextResult.provider_preview, fetch, signal), (next) => {
      setBuildResult(next);
      applyProposal(next.proposal);
      setNotice("Предложение готово. До принятия запуска ничего не исполняется.");
    }, "Не удалось построить предложение запуска.");
  }

  function updateDraftStep(index: number, value: string): void {
    setProposalDraft((current) => current.map((step, stepIndex) => stepIndex === index ? { ...step, [step.kind === "clarify" ? "question" : step.kind === "checkpoint" ? "summary" : "reason"]: value } : step));
  }

  async function reviewSteps(nextSteps: readonly AgentStep[]): Promise<void> {
    if (!mission || !proposal) return;
    setProposalDraft(nextSteps);
    await invoke("review", (signal) => reviewPersonalAgentRun(mission, proposal, nextSteps, fetch, signal), (next) => {
      applyProposal(next.proposal);
      setNotice("Изменения запуска проверены. Новый отпечаток предложения нужно принять отдельно.");
    }, "Не удалось проверить изменения запуска.");
  }

  function moveStep(index: number, direction: -1 | 1): void {
    const target = index + direction;
    if (target < 0 || target >= proposalDraft.length) return;
    const next = [...proposalDraft];
    [next[index], next[target]] = [next[target], next[index]];
    void reviewSteps(next.map((step, position) => ({ ...step, position: position + 1 })));
  }

  function removeStep(index: number): void {
    if (proposalDraft.length <= 1) {
      setError("В запуске должен остаться хотя бы один шаг.");
      return;
    }
    void reviewSteps(proposalDraft.filter((_, position) => position !== index).map((step, position) => ({ ...step, position: position + 1 })));
  }

  async function saveDraftEdits(): Promise<void> {
    await reviewSteps(proposalDraft);
  }

  async function acceptRun(): Promise<void> {
    if (!mission || !proposal) return;
    await invoke("accept", (signal) => acceptPersonalAgentRun(mission, proposal, newOperationId(), fetch, signal), (next) => {
      setRun(next.run);
      applyProposal(null);
      setPrepared(null);
      setConfirmationToken(null);
      setNotice("Запуск принят. Теперь он продвигается только по явным действиям владельца.");
    }, "Не удалось принять запуск.");
  }

  function applyRunResponse(next: AgentRunResponse, message: string): void {
    setRun(next.run);
    setPrepared(null);
    setConfirmationToken(null);
    setNotice(message);
  }

  async function startRun(): Promise<void> {
    if (!mission || !run) return;
    await invoke("run", (signal) => startPersonalAgentRun(mission, run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Операция запущена. Следующий шаг показан владельцу; пакетной автоматизации нет."), "Не удалось запустить операцию.");
  }

  async function pauseRun(): Promise<void> {
    if (!mission || !run) return;
    await invoke("run", (signal) => pausePersonalAgentRun(mission, run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Операция поставлена на паузу."), "Не удалось поставить операцию на паузу.");
  }

  async function resumeRun(): Promise<void> {
    if (!mission || !run) return;
    await invoke("run", (signal) => resumePersonalAgentRun(mission, run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Операция продолжена. Проверь показанный текущий шаг."), "Не удалось продолжить операцию.");
  }

  async function continueRun(): Promise<void> {
    if (!mission || !run) return;
    await invoke("run", (signal) => continuePersonalAgentRun(mission, run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Переход выполнен только на следующий шаг операции."), "Не удалось продолжить текущий шаг.");
  }

  async function answerRun(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!mission || !run || !answer.trim()) {
      setError("Напиши ответ владельца перед сохранением.");
      return;
    }
    await invoke("run", (signal) => answerPersonalAgentRun(mission, run.run_id, answer.trim(), newOperationId(), fetch, signal), (next) => {
      setAnswer("");
      applyRunResponse(next, "Ответ сохранён. Следующий шаг не выполняется автоматически — проверь его отдельно.");
    }, "Не удалось сохранить ответ владельца.");
  }

  async function skipRun(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!mission || !run || !skipReason.trim()) {
      setError("Укажи причину пропуска текущего шага.");
      return;
    }
    await invoke("run", (signal) => skipPersonalAgentRun(mission, run.run_id, skipReason.trim(), newOperationId(), fetch, signal), (next) => {
      setSkipReason("");
      applyRunResponse(next, "Шаг отмечен пропущенным с указанной причиной.");
    }, "Не удалось пропустить текущий шаг.");
  }

  async function abandonRun(): Promise<void> {
    if (!run) return;
    await invoke("run", (signal) => abandonPersonalAgentRun(run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Операция остановлена владельцем."), "Не удалось остановить операцию.");
  }

  async function completeRun(): Promise<void> {
    if (!run) return;
    await invoke("run", (signal) => completePersonalAgentRun(run.run_id, newOperationId(), fetch, signal), (next) => applyRunResponse(next, "Операция завершена явно владельцем."), "Не удалось завершить операцию.");
  }

  async function supersedeRun(): Promise<void> {
    if (!run) return;
    await invoke("run", (signal) => supersedePersonalAgentRun(run.run_id, newOperationId(), fetch, signal), (next) => {
      applyRunResponse(next, "Текущая операция заменена. Собери новую миссию, если нужен другой контекст.");
      setContextResult(null);
      setBuildResult(null);
      applyProposal(null);
    }, "Не удалось заменить текущую операцию.");
  }

  async function prepareAction(): Promise<void> {
    if (!mission || !run) return;
    await invoke("run", (signal) => preparePersonalAgentAction(mission, run.run_id, newOperationId(), fetch, signal), (next) => {
      setRun(next.run);
      setPrepared(next.prepared);
      setConfirmationToken(next.confirmation_token);
      setNotice("Внешний шлюз подготовил точное действие. Прочитай предпросмотр: операция сама по себе ничего не изменяет.");
    }, "Не удалось подготовить внешнее действие.");
  }

  async function confirmAction(): Promise<void> {
    if (!mission || !run || !prepared || !confirmationToken) return;
    await invoke("run", (signal) => confirmPersonalAgentAction(mission, run.run_id, prepared, confirmationToken, newOperationId(), fetch, signal), (next) => {
      setRun(next.run);
      setPrepared(null);
      setConfirmationToken(null);
      setNotice("Внешний шлюз вернул результат. Проверь его и нажми «Продолжить», если готов перейти дальше.");
    }, "Не удалось подтвердить внешнее действие.");
  }

  async function reconcileAction(): Promise<void> {
    if (!mission || !run || !prepared) return;
    await invoke("run", (signal) => reconcilePersonalAgentAction(mission, run.run_id, prepared, newOperationId(), fetch, signal), (next) => {
      setRun(next.run);
      setPrepared(null);
      setConfirmationToken(null);
      setNotice("Проверка внешнего шлюза завершена без повторной отправки действия.");
    }, "Не удалось проверить результат внешнего действия.");
  }

  const selectedCount = selectedItemIds.length;
  const stage19Ready = agentState?.stage19.status === "ready";
  const canBuildContext = Boolean(currentPlan && selectedCount > 0 && draft.task.trim()) && !busyNow && !openRun;
  const canBuildRun = Boolean(contextResult && mission) && !busyNow && !openRun;
  const canStart = run?.state === "accepted" && !busyNow;
  const canPause = Boolean(run && ["active", "waiting_owner", "waiting_stage19"].includes(run.state) && !busyNow);
  const canResume = run?.state === "paused" && !busyNow;
  const canComplete = run?.state === "ready_to_complete" && !busyNow;
  const currentStepKind = currentStep?.step.kind;
  const needsAnswer = currentStepKind === "clarify" && currentStep?.state === "waiting_owner";
  const needsStage19 = currentStepKind === "stage19_action" && ["current", "waiting_stage19"].includes(currentStep?.state ?? "");
  const canContinue = Boolean(run && ((run.state === "active" && !currentStep) || (currentStep?.step.kind === "checkpoint" && currentStep.state !== "waiting_stage19"))) && !busyNow;

  return (
    <section className="personal-agent-surface" id="chief-of-staff" aria-labelledby="personal-agent-title" aria-busy={busyNow}>
      <div className="personal-agent-heading">
        <div>
          <p className="personal-agent-kicker">Личный агент · только по запросу владельца</p>
          <h2 id="personal-agent-title">Агент</h2>
          <p className="personal-agent-lead">Собери миссию из принятого плана, проверь точный контекст и вручную продвигай одну операцию. Никаких фоновых запусков, скрытых повторов и автоматических изменений GitHub.</p>
        </div>
        <div className="personal-agent-status-card">
          <span className="personal-agent-status-label">Внешний шлюз</span>
          <StatusPill state={agentState?.stage19.status ?? "проверяется"} />
          <span>{stage19Ready ? "Внешнее действие появится только после явной подготовки и отдельного подтверждения." : "Внешние действия сейчас недоступны; планирование и проверка операции остаются отдельными."}</span>
          <button type="button" className="personal-agent-secondary" onClick={() => void refresh()} disabled={busyNow}>Обновить состояние</button>
        </div>
      </div>

      <div className="personal-agent-principles" aria-label="Правила агента">
        <span>Точный принятый план</span><span>Предпросмотр до провайдера</span><span>Одна операция за раз</span><span>Каждый шаг вручную</span>
      </div>

      <div className="personal-agent-workspace">
        <form className="personal-agent-mission" onSubmit={(event) => void buildContext(event)}>
          <div className="personal-agent-panel-heading"><div><span className="personal-agent-eyebrow">Шаг 1 · миссия</span><h3>Что нужно провести через операцию?</h3></div><span className="personal-agent-count">Выбрано: {selectedCount}/16</span></div>
          {!currentPlan && <p className="personal-agent-empty"><Icon name="info" size={18} aria-hidden="true" />Принятый план пока не найден. Собери его в «Личном планировании», затем обнови состояние.</p>}
          {currentPlan && <fieldset className="personal-agent-plan-picker" disabled={busyNow || openRun}>
            <legend>Исполнимые элементы принятого плана</legend>
            {executableItems.length === 0 && <p className="personal-agent-empty">В текущем плане нет доступных элементов «Обязательство» или «Следующее действие».</p>}
            {executableItems.map((item) => {
              const projection = executionById.get(item.item_id);
              const selected = selectedItemIds.includes(item.item_id);
              return <label className="personal-agent-plan-item" data-selected={selected} key={item.item_id}><input type="checkbox" checked={selected} onChange={() => toggleItem(item.item_id)} /><span><strong>{item.title}</strong><small>{item.kind === "commitment" ? "Обязательство" : "Следующее действие"} · {item.effort_minutes} мин · состояние выполнения: {projection?.state ?? "нет состояния"}</small></span></label>;
            })}
          </fieldset>}
          <label className="personal-agent-field personal-agent-field-wide"><span>Миссия</span><textarea value={draft.task} onChange={(event) => setDraft((current) => ({ ...current, task: event.target.value }))} disabled={busyNow || openRun} placeholder="Например: подготовить проверяемый следующий шаг по выбранному обязательству" rows={3} maxLength={4096} required /></label>
          <label className="personal-agent-field"><span>Ограничения</span><textarea value={draft.constraints} onChange={(event) => setDraft((current) => ({ ...current, constraints: event.target.value }))} disabled={busyNow || openRun} placeholder="Одно ограничение на строку" rows={4} maxLength={8192} /></label>
          <label className="personal-agent-field"><span>Текущий контекст</span><textarea value={draft.currentContext} onChange={(event) => setDraft((current) => ({ ...current, currentContext: event.target.value }))} disabled={busyNow || openRun} placeholder="Что владельцу важно учесть сейчас" rows={4} maxLength={8192} /></label>

          <div className="personal-agent-external-fields personal-agent-field-wide">
            <div className="personal-agent-subheading"><span>Точная внешняя цель</span><small>Необязательно. Если указана, она войдёт в предпросмотр для провайдера.</small></div>
            <div className="personal-agent-field-grid">
              <label className="personal-agent-field"><span>Тип действия</span><select value={draft.actionKind} onChange={(event) => setDraft((current) => ({ ...current, actionKind: event.target.value }))} disabled={busyNow || openRun}><option value="github.issue.create">Создать задачу GitHub</option><option value="github.issue.comment">Добавить комментарий</option><option value="github.issue.set_state">Изменить состояние задачи</option></select></label>
              <label className="personal-agent-field"><span>Разрешённый репозиторий</span><select value={draft.repository} onChange={(event) => setDraft((current) => ({ ...current, repository: event.target.value }))} disabled={busyNow || openRun || !agentState?.stage19.repositories.length}><option value="">Без внешнего действия</option>{agentState?.stage19.repositories.map((repository) => <option value={repository} key={repository}>{repository}</option>)}</select></label>
              {draft.actionKind !== "github.issue.create" && <label className="personal-agent-field"><span>Номер задачи</span><input type="number" min="1" max="2147483647" inputMode="numeric" value={draft.issueNumber} onChange={(event) => setDraft((current) => ({ ...current, issueNumber: event.target.value }))} disabled={busyNow || openRun} required /></label>}
            </div>
          </div>
          <div className="personal-agent-form-actions personal-agent-field-wide"><button type="submit" className="personal-agent-primary" disabled={!canBuildContext}>{busy === "context" ? "Собираем контекст…" : "Собрать точный контекст"}</button><span>Провайдер не вызывается этой кнопкой.</span></div>
        </form>

        <aside className="personal-agent-explainer">
          <span className="personal-agent-eyebrow">Порядок работы</span>
          <h3>Решение остаётся у владельца</h3>
          <ol><li><strong>Выбери.</strong> Только элементы текущего принятого плана.</li><li><strong>Проверь.</strong> Контекст и точный запрос провайдеру до вызова.</li><li><strong>Отредактируй.</strong> Переставь, уточни или убери шаги.</li><li><strong>Продвигай.</strong> Каждый переход и GitHub-экшен отдельной кнопкой.</li></ol>
          <p>Операция сама по себе не меняет GitHub. Токен подтверждения и предпросмотр живут только в памяти этой страницы.</p>
        </aside>
      </div>

      {contextResult && <div className="personal-agent-context-panel">
        <div className="personal-agent-panel-heading"><div><span className="personal-agent-eyebrow">Шаг 2 · контекст</span><h3>Проверь, что увидит провайдер</h3></div><span className="personal-agent-readonly">Только чтение</span></div>
        <div className="personal-agent-context-grid"><div><span className="personal-agent-meta-label">Миссия</span><p>{mission?.task}</p></div><div><span className="personal-agent-meta-label">Выбранные элементы</span><p>{selectedCount} · план {currentPlan?.revision}</p></div><div><span className="personal-agent-meta-label">Внешние действия</span><p>{mission?.external_targets.length ? mission.external_targets.map((target) => `${ACTION_LABELS[target.action_kind] ?? target.action_kind} · ${target.repository}${target.issue_number ? ` #${target.issue_number}` : ""}`).join("; ") : "Не заявлены"}</p></div></div>
        <details className="personal-agent-preview"><summary>Показать точный предпросмотр для провайдера</summary><pre>{contextResult.provider_preview.canonical_json}</pre><p>Отпечаток канонических байтов: {contextResult.provider_preview.canonical_bytes_sha256}</p></details>
        <div className="personal-agent-panel-actions"><button type="button" className="personal-agent-primary" onClick={() => void buildRun()} disabled={!canBuildRun}>{busy === "build" ? "Строим предложение…" : "Построить предложение операции"}</button><span>Следующая кнопка — первый вызов провайдера; тело сверяется с этим предпросмотром на сервере.</span></div>
      </div>}

      {proposal && mission && <div className="personal-agent-proposal-panel">
        <div className="personal-agent-panel-heading"><div><span className="personal-agent-eyebrow">Шаг 3 · предложение</span><h3>Проверь линейную операцию</h3></div><span className="personal-agent-count">{proposalDraft.length} шагов</span></div>
        <p className="personal-agent-panel-lead">Порядок, формулировки и внешние действия видны до принятия. Внешний шлюз здесь только описан: он не выполняется.</p>
        <ol className="personal-agent-step-list">{proposalDraft.map((step, index) => <StepCard key={step.step_id} step={step} draft={proposalDraft[index] ?? step} index={index} total={proposalDraft.length} disabled={busyNow} onDraft={(value) => updateDraftStep(index, value)} onMove={(direction) => moveStep(index, direction)} onRemove={() => removeStep(index)} />)}</ol>
        <div className="personal-agent-panel-actions"><button type="button" className="personal-agent-secondary" onClick={() => void saveDraftEdits()} disabled={busyNow}>{busy === "review" ? "Проверяем изменения…" : "Сохранить правки шагов"}</button><button type="button" className="personal-agent-primary" onClick={() => void acceptRun()} disabled={busyNow}>{busy === "accept" ? "Принимаем…" : "Принять операцию"}</button><span>Принятие создаст одну текущую операцию и не запускает внешних действий.</span></div>
        <details className="personal-agent-provenance-inline"><summary>Техническая информация предложения</summary><p>Провайдер: {proposal.provider_fingerprint}. Политика: {proposal.provider_policy_id}. Отпечаток: {proposal.proposal_fingerprint}.</p></details>
      </div>}

      {run && <div className="personal-agent-run-panel" data-run-state={run.state}>
        <div className="personal-agent-panel-heading"><div><span className="personal-agent-eyebrow">Шаг 4 · текущая операция</span><h3>{stateLabel(run.state)}</h3></div><span className="personal-agent-run-id">Идентификатор {run.run_id}</span></div>
        <div className="personal-agent-run-summary"><div><span className="personal-agent-meta-label">Миссия</span><strong>{run.mission.task}</strong></div><div><span className="personal-agent-meta-label">Обновлён</span><strong>{formatTime(run.updated_at)}</strong></div><div><span className="personal-agent-meta-label">Прогресс</span><strong>{run.steps.filter((item) => ["completed", "skipped"].includes(item.state)).length}/{run.steps.length} шагов</strong></div></div>
        <ol className="personal-agent-run-list">{run.steps.map((item) => <RunStepCard key={item.step.step_id} item={item} />)}</ol>

        {currentStep && <div className="personal-agent-owner-needed" data-step-kind={currentStep.step.kind}><span className="personal-agent-eyebrow">Нужно действие владельца</span><h4>{STEP_KIND_LABELS[currentStep.step.kind] ?? "Текущий шаг"}</h4><p>{stepDescription(currentStep.step)}</p>
          {needsAnswer && <form onSubmit={(event) => void answerRun(event)} className="personal-agent-owner-form"><label className="personal-agent-field"><span>Ответ владельца</span><textarea value={answer} onChange={(event) => setAnswer(event.target.value)} rows={3} maxLength={4096} placeholder="Ответь на вопрос, чтобы продолжить этот шаг" disabled={busyNow} required /></label><button type="submit" className="personal-agent-primary" disabled={busyNow || !answer.trim()}>{busy === "run" ? "Сохраняем…" : "Сохранить ответ"}</button></form>}
          {needsStage19 && !prepared && <div className="personal-agent-stage19-gate"><p>Риск: контролируемое изменение. Цель и исходящая семантика будут показаны внешним шлюзом. Сначала подготовь точный предпросмотр.</p><button type="button" className="personal-agent-secondary" onClick={() => void prepareAction()} disabled={busyNow || !stage19Ready}>{busy === "run" ? "Готовим предпросмотр…" : "Подготовить внешнее действие"}</button></div>}
          {currentStep.step.kind === "checkpoint" && <button type="button" className="personal-agent-primary" onClick={() => void continueRun()} disabled={!canContinue}>{busy === "run" ? "Продолжаем…" : "Проверено · продолжить"}</button>}
          {currentStep.step.kind !== "clarify" && currentStep.step.kind !== "stage19_action" && <form onSubmit={(event) => void skipRun(event)} className="personal-agent-skip-form"><label className="personal-agent-field"><span>Причина пропуска</span><input value={skipReason} onChange={(event) => setSkipReason(event.target.value)} maxLength={4096} placeholder="Почему этот шаг не выполняем сейчас" disabled={busyNow} /></label><button type="submit" className="personal-agent-quiet-button" disabled={busyNow || !skipReason.trim()}>Пропустить текущий шаг</button></form>}
        </div>}

        {prepared && <div className="personal-agent-prepared-panel" data-risk="controlled_write"><div className="personal-agent-panel-heading"><div><span className="personal-agent-eyebrow">Внешний шлюз · точный предпросмотр</span><h4>{ACTION_LABELS[prepared.action_kind] ?? prepared.action_kind}</h4></div><span className="personal-agent-risk">Контролируемое изменение</span></div><p className="personal-agent-target">Цель: {actionTarget(prepared.target_safe_identity)}</p><pre>{prepared.preview}</pre><p>Обратимость: {prepared.reversibility}. Подготовлено до: {formatTime(prepared.expires_at)}.</p><div className="personal-agent-panel-actions"><button type="button" className="personal-agent-primary" onClick={() => void confirmAction()} disabled={!confirmationToken || busyNow}>{busy === "run" ? "Выполняем…" : "Подтвердить и выполнить"}</button><button type="button" className="personal-agent-secondary" onClick={() => void reconcileAction()} disabled={busyNow}>Проверить результат без повтора</button></div></div>}

        <div className="personal-agent-run-controls" aria-label="Управление операцией"><span className="personal-agent-eyebrow">Управление операцией</span>{canStart && <button type="button" className="personal-agent-primary" onClick={() => void startRun()}>Запустить операцию</button>}{canPause && <button type="button" className="personal-agent-secondary" onClick={() => void pauseRun()}>Пауза</button>}{canResume && <button type="button" className="personal-agent-primary" onClick={() => void resumeRun()}>Продолжить операцию</button>}{canContinue && !currentStep && <button type="button" className="personal-agent-primary" onClick={() => void continueRun()}>Продолжить следующий шаг</button>}{canComplete && <button type="button" className="personal-agent-primary" onClick={() => void completeRun()}>Завершить операцию</button>}{openRun && <><button type="button" className="personal-agent-quiet-button" onClick={() => void supersedeRun()}>Пересмотреть операцию</button><button type="button" className="personal-agent-danger-button personal-agent-quiet-button" onClick={() => void abandonRun()}>Остановить операцию</button></>}</div>
        <details className="personal-agent-provenance-inline"><summary>Показать технический след операции</summary><dl><div><dt>Контекст</dt><dd>{run.context_pack_fingerprint}</dd></div><div><dt>Предложение</dt><dd>{run.proposal_fingerprint}</dd></div><div><dt>Отпечаток снимка</dt><dd>{run.snapshot_fingerprint}</dd></div><div><dt>Квитанций внешнего шлюза</dt><dd>{run.receipt_refs.length}</dd></div></dl></details>
      </div>}

      <ErrorMessage message={error} />
      {notice && <p className="personal-agent-notice" role="status" aria-live="polite"><Icon name="info" size={18} aria-hidden="true" />{notice}</p>}
      <PlanProvenance plan={currentPlan} mission={mission} context={contextResult} proposal={proposal} />
      <div className="personal-agent-history"><span className="personal-agent-eyebrow">История операций</span>{agentState?.run_history.length ? <ul>{agentState.run_history.map((item) => <li key={`${item.run_id}-${item.revision}`}><strong>{stateLabel(item.state)}</strong><span>{item.run_id} · ревизия {item.revision}</span></li>)}</ul> : <p>История текущей операции появится после её принятия.</p>}</div>
    </section>
  );
}
