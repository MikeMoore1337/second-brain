import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  acceptPersonalPlanning,
  buildPersonalPlanningContext,
  editPersonalPlanning,
  generatePersonalPlanning,
  loadPersonalPlanningState,
  type PlanningContextResponse,
  type PlanningGenerateResponse,
  type PlanningGoalProjection,
  type PlanningItem,
  type PlanningPlan,
  type PlanningProposal,
  type PlanningStateResponse,
  type PlanningWindow,
} from "./personal-planning-api";
import { Icon } from "./icons";
import { presentError } from "./presentation";

type Operation = "state" | "context" | "generate" | "accept" | "edit";

const ITEM_KIND_LABELS: Readonly<Record<string, string>> = {
  project: "Проект",
  milestone: "Рубеж",
  commitment: "Обязательство",
  next_action: "Следующее действие",
  hold: "Пауза",
};

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

function todayLocal(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function addDays(value: string, days: number): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function datesBetween(start: string, end: string): string[] {
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(start) || !/^\d{4}-\d{2}-\d{2}$/u.test(end)) return [];
  const first = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime()) || last < first) return [];
  const result: string[] = [];
  for (let cursor = first; cursor <= last; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    result.push(cursor.toISOString().slice(0, 10));
    if (result.length > 31) return [];
  }
  return result;
}

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function orderedItems(items: readonly PlanningItem[], order: readonly string[]): PlanningItem[] {
  const byId = new Map(items.map((item) => [item.item_id, item]));
  const ids = [...order, ...items.map((item) => item.item_id)];
  const seen = new Set<string>();
  return ids.flatMap((id) => {
    const item = byId.get(id);
    if (!item || seen.has(id)) return [];
    seen.add(id);
    return [item];
  });
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="personal-planning-error" role="alert" tabIndex={-1} ref={ref}><Icon name="error" size={18} aria-hidden="true" />{message}</p> : null;
}

function GoalCard({
  goal,
  selected,
  disabled,
  onToggle,
}: {
  readonly goal: PlanningGoalProjection;
  readonly selected: boolean;
  readonly disabled: boolean;
  readonly onToggle: () => void;
}): ReactElement {
  const strategy = goal.strategy_snapshot;
  return (
    <label className="personal-planning-goal" data-selected={selected} data-eligible={Boolean(strategy)}>
      <input type="checkbox" checked={selected} disabled={disabled || !strategy} onChange={onToggle} />
      <span className="personal-planning-goal-copy">
        <strong>{goal.goal_text}</strong>
        <span>{strategy ? `${strategy.selected_actions.length} выбранных связей со стратегией` : "Сначала прими стратегию для этой цели"}</span>
      </span>
      {strategy ? <span className="personal-planning-goal-mark" aria-hidden="true"><Icon name="success" size={18} /></span> : null}
    </label>
  );
}

function ItemKind({ item }: { readonly item: PlanningItem }): ReactElement {
  return <span className="personal-planning-kind"><Icon name={item.kind === "hold" ? "pause" : "growth"} size={17} aria-hidden="true" />{ITEM_KIND_LABELS[item.kind] ?? "Элемент плана"}</span>;
}

function PlanProvenance({ plan }: { readonly plan: PlanningPlan }): ReactElement {
  return (
    <details className="personal-planning-provenance">
      <summary>Показать происхождение текущего плана</summary>
      <dl>
        <div><dt>Идентификатор плана</dt><dd>{plan.plan_id}</dd></div>
        <div><dt>Версия</dt><dd>{plan.revision}</dd></div>
        <div><dt>Отпечаток контекста</dt><dd>{plan.source_pack_fingerprint}</dd></div>
        <div><dt>Отпечаток предложения</dt><dd>{plan.proposal_fingerprint}</dd></div>
        <div><dt>Отпечаток плана</dt><dd>{plan.plan_fingerprint}</dd></div>
      </dl>
    </details>
  );
}

export function PersonalPlanningSurface(): ReactElement {
  const [state, setState] = useState<PlanningStateResponse | null>(null);
  const [selectedGoalIds, setSelectedGoalIds] = useState<readonly string[]>([]);
  const [goalOrder, setGoalOrder] = useState<readonly string[]>([]);
  const [startLocal, setStartLocal] = useState(todayLocal);
  const [endLocal, setEndLocal] = useState(() => addDays(todayLocal(), 6));
  const [timezone, setTimezone] = useState(localTimezone);
  const [dailyMinutes, setDailyMinutes] = useState("60");
  const [windowsText, setWindowsText] = useState("");
  const [constraints, setConstraints] = useState("");
  const [planningContext, setPlanningContext] = useState("");
  const [contextResult, setContextResult] = useState<PlanningContextResponse | null>(null);
  const [proposalResult, setProposalResult] = useState<PlanningGenerateResponse | null>(null);
  const [proposalOrder, setProposalOrder] = useState<readonly string[]>([]);
  const [selectedItemIds, setSelectedItemIds] = useState<readonly string[]>([]);
  const [currentPlan, setCurrentPlan] = useState<PlanningPlan | null>(null);
  const [planDraft, setPlanDraft] = useState<readonly PlanningItem[]>([]);
  const [planOrder, setPlanOrder] = useState<readonly string[]>([]);
  const [planSelectedIds, setPlanSelectedIds] = useState<readonly string[]>([]);
  const [busy, setBusy] = useState<Operation | null>(null);
  const [status, setStatus] = useState("Нажми «Загрузить состояние», когда будешь готов открыть текущие цели.");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);

  const eligibleGoals = state?.goals.filter((goal) => goal.strategy_snapshot !== null) ?? [];
  const selectedGoals = goalOrder.filter((id) => selectedGoalIds.includes(id));
  const proposal = proposalResult?.proposal ?? null;
  const proposalItems = proposal ? orderedItems(proposal.items, proposalOrder) : [];
  const draftItems = orderedItems(planDraft, planOrder);

  function begin(operation: Operation): AbortController {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setBusy(operation);
    setError("");
    return next;
  }

  function finish(request: AbortController): void {
    if (controller.current !== request) return;
    controller.current = null;
    setBusy(null);
  }

  function fail(caught: unknown, fallback: string, request: AbortController): void {
    if (request.signal.aborted || controller.current !== request) return;
    setError(presentError(caught, fallback));
    setStatus("");
  }

  function applyPlan(plan: PlanningPlan | null): void {
    setCurrentPlan(plan);
    setPlanDraft(plan?.items ?? []);
    setPlanSelectedIds(plan?.selected_item_ids ?? []);
    setPlanOrder(plan?.item_order ?? []);
  }

  async function refresh(): Promise<void> {
    const request = begin("state");
    setState(null);
    setContextResult(null);
    setProposalResult(null);
    setSelectedGoalIds([]);
    setGoalOrder([]);
    setStatus("Загружаю только текущие цели и принятый план; провайдер пока не вызывается…");
    try {
      const next = await loadPersonalPlanningState(fetch, request.signal);
      if (controller.current !== request) return;
      setState(next);
      applyPlan(next.current_plan);
      setStatus(next.eligible_goal_count > 0 ? "Цели с принятой стратегией готовы к явному выбору." : "Нет целей с принятой стратегией для планирования.");
    } catch (caught) {
      fail(caught, "Текущее состояние личного планирования недоступно.", request);
    } finally {
      finish(request);
    }
  }

  function toggleGoal(id: string): void {
    if (busy) return;
    setSelectedGoalIds((current) => {
      if (current.includes(id)) {
        setGoalOrder((order) => order.filter((item) => item !== id));
        return current.filter((item) => item !== id);
      }
      setGoalOrder((order) => order.includes(id) ? order : [...order, id]);
      return [...current, id];
    });
    setContextResult(null);
    setProposalResult(null);
  }

  function moveGoal(id: string, direction: -1 | 1): void {
    setGoalOrder((current) => {
      const next = [...current];
      const index = next.indexOf(id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setContextResult(null);
    setProposalResult(null);
  }

  function parseWindows(): { readonly windows: readonly PlanningWindow[]; readonly error: string } {
    const lines = windowsText.split(/\r?\n/u).map((line) => line.trim()).filter(Boolean);
    const windows = lines.map((line, index) => {
      const [title, start, end] = line.split("|").map((item) => item.trim());
      return { window_id: `window-${index + 1}`, kind: "fixed_commitment", title, start_local: start, end_local: end };
    });
    if (windows.some((window) => !window.title || !window.start_local || !window.end_local)) {
      return { windows: [], error: "Окно должно быть записано как «название | начало | конец» на отдельной строке." };
    }
    return { windows, error: "" };
  }

  async function buildContext(): Promise<void> {
    if (!state || selectedGoals.length === 0) {
      setError("Загрузи состояние и выбери хотя бы одну цель с принятой стратегией.");
      return;
    }
    const dates = datesBetween(startLocal, endLocal);
    const minutes = Number(dailyMinutes);
    const parsedWindows = parseWindows();
    if (dates.length === 0 || !Number.isInteger(minutes) || minutes < 0 || minutes > 1440) {
      setError("Укажи горизонт до 31 дня и доступное время от 0 до 1440 минут.");
      return;
    }
    if (parsedWindows.error) {
      setError(parsedWindows.error);
      return;
    }
    const request = begin("context");
    setContextResult(null);
    setProposalResult(null);
    setStatus("Собираю детерминированный контекстный пакет без провайдера…");
    try {
      const next = await buildPersonalPlanningContext({
        goal_source_uuids: selectedGoals,
        start_local: startLocal,
        end_local: endLocal,
        timezone,
        capacity: dates.map((date) => ({ date, available_minutes: minutes })),
        fixed_windows: parsedWindows.windows,
        planning_constraints: constraints.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean).slice(0, 8),
        planning_context: planningContext.trim(),
      }, fetch, request.signal);
      if (controller.current !== request) return;
      setContextResult(next);
      applyPlan(next.current_plan);
      setStatus("Контекст готов. Открой точный предпросмотр и только затем запроси предложение.");
    } catch (caught) {
      fail(caught, "Не удалось собрать контекст личного плана.", request);
    } finally {
      finish(request);
    }
  }

  async function generate(): Promise<void> {
    if (!contextResult || busy) return;
    const request = begin("generate");
    setProposalResult(null);
    setStatus("Передаю существующему независимому совету только проверенный предпросмотр…");
    try {
      const next = await generatePersonalPlanning(contextResult.context_pack, contextResult.provider_preview, fetch, request.signal);
      if (controller.current !== request) return;
      setProposalResult(next);
      const order = next.proposal.suggested_order.length > 0 ? next.proposal.suggested_order : next.proposal.items.map((item) => item.item_id);
      setProposalOrder(order);
      setSelectedItemIds(next.proposal.items.map((item) => item.item_id));
      applyPlan(next.current_plan);
      setStatus(next.proposal.result_state === "proposal" ? "Предложение готово. Выбери элементы и проверь их порядок перед принятием." : "Совет вернул безопасный результат без исполнимого предложения.");
    } catch (caught) {
      fail(caught, "Не удалось построить предложение личного плана.", request);
    } finally {
      finish(request);
    }
  }

  function toggleProposalItem(id: string): void {
    setSelectedItemIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function moveProposalItem(id: string, direction: -1 | 1): void {
    setProposalOrder((current) => {
      const next = [...current];
      const index = next.indexOf(id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  async function accept(): Promise<void> {
    if (!contextResult || !proposal || selectedItemIds.length === 0 || busy) {
      if (proposal && selectedItemIds.length === 0) setError("Выбери хотя бы один элемент предложения.");
      return;
    }
    const request = begin("accept");
    setStatus("Повторно проверяю происхождение и добавляю новую версию плана в операционную историю…");
    try {
      const next = await acceptPersonalPlanning(
        contextResult.context_pack,
        proposal,
        selectedItemIds,
        proposalOrder.filter((id) => selectedItemIds.includes(id)),
        uuidv7(),
        currentPlan?.plan_fingerprint ?? null,
        new Date().toISOString(),
        fetch,
        request.signal,
      );
      if (controller.current !== request) return;
      applyPlan(next.plan);
      setProposalResult(null);
      setStatus("План принят. Он остаётся неисполняемым: здесь не создаются задачи, события или внешние действия.");
    } catch (caught) {
      fail(caught, "Не удалось принять личный план.", request);
    } finally {
      finish(request);
    }
  }

  function updatePlanItem(id: string, patch: Partial<PlanningItem>): void {
    setPlanDraft((current) => current.map((item) => item.item_id === id ? { ...item, ...patch } : item));
  }

  function togglePlanItem(id: string): void {
    setPlanSelectedIds((current) => {
      if (current.includes(id)) {
        setPlanOrder((order) => order.filter((item) => item !== id));
        return current.filter((item) => item !== id);
      }
      setPlanOrder((order) => order.includes(id) ? order : [...order, id]);
      return [...current, id];
    });
  }

  function movePlanItem(id: string, direction: -1 | 1): void {
    setPlanOrder((current) => {
      const next = [...current];
      const index = next.indexOf(id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  async function saveEdit(): Promise<void> {
    if (!currentPlan || planSelectedIds.length === 0 || busy) {
      if (currentPlan && planSelectedIds.length === 0) setError("Выбери хотя бы один элемент текущего плана.");
      return;
    }
    const request = begin("edit");
    setStatus("Проверяю изменения владельца и добавляю новую версию текущего плана…");
    try {
      const next = await editPersonalPlanning(
        planDraft,
        planSelectedIds,
        planOrder.filter((id) => planSelectedIds.includes(id)),
        uuidv7(),
        currentPlan.plan_fingerprint,
        new Date().toISOString(),
        fetch,
        request.signal,
      );
      if (controller.current !== request) return;
      applyPlan(next.plan);
      setStatus("Изменения плана сохранены как новая версия без перезаписи истории; внешних действий не запущено.");
    } catch (caught) {
      fail(caught, "Не удалось сохранить изменения личного плана.", request);
    } finally {
      finish(request);
    }
  }

  function cancel(): void {
    controller.current?.abort();
    controller.current = null;
    setBusy(null);
    setStatus("Операция отменена.");
  }

  return (
    <section id="personal-planning" className="stage7-surface signal-plane personal-planning-surface" aria-labelledby="personal-planning-title" aria-busy={busy !== null}>
      <header className="personal-planning-heading">
        <div>
          <h2 id="personal-planning-title">Личное планирование</h2>
          <p>Собери портфель целей, проверь точный горизонт и прими только те элементы плана, которые действительно готов взять на себя.</p>
        </div>
        <div className="personal-planning-heading-meta"><span><Icon name="growth" size={18} aria-hidden="true" /> Только владелец</span><span><Icon name="info" size={18} aria-hidden="true" /> Без автоисполнения</span></div>
      </header>

      <aside className="personal-planning-boundary"><Icon name="info" size={20} aria-hidden="true" /><p>План — это проверяемая операционная договорённость с самим собой. Он хранится вне хранилища заметок, не создаёт задачи или события и не отправляет внешние сообщения. Провайдер видит только точный предпросмотр после отдельного нажатия.</p></aside>

      <div className="personal-planning-toolbar">
        <button className="personal-planning-button personal-planning-button-secondary" type="button" onClick={() => void refresh()} disabled={busy !== null}><Icon name="refresh" size={18} aria-hidden="true" />{busy === "state" ? "Загружаю…" : "Загрузить состояние"}</button>
        {busy ? <button className="personal-planning-button personal-planning-button-secondary" type="button" onClick={cancel}>Отменить операцию</button> : null}
      </div>
      <p className="personal-planning-live" role="status" aria-live="polite">{status}</p>
      <ErrorMessage message={error} />

      <div className="personal-planning-builder">
        <section className="personal-planning-panel" aria-labelledby="planning-goals-title">
          <div className="personal-planning-panel-heading"><div><h3 id="planning-goals-title">1. Портфель целей</h3><p>Выбирай только цели с принятой стратегией. Порядок здесь станет порядком портфеля в контексте.</p></div><span className="personal-planning-count">{selectedGoals.length} выбрано</span></div>
          <div className="personal-planning-goals">
            {state ? state.goals.map((goal) => <GoalCard key={goal.goal_source_uuid} goal={goal} selected={selectedGoalIds.includes(goal.goal_source_uuid)} disabled={busy !== null} onToggle={() => toggleGoal(goal.goal_source_uuid)} />) : <p className="personal-planning-muted">Сначала загрузи состояние владельца.</p>}
          </div>
          {selectedGoals.length > 1 ? <ol className="personal-planning-order" aria-label="Порядок выбранных целей">{selectedGoals.map((id, index) => <li key={id}><span>{state?.goals.find((goal) => goal.goal_source_uuid === id)?.goal_text ?? id}</span><button className="personal-planning-icon-button" type="button" aria-label={`Поднять цель ${index + 1}`} onClick={() => moveGoal(id, -1)} disabled={index === 0 || busy !== null}><Icon name="collapse" size={16} aria-hidden="true" /></button><button className="personal-planning-icon-button" type="button" aria-label={`Опустить цель ${index + 1}`} onClick={() => moveGoal(id, 1)} disabled={index === selectedGoals.length - 1 || busy !== null}><Icon name="expand" size={16} aria-hidden="true" /></button></li>)}</ol> : null}
        </section>

        <section className="personal-planning-panel" aria-labelledby="planning-horizon-title">
          <div className="personal-planning-panel-heading"><div><h3 id="planning-horizon-title">2. Горизонт и вместимость</h3><p>Минуты задаются на каждый день горизонта. Окна здесь не являются событиями календаря.</p></div><span className="personal-planning-state-chip">До 31 дня</span></div>
          <div className="personal-planning-input-grid">
            <label className="personal-planning-field"><span>Начало</span><input type="date" value={startLocal} onChange={(event) => { setStartLocal(event.target.value); setContextResult(null); setProposalResult(null); }} /></label>
            <label className="personal-planning-field"><span>Конец</span><input type="date" value={endLocal} onChange={(event) => { setEndLocal(event.target.value); setContextResult(null); setProposalResult(null); }} /></label>
            <label className="personal-planning-field"><span>Доступно минут в день</span><input type="number" min={0} max={1440} step={15} value={dailyMinutes} onChange={(event) => { setDailyMinutes(event.target.value); setContextResult(null); setProposalResult(null); }} /></label>
            <label className="personal-planning-field"><span>Часовой пояс</span><input value={timezone} maxLength={128} onChange={(event) => { setTimezone(event.target.value); setContextResult(null); setProposalResult(null); }} /></label>
          </div>
          <label className="personal-planning-field"><span>Фиксированные окна <small>необязательно · название | начало | конец</small></span><textarea rows={3} value={windowsText} onChange={(event) => { setWindowsText(event.target.value); setContextResult(null); setProposalResult(null); }} placeholder="Например: Семья | 2026-09-17T19:00 | 2026-09-17T20:30" /></label>
        </section>
      </div>

      <section className="personal-planning-panel personal-planning-context-form" aria-labelledby="planning-context-title">
        <div className="personal-planning-panel-heading"><div><h3 id="planning-context-title">3. Контекст владельца</h3><p>Эти поля влияют на детерминированный пакет и остаются под твоим контролем.</p></div></div>
        <div className="personal-planning-input-grid"><label className="personal-planning-field"><span>Ограничения <small>по одному на строку, максимум 8</small></span><textarea rows={4} value={constraints} onChange={(event) => { setConstraints(event.target.value); setContextResult(null); setProposalResult(null); }} placeholder="Например: не перегружать будни" /></label><label className="personal-planning-field"><span>Дополнительный контекст <small>необязательно</small></span><textarea rows={4} value={planningContext} onChange={(event) => { setPlanningContext(event.target.value); setContextResult(null); setProposalResult(null); }} placeholder="Что важно учесть при распределении внимания?" /></label></div>
        <div className="personal-planning-actions"><button className="personal-planning-button personal-planning-button-primary" type="button" onClick={() => void buildContext()} disabled={busy !== null || selectedGoals.length === 0}><Icon name="relation" size={18} aria-hidden="true" />{busy === "context" ? "Собираю пакет…" : "Собрать контекстный пакет"}</button></div>
      </section>

      {contextResult ? <section className="personal-planning-panel" aria-labelledby="planning-preview-title"><div className="personal-planning-panel-heading"><div><h3 id="planning-preview-title">4. Точный предпросмотр</h3><p>Сервер перепроверит тот же пакет перед вызовом существующего совета.</p></div><span className="personal-planning-state-chip" data-state={contextResult.context_pack.readiness}>{contextResult.context_pack.readiness === "exact_current" ? "Актуально" : "Есть оговорки"}</span></div><details className="personal-planning-preview"><summary>Показать канонические байты запроса</summary><pre>{contextResult.provider_preview.canonical_json}</pre><dl><div><dt>Хэш байтов</dt><dd>{contextResult.provider_preview.canonical_bytes_sha256}</dd></div><div><dt>Отпечаток пакета</dt><dd>{contextResult.context_pack.pack_fingerprint}</dd></div></dl></details><div className="personal-planning-actions"><button className="personal-planning-button personal-planning-button-primary" type="button" onClick={() => void generate()} disabled={busy !== null || contextResult.context_pack.readiness !== "exact_current"}><Icon name="relation" size={18} aria-hidden="true" />{busy === "generate" ? "Строю предложение…" : "Получить предложение"}</button></div></section> : null}

      {proposal ? <section className="personal-planning-panel personal-planning-proposal" aria-labelledby="planning-proposal-title"><div className="personal-planning-panel-heading"><div><h3 id="planning-proposal-title">5. Предложение для проверки</h3><p>Проверь формулировки, отметь элементы и расставь порядок. Ничего не исполняется автоматически.</p></div><span className="personal-planning-state-chip" data-state={proposal.result_state}>{proposal.result_state === "proposal" ? "Есть предложение" : "Без безопасного предложения"}</span></div>{proposal.reasons.length > 0 ? <div className="personal-planning-reasons"><strong>Почему такой результат</strong><ul>{proposal.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div> : null}{proposal.caveats.length > 0 ? <div className="personal-planning-caveats"><Icon name="warning" size={18} aria-hidden="true" /><ul>{proposal.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}</ul></div> : null}<ol className="personal-planning-items">{proposalItems.map((item, index) => <li key={item.item_id} className="personal-planning-item" data-selected={selectedItemIds.includes(item.item_id)}><div className="personal-planning-item-toolbar"><label><input type="checkbox" checked={selectedItemIds.includes(item.item_id)} onChange={() => toggleProposalItem(item.item_id)} /><span>Включить в план</span></label><ItemKind item={item} /><span className="personal-planning-position">Позиция {index + 1}</span><button className="personal-planning-icon-button" type="button" aria-label={`Поднять элемент ${index + 1}`} onClick={() => moveProposalItem(item.item_id, -1)} disabled={index === 0 || busy !== null}><Icon name="collapse" size={16} aria-hidden="true" /></button><button className="personal-planning-icon-button" type="button" aria-label={`Опустить элемент ${index + 1}`} onClick={() => moveProposalItem(item.item_id, 1)} disabled={index === proposalItems.length - 1 || busy !== null}><Icon name="expand" size={16} aria-hidden="true" /></button></div><strong>{item.title}</strong><p>{item.description}</p><dl className="personal-planning-item-meta"><div><dt>Трудоёмкость</dt><dd>{item.effort_minutes ? `${item.effort_minutes} мин` : "Не задаётся"}</dd></div><div><dt>Зависимости</dt><dd>{item.dependency_ids.length ? item.dependency_ids.join(", ") : "Нет"}</dd></div></dl></li>)}</ol><div className="personal-planning-actions"><button className="personal-planning-button personal-planning-button-primary" type="button" onClick={() => void accept()} disabled={busy !== null || selectedItemIds.length === 0}><Icon name="confirm" size={18} aria-hidden="true" />{busy === "accept" ? "Принимаю…" : "Принять выбранные элементы"}</button></div></section> : null}

      {currentPlan ? <section className="personal-planning-panel personal-planning-current" aria-labelledby="planning-current-title"><div className="personal-planning-panel-heading"><div><h3 id="planning-current-title">Текущий план · версия {currentPlan.revision}</h3><p>Владелец может менять описание, трудоёмкость, окна, зависимости, выбор и порядок. Происхождение и типы элементов неизменяемы.</p></div><span className="personal-planning-state-chip" data-state="current">Сохранён</span></div><div className="personal-planning-items">{draftItems.map((item, index) => { const selected = planSelectedIds.includes(item.item_id); const canEditEffort = item.kind === "commitment" || item.kind === "next_action"; return <article key={item.item_id} className="personal-planning-item" data-selected={selected}><div className="personal-planning-item-toolbar"><label><input type="checkbox" checked={selected} onChange={() => togglePlanItem(item.item_id)} /><span>Оставить в плане</span></label><ItemKind item={item} /><span className="personal-planning-position">Позиция {index + 1}</span><button className="personal-planning-icon-button" type="button" aria-label={`Поднять элемент текущего плана ${index + 1}`} onClick={() => movePlanItem(item.item_id, -1)} disabled={!selected || planOrder.indexOf(item.item_id) === 0 || busy !== null}><Icon name="collapse" size={16} aria-hidden="true" /></button><button className="personal-planning-icon-button" type="button" aria-label={`Опустить элемент текущего плана ${index + 1}`} onClick={() => movePlanItem(item.item_id, 1)} disabled={!selected || planOrder.indexOf(item.item_id) < 0 || planOrder.indexOf(item.item_id) === planSelectedIds.length - 1 || busy !== null}><Icon name="expand" size={16} aria-hidden="true" /></button></div><label className="personal-planning-field"><span>Название</span><input value={item.title} maxLength={256} onChange={(event) => updatePlanItem(item.item_id, { title: event.target.value })} disabled={busy !== null} /></label><label className="personal-planning-field"><span>Описание</span><textarea rows={3} value={item.description} maxLength={2048} onChange={(event) => updatePlanItem(item.item_id, { description: event.target.value })} disabled={busy !== null} /></label><div className="personal-planning-input-grid"><label className="personal-planning-field"><span>Трудоёмкость, минут</span><input type="number" min={canEditEffort ? 1 : 0} max={1440} value={item.effort_minutes} disabled={!canEditEffort || busy !== null} onChange={(event) => updatePlanItem(item.item_id, { effort_minutes: Number(event.target.value) })} /></label><label className="personal-planning-field"><span>Зависимости <small>через запятую</small></span><input value={item.dependency_ids.join(", ")} onChange={(event) => updatePlanItem(item.item_id, { dependency_ids: event.target.value.split(",").map((part) => part.trim()).filter(Boolean) })} disabled={busy !== null} /></label><label className="personal-planning-field"><span>Начало окна <small>необязательно</small></span><input type="datetime-local" value={item.target_start_local ?? ""} onChange={(event) => updatePlanItem(item.item_id, { target_start_local: event.target.value || null })} disabled={busy !== null} /></label><label className="personal-planning-field"><span>Конец окна <small>необязательно</small></span><input type="datetime-local" value={item.target_end_local ?? ""} onChange={(event) => updatePlanItem(item.item_id, { target_end_local: event.target.value || null })} disabled={busy !== null} /></label></div></article>; })}</div><div className="personal-planning-actions"><button className="personal-planning-button personal-planning-button-primary" type="button" onClick={() => void saveEdit()} disabled={busy !== null || planSelectedIds.length === 0}><Icon name="save" size={18} aria-hidden="true" />{busy === "edit" ? "Сохраняю…" : "Сохранить изменения плана"}</button></div><PlanProvenance plan={currentPlan} /></section> : <section className="personal-planning-empty"><Icon name="info" size={24} aria-hidden="true" /><p>Принятого плана пока нет. После принятия он появится здесь для осознанного редактирования.</p></section>}
    </section>
  );
}
