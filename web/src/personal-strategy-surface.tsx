import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  acceptPersonalStrategy,
  buildPersonalStrategyContext,
  generatePersonalStrategy,
  loadPersonalStrategyState,
  rejectPersonalStrategy,
  type PersonalStrategyContextResponse,
  type PersonalStrategyGenerateResponse,
  type PersonalStrategyStateResponse,
  type ReviewedAction,
  type StrategyCandidate,
  type StrategyContextPack,
  type StrategyGoal,
  type StrategyProposal,
  type StrategySnapshot,
} from "./personal-strategy-api";
import { Icon } from "./icons";
import { presentError } from "./presentation";

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  "goal.current": "Точная текущая цель",
  "growth.relation": "Текущая связь с развитием",
  "progress.current": "Текущий измеримый прогресс",
  "behavior.relation": "Связь с наблюдаемым поведением",
  "experiment.terminal": "Завершённый эксперимент",
  "adaptive_profile.active": "Активный адаптивный профиль",
  "calibration.caveat": "Оговорка к калибровке",
  "caller.task": "Задача владельца",
  "caller.constraints": "Ограничения владельца",
  "caller.context": "Контекст владельца",
};

const READINESS_LABELS: Readonly<Record<string, string>> = {
  exact_current: "Точный текущий срез",
  incomplete: "Контекст неполный",
  conflict: "Есть конфликт источников",
  source_changed: "Источник изменился",
  missing: "Источник отсутствует",
  stale: "Источник устарел",
  not_comparable: "Нельзя сопоставить",
  policy_mismatch: "Политика не совпала",
};

const RESULT_LABELS: Readonly<Record<string, string>> = {
  proposal: "Есть предложение для проверки",
  insufficient_context: "Недостаточно контекста",
  insufficient_evidence: "Недостаточно свидетельств",
  not_comparable: "Источники нельзя сопоставить",
  source_changed: "Источник изменился",
  conflicting_constraints: "Ограничения конфликтуют",
  hold_current_strategy: "Сохраняй текущую стратегию",
  provider_unavailable: "Независимый совет недоступен",
  provider_abstained: "Независимый совет воздержался",
};

const KIND_LABELS: Readonly<Record<string, string>> = {
  act: "Возможный шаг",
  investigate: "Проверка",
  clarify: "Уточнение",
  experiment_candidate: "Кандидат эксперимента",
  hold: "Пауза",
};

type Operation = "state" | "context" | "generate" | "reject" | "accept";

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

function splitLines(value: string, limit: number): string[] {
  return value.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean).slice(0, limit);
}

function labelFor(map: Readonly<Record<string, string>>, value: string, fallback: string): string {
  return map[value] ?? fallback;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function sameCandidate(left: StrategyCandidate, right: StrategyCandidate): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function orderedCandidates(proposal: StrategyProposal, order: readonly string[]): StrategyCandidate[] {
  const byId = new Map(proposal.candidates.map((candidate) => [candidate.action_id, candidate]));
  const ids = [...order, ...proposal.candidates.map((candidate) => candidate.action_id)];
  const seen = new Set<string>();
  return ids.flatMap((id) => {
    const candidate = byId.get(id);
    if (!candidate || seen.has(id)) return [];
    seen.add(id);
    return [candidate];
  });
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="personal-strategy-error" role="alert" tabIndex={-1} ref={ref}><Icon name="error" size={18} aria-hidden="true" />{message}</p> : null;
}

function GoalSummary({ goal }: { readonly goal: StrategyGoal }): ReactElement {
  return (
    <div className="personal-strategy-goal-summary">
      <Icon name="growth" size={26} aria-hidden="true" />
      <div>
        <strong>{goal.goal_text}</strong>
        <span>Точная цель · источник {goal.goal.source_note_uuid}</span>
      </div>
    </div>
  );
}

function SourceList({ pack }: { readonly pack: StrategyContextPack }): ReactElement {
  return (
    <div className="personal-strategy-sources">
      <div className="personal-strategy-section-title"><Icon name="relation" size={22} aria-hidden="true" /><h3>Источники контекста</h3></div>
      <ul>
        {pack.sources.map((source) => (
          <li key={source.alias} data-state={source.readiness}>
            <span className="personal-strategy-state-mark" aria-hidden="true">{source.readiness === "exact_current" ? "✓" : "!"}</span>
            <div>
              <strong>{SOURCE_LABELS[source.alias] ?? source.alias}</strong>
              <span>{labelFor(READINESS_LABELS, source.readiness, "Состояние не определено")}</span>
              <p>{source.summary}</p>
            </div>
          </li>
        ))}
      </ul>
      {pack.pack_caveats.length > 0 ? <div className="personal-strategy-caveats"><Icon name="warning" size={18} aria-hidden="true" /><ul>{pack.pack_caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}</ul></div> : null}
    </div>
  );
}

function SnapshotSummary({ snapshot, status }: { readonly snapshot: StrategySnapshot | null; readonly status: string }): ReactElement {
  if (!snapshot) return <p className="personal-strategy-muted">Принятого снимка для этой цели пока нет.</p>;
  const current = status === "current";
  return (
    <div className="personal-strategy-snapshot" data-state={current ? "current" : "stale"}>
      <div className="personal-strategy-section-title"><Icon name={current ? "success" : "warning"} size={22} aria-hidden="true" /><h3>{current ? "Принятый снимок актуален" : "Принятый снимок требует свежей проверки"}</h3></div>
      <p>{snapshot.selected_actions.length} выбранных предложений · принят {formatTime(snapshot.accepted_at)}.</p>
      <details>
        <summary>Показать происхождение снимка</summary>
        <dl className="personal-strategy-provenance">
          <div><dt>Идентификатор</dt><dd>{snapshot.snapshot_id}</dd></div>
          <div><dt>Отпечаток цели</dt><dd>{snapshot.goal_identity_fingerprint}</dd></div>
          <div><dt>Отпечаток контекста</dt><dd>{snapshot.source_pack_fingerprint}</dd></div>
          <div><dt>Отпечаток снимка</dt><dd>{snapshot.snapshot_fingerprint}</dd></div>
        </dl>
      </details>
    </div>
  );
}

export function PersonalStrategySurface(): ReactElement {
  const [state, setState] = useState<PersonalStrategyStateResponse | null>(null);
  const [selectedGoalUuid, setSelectedGoalUuid] = useState("");
  const [task, setTask] = useState("");
  const [constraints, setConstraints] = useState("");
  const [currentContext, setCurrentContext] = useState("");
  const [contextResult, setContextResult] = useState<PersonalStrategyContextResponse | null>(null);
  const [proposalResult, setProposalResult] = useState<PersonalStrategyGenerateResponse | null>(null);
  const [candidateOrder, setCandidateOrder] = useState<readonly string[]>([]);
  const [candidateDrafts, setCandidateDrafts] = useState<Readonly<Record<string, StrategyCandidate>>>({});
  const [selectedActionIds, setSelectedActionIds] = useState<readonly string[]>([]);
  const [acceptedSnapshot, setAcceptedSnapshot] = useState<StrategySnapshot | null>(null);
  const [acceptedSnapshotStatus, setAcceptedSnapshotStatus] = useState("none");
  const [busy, setBusy] = useState<Operation | null>(null);
  const [status, setStatus] = useState("Нажми «Загрузить текущие цели», когда будешь готов показать серверный срез.");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);

  const selectedGoal = state?.goals.find((goal) => goal.goal.source_note_uuid === selectedGoalUuid) ?? null;
  const currentSnapshotEntry = state?.current_snapshots.find((item) => item.goal_source_uuid === selectedGoalUuid) ?? null;
  const candidates = proposalResult ? orderedCandidates(proposalResult.proposal, candidateOrder) : [];
  const selectedActions: ReviewedAction[] = candidates
    .filter((candidate) => selectedActionIds.includes(candidate.action_id))
    .map((candidate) => {
      const reviewed = candidateDrafts[candidate.action_id] ?? candidate;
      return { action_id: candidate.action_id, kind: candidate.kind, generated: candidate, reviewed, edited: !sameCandidate(candidate, reviewed) };
    });

  function invalidateDerived(): void {
    setContextResult(null);
    setProposalResult(null);
    setCandidateOrder([]);
    setCandidateDrafts({});
    setSelectedActionIds([]);
    setAcceptedSnapshot(null);
    setAcceptedSnapshotStatus("none");
  }

  function begin(operation: Operation): { readonly request: AbortController; readonly id: number } {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const id = ++sequence.current;
    setBusy(operation);
    setError("");
    return { request, id };
  }

  function isCurrent(request: AbortController, id: number): boolean {
    return !request.signal.aborted && controller.current === request && sequence.current === id;
  }

  function finish(request: AbortController, id: number): void {
    if (!isCurrent(request, id)) return;
    controller.current = null;
    setBusy(null);
  }

  function fail(caught: unknown, fallback: string, request: AbortController, id: number): void {
    if (isAbortError(caught) || !isCurrent(request, id)) return;
    setError(presentError(caught, fallback));
    setStatus("");
  }

  function refresh(): void {
    const { request, id } = begin("state");
    setState(null);
    setSelectedGoalUuid("");
    invalidateDerived();
    setStatus("Загружаю только текущие цели и принятые снимки; независимый совет ещё не вызывается…");
    void loadPersonalStrategyState(undefined, request.signal).then((next) => {
      if (!isCurrent(request, id)) return;
      setState(next);
      setStatus(next.goals.length > 0 ? "Цели готовы. Выбери одну точную цель, затем собери контекст явно." : "Точных текущих целей не найдено.");
    }).catch((caught) => fail(caught, "Текущие цели личной стратегии недоступны.", request, id)).finally(() => finish(request, id));
  }

  function selectGoal(value: string): void {
    if (busy) return;
    setSelectedGoalUuid(value);
    invalidateDerived();
    setError("");
    setStatus(value ? "Цель выбрана точно. Заполни задачу и отдельно собери контекст." : "Цель не выбрана.");
  }

  function editTask(value: string): void { setTask(value); invalidateDerived(); }
  function editConstraints(value: string): void { setConstraints(value); invalidateDerived(); }
  function editCurrentContext(value: string): void { setCurrentContext(value); invalidateDerived(); }

  function buildContext(): void {
    if (!selectedGoal) { setError("Сначала загрузи состояние и выбери одну точную цель."); return; }
    if (!task.trim()) { setError("Опиши задачу владельца перед сборкой контекста."); return; }
    const { request, id } = begin("context");
    setContextResult(null);
    setProposalResult(null);
    setStatus("Собираю контекстный пакет без провайдера из текущей цели и доступных источников…");
    void buildPersonalStrategyContext({
      goal_source_uuid: selectedGoal.goal.source_note_uuid,
      goal_identity_fingerprint: selectedGoal.goal_identity_fingerprint,
      task: task.trim(),
      constraints: splitLines(constraints, 8),
      current_context: currentContext.trim(),
    }, undefined, request.signal).then((next) => {
      if (!isCurrent(request, id)) return;
      setContextResult(next);
      setAcceptedSnapshot(next.accepted_snapshot);
      setAcceptedSnapshotStatus(next.accepted_snapshot_status);
    setStatus(next.context_pack.readiness === "exact_current" ? "Контекст готов. Проверь источники и точный предпросмотр перед вызовом независимого совета." : "Контекст собран с оговорками. Безопасный результат может быть отказом.");
    }).catch((caught) => fail(caught, "Не удалось собрать текущий контекст.", request, id)).finally(() => finish(request, id));
  }

  function generate(): void {
    if (!contextResult || busy) return;
    const { request, id } = begin("generate");
    setProposalResult(null);
    setSelectedActionIds([]);
    setCandidateDrafts({});
    setStatus("Вызываю существующий независимый совет только после явной проверки точного предпросмотра…");
    void generatePersonalStrategy(contextResult.context_pack, contextResult.provider_preview, undefined, request.signal).then((next) => {
      if (!isCurrent(request, id)) return;
      setProposalResult(next);
      setContextResult(next);
      setAcceptedSnapshot(next.accepted_snapshot);
      setAcceptedSnapshotStatus(next.accepted_snapshot_status);
      setCandidateOrder(next.proposal.suggested_order.length > 0 ? next.proposal.suggested_order : next.proposal.candidates.map((candidate) => candidate.action_id));
      setStatus(next.proposal.result_state === "proposal" ? "Предложение готово. Отредактируй и выбери действия, которые хочешь принять." : "Совет вернул безопасный результат без исполнимого предложения.");
    }).catch((caught) => fail(caught, "Личная стратегия сейчас недоступна.", request, id)).finally(() => finish(request, id));
  }

  function updateCandidate(candidate: StrategyCandidate, field: "title" | "description", value: string): void {
    setCandidateDrafts((current) => ({ ...current, [candidate.action_id]: { ...(current[candidate.action_id] ?? candidate), [field]: value } }));
  }

  function moveCandidate(actionId: string, direction: -1 | 1): void {
    setCandidateOrder((current) => {
      const next = [...current];
      const index = next.indexOf(actionId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function toggleCandidate(actionId: string): void {
    setSelectedActionIds((current) => current.includes(actionId) ? current.filter((item) => item !== actionId) : [...current, actionId]);
  }

  function reject(): void {
    if (!proposalResult || busy) return;
    const { request, id } = begin("reject");
    setStatus("Фиксирую отклонение предложения без записи его содержимого в память…");
    let operationId: string;
    try { operationId = uuidv7(); } catch { setError("Не удалось создать UUID операции. Повтори отклонение."); finish(request, id); return; }
    void rejectPersonalStrategy(proposalResult.proposal, operationId, undefined, request.signal).then(() => {
      if (!isCurrent(request, id)) return;
      setProposalResult(null);
      setCandidateOrder([]);
      setCandidateDrafts({});
      setSelectedActionIds([]);
      setStatus("Предложение отклонено. Никаких задач, событий или записей не создано.");
    }).catch((caught) => fail(caught, "Не удалось отклонить предложение.", request, id)).finally(() => finish(request, id));
  }

  function accept(): void {
    if (!contextResult || !proposalResult || selectedActions.length === 0 || busy) {
      if (proposalResult && selectedActions.length === 0) setError("Выбери хотя бы одно отредактированное или исходное предложение перед принятием.");
      return;
    }
    const { request, id } = begin("accept");
    setStatus("Повторно проверяю цель, источники и выбранные действия перед добавлением новой версии без изменения истории…");
    let operationId: string;
    try { operationId = uuidv7(); } catch { setError("Не удалось создать UUID операции. Повтори принятие."); finish(request, id); return; }
    void acceptPersonalStrategy(contextResult.context_pack, proposalResult.proposal, selectedActions, operationId, acceptedSnapshot, undefined, request.signal).then((next) => {
      if (!isCurrent(request, id)) return;
      setAcceptedSnapshot(next.snapshot);
      setAcceptedSnapshotStatus("current");
      setProposalResult(null);
      setCandidateOrder([]);
      setCandidateDrafts({});
      setSelectedActionIds([]);
      setContextResult((current) => current ? { ...current, accepted_snapshot: next.snapshot, accepted_snapshot_status: "current" } : current);
      setStatus("Снимок стратегии принят. Он не запускает задачи, календарь, внешние действия или обучение.");
    }).catch((caught) => fail(caught, "Не удалось принять снимок стратегии.", request, id)).finally(() => finish(request, id));
  }

  function cancel(): void {
    controller.current?.abort();
    controller.current = null;
    sequence.current += 1;
    setBusy(null);
    setStatus("Операция отменена.");
  }

  const proposal = proposalResult?.proposal ?? null;
  const providerPreview = contextResult?.provider_preview ?? null;

  return (
    <section id="personal-strategy" className="stage7-surface signal-plane personal-strategy-surface" aria-labelledby="personal-strategy-title" aria-busy={busy !== null}>
      <header className="personal-strategy-heading">
        <div>
          <h2 id="personal-strategy-title">Личная стратегия</h2>
          <p>Собери проверяемый срез твоей текущей цели, получи предложение и прими только то, что осознанно выберешь.</p>
        </div>
        <div className="personal-strategy-heading-meta"><span><Icon name="growth" size={18} aria-hidden="true" /> Только владелец</span><span><Icon name="info" size={18} aria-hidden="true" /> Без автоисполнения</span></div>
      </header>

      <aside className="personal-strategy-boundary"><Icon name="info" size={20} aria-hidden="true" /><p>Эта поверхность не создаёт задачи, события календаря, внешние действия или обучение. Браузер не хранит персональный срез; источник истины остаётся на серверной стороне и перепроверяется перед каждым важным действием.</p></aside>

      <div className="personal-strategy-toolbar">
        <button className="personal-strategy-button personal-strategy-button-secondary" type="button" onClick={refresh} disabled={busy !== null}><Icon name="refresh" size={18} aria-hidden="true" />{busy === "state" ? "Загружаю…" : "Загрузить текущие цели"}</button>
        {busy ? <button className="personal-strategy-button personal-strategy-button-secondary" type="button" onClick={cancel}>Отменить операцию</button> : null}
      </div>

      <p className="personal-strategy-live" role="status" aria-live="polite">{status}</p>
      <ErrorMessage message={error} />

      <div className="personal-strategy-form">
        <div className="personal-strategy-field">
          <label htmlFor="personal-strategy-goal">Точная текущая цель</label>
          <select id="personal-strategy-goal" value={selectedGoalUuid} onChange={(event) => selectGoal(event.target.value)} disabled={!state || busy !== null}>
            <option value="">Выбери одну цель явно</option>
            {state?.goals.map((goal) => <option key={goal.goal.source_note_uuid} value={goal.goal.source_note_uuid}>{goal.goal_text}</option>)}
          </select>
          <small>Сервер сверяет UUID и отпечаток; похожая или неявная цель не подходит.</small>
        </div>
        {selectedGoal ? <GoalSummary goal={selectedGoal} /> : null}

        <div className="personal-strategy-input-grid">
          <label className="personal-strategy-field"><span>Задача владельца</span><textarea value={task} onChange={(event) => editTask(event.target.value)} rows={4} maxLength={12000} placeholder="Что ты хочешь понять или решить по этой цели?" /></label>
          <label className="personal-strategy-field"><span>Текущий контекст <small>необязательно</small></span><textarea value={currentContext} onChange={(event) => editCurrentContext(event.target.value)} rows={4} maxLength={12000} placeholder="Какие обстоятельства важно учесть прямо сейчас?" /></label>
        </div>
        <label className="personal-strategy-field"><span>Ограничения <small>по одному на строку, максимум 8</small></span><textarea value={constraints} onChange={(event) => editConstraints(event.target.value)} rows={3} maxLength={12000} placeholder="Например: не предлагать внешние действия; не использовать непроверенные источники" /></label>
        <div className="personal-strategy-actions"><button className="personal-strategy-button personal-strategy-button-primary" type="button" onClick={buildContext} disabled={!selectedGoal || !task.trim() || busy !== null}><Icon name="relation" size={18} aria-hidden="true" />{busy === "context" ? "Собираю контекст…" : "Собрать текущий контекст"}</button></div>
      </div>

      {contextResult ? <section className="personal-strategy-panel" aria-labelledby="personal-strategy-context-title">
        <div className="personal-strategy-panel-heading"><div><h3 id="personal-strategy-context-title">Контекстный пакет</h3><p>Это срез без провайдера. Проверь готовность и источники до вызова независимого совета.</p></div><span className="personal-strategy-state-chip" data-state={contextResult.context_pack.readiness}>{labelFor(READINESS_LABELS, contextResult.context_pack.readiness, "Состояние не определено")}</span></div>
        <SourceList pack={contextResult.context_pack} />
        <SnapshotSummary snapshot={acceptedSnapshot ?? currentSnapshotEntry?.snapshot ?? null} status={acceptedSnapshotStatus || currentSnapshotEntry?.freshness || "none"} />
        {providerPreview ? <details className="personal-strategy-preview"><summary>Показать точный предпросмотр для совета</summary><p>Ниже показаны канонические байты, которые будут переданы существующему независимому совету только после отдельного нажатия.</p><pre>{providerPreview.canonical_json}</pre><dl className="personal-strategy-provenance"><div><dt>Хэш байтов</dt><dd>{providerPreview.canonical_bytes_sha256}</dd></div><div><dt>Отпечаток набора источников</dt><dd>{providerPreview.source_pack_fingerprint}</dd></div></dl></details> : null}
        <div className="personal-strategy-actions"><button className="personal-strategy-button personal-strategy-button-primary" type="button" onClick={generate} disabled={busy !== null}><Icon name="relation" size={18} aria-hidden="true" />{busy === "generate" ? "Строю предложение…" : "Получить независимый совет"}</button></div>
      </section> : null}

      {proposal ? <section className="personal-strategy-panel personal-strategy-proposal" aria-labelledby="personal-strategy-proposal-title">
        <div className="personal-strategy-panel-heading"><div><h3 id="personal-strategy-proposal-title">Предложение и проверка</h3><p>Кандидаты остаются неисполняемыми. Отредактируй формулировки, расставь порядок и отметь только выбранные.</p></div><span className="personal-strategy-state-chip" data-state={proposal.result_state}>{labelFor(RESULT_LABELS, proposal.result_state, "Результат требует проверки")}</span></div>
        {proposal.reasons.length > 0 ? <div className="personal-strategy-reason-block"><strong>Почему такой результат</strong><ul>{proposal.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div> : null}
        {proposal.caveats.length > 0 ? <div className="personal-strategy-caveats"><Icon name="warning" size={18} aria-hidden="true" /><ul>{proposal.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}</ul></div> : null}
        {proposal.candidates.length > 0 ? <ol className="personal-strategy-candidates">
          {candidates.map((candidate, index) => {
            const reviewed = candidateDrafts[candidate.action_id] ?? candidate;
            const checked = selectedActionIds.includes(candidate.action_id);
            return <li key={candidate.action_id} className="personal-strategy-candidate" data-selected={checked}>
              <div className="personal-strategy-candidate-toolbar"><label><input type="checkbox" checked={checked} onChange={() => toggleCandidate(candidate.action_id)} /><span>Включить в принятый снимок</span></label><span className="personal-strategy-order">Позиция {index + 1}</span><div className="personal-strategy-order-actions"><button className="personal-strategy-icon-button" type="button" aria-label={`Поднять предложение ${index + 1}`} onClick={() => moveCandidate(candidate.action_id, -1)} disabled={index === 0}><Icon name="collapse" size={16} aria-hidden="true" /></button><button className="personal-strategy-icon-button" type="button" aria-label={`Опустить предложение ${index + 1}`} onClick={() => moveCandidate(candidate.action_id, 1)} disabled={index === candidates.length - 1}><Icon name="expand" size={16} aria-hidden="true" /></button></div></div>
              <div className="personal-strategy-candidate-kind"><Icon name={candidate.kind === "hold" ? "pause" : "growth"} size={18} aria-hidden="true" />{KIND_LABELS[candidate.kind] ?? candidate.kind}</div>
              <label className="personal-strategy-field"><span>Название</span><input value={reviewed.title} onChange={(event) => updateCandidate(candidate, "title", event.target.value)} maxLength={240} /></label>
              <label className="personal-strategy-field"><span>Описание</span><textarea value={reviewed.description} onChange={(event) => updateCandidate(candidate, "description", event.target.value)} rows={3} maxLength={4000} /></label>
              <dl className="personal-strategy-candidate-meta"><div><dt>Связь с целью</dt><dd>{candidate.goal_relation}</dd></div><div><dt>Наблюдаемый сигнал</dt><dd>{candidate.expected_observable_signal}</dd></div></dl>
              {candidate.caveats.length > 0 ? <p className="personal-strategy-muted">Оговорки: {candidate.caveats.join(" · ")}</p> : null}
            </li>;
          })}
        </ol> : <div className="personal-strategy-empty"><Icon name="warning" size={28} aria-hidden="true" /><p>Исполнимых кандидатов нет. Это безопасный результат; не нужно превращать его в задачу вручную здесь.</p></div>}
        <div className="personal-strategy-actions"><button className="personal-strategy-button personal-strategy-button-primary" type="button" onClick={accept} disabled={busy !== null || selectedActions.length === 0}><Icon name="confirm" size={18} aria-hidden="true" />{busy === "accept" ? "Принимаю…" : "Принять выбранные действия"}</button><button className="personal-strategy-button personal-strategy-button-danger" type="button" onClick={reject} disabled={busy !== null}><Icon name="cancel" size={18} aria-hidden="true" />{busy === "reject" ? "Отклоняю…" : "Отклонить предложение"}</button></div>
      </section> : null}

    </section>
  );
}
