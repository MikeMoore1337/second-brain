import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";

import { presentError } from "./presentation";
import { Icon } from "./icons";
import {
  correctExecutionFeedback,
  loadExecutionCalibration,
  loadExecutionFeedback,
  loadExecutionFeedbackState,
  recordExecutionFeedbackEvent,
  type ExecutionCalibration,
  type ExecutionEffortPrecision,
  type ExecutionEventType,
  type ExecutionFeedbackReport,
  type ExecutionFeedbackStateResponse,
  type ExecutionHistoryEvent,
  type ExecutionItemState,
  type ExecutionPlanSummary,
  type ExecutionResultDisposition,
} from "./execution-feedback-api";

type DialogAction = {
  readonly item: ExecutionItemState;
  readonly eventType: ExecutionEventType;
};

type DialogCorrection = {
  readonly item: ExecutionItemState;
  readonly event: ExecutionHistoryEvent;
};

type ActionDraft = {
  occurredAt: string;
  effortPrecision: ExecutionEffortPrecision;
  actualEffortMinutes: string;
  actualResultNote: string;
  reasonCodes: string[];
  deviationCodes: string[];
  resultDisposition: ExecutionResultDisposition;
};

type CorrectionDraft = {
  occurredAt: string;
  reason: string;
};

const REASON_CODES = [
  ["dependency", "Зависимость"],
  ["missing_information", "Не хватает информации"],
  ["capacity", "Не хватило вместимости"],
  ["priority_change", "Изменился приоритет"],
  ["scope_change", "Изменился объём"],
  ["estimate_mismatch", "Оценка не совпала"],
  ["technical_problem", "Техническая проблема"],
  ["external_wait", "Ожидание внешнего ответа"],
  ["context_change", "Изменился контекст"],
  ["other", "Другая причина"],
  ["unknown", "Причина неизвестна"],
] as const;

const STATE_LABELS: Readonly<Record<string, string>> = {
  not_started: "Не начато",
  in_progress: "В работе",
  paused: "На паузе",
  blocked: "Заблокировано",
  completed: "Завершено",
  abandoned: "Прекращено",
};

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  current: "Актуальный источник",
  stale: "Источник изменился",
  unavailable: "Источник недоступен",
  superseded: "Исторический план",
};

const KIND_LABELS: Readonly<Record<string, string>> = {
  commitment: "Обязательство",
  next_action: "Следующее действие",
};

const EVENT_LABELS: Readonly<Record<string, string>> = {
  start: "Начато",
  pause: "Пауза",
  resume: "Возобновлено",
  block: "Заблокировано",
  unblock: "Блокировка снята",
  complete: "Завершено",
  abandon: "Прекращено",
  void: "Запись аннулирована",
};

const WINDOW_LABELS: Readonly<Record<string, string>> = {
  within_window: "В целевом окне",
  before_window: "Раньше целевого окна",
  after_window: "После целевого окна",
  no_window: "Целевое окно не задано",
  unknown: "Время нельзя сопоставить",
};

const DISPOSITION_LABELS: Readonly<Record<string, string>> = {
  as_planned: "По плану",
  with_changes: "С изменениями",
  partial: "Частично",
  unknown: "Неизвестно",
};

const CAVEAT_LABELS: Readonly<Record<string, string>> = {
  current_plan_source_changed: "Источник текущего плана изменился; новое начало закрыто до обновления плана.",
  current_accepted_plan_missing: "Актуальный принятый план не найден.",
  window_elapsed: "Целевое окно завершилось; состояние выполнения не изменялось автоматически.",
  actual_effort_unknown: "Неизвестное усилие не считается нулевым и не попадает в сопоставление времени.",
  no_comparable_effort: "Нет записей, где плановое и фактическое время можно сопоставить.",
  superseded_plan: "Исторический план доступен только для чтения.",
};

const EVENT_ACTIONS: Readonly<Record<ExecutionEventType, { readonly label: string; readonly title: string; readonly description: string }>> = {
  start: { label: "Начать", title: "Начать выполнение", description: "Зафиксируй, что это действие действительно началось." },
  pause: { label: "Пауза", title: "Поставить на паузу", description: "Сохрани явную паузу без изменения самого плана." },
  resume: { label: "Возобновить", title: "Возобновить выполнение", description: "Сохрани, что ты снова вернулся к этому действию." },
  block: { label: "Заблокировать", title: "Заблокировать выполнение", description: "Выбери нейтральную причину текущей блокировки." },
  unblock: { label: "Снять блокировку", title: "Снять блокировку", description: "Сохрани, что препятствие больше не удерживает действие." },
  complete: { label: "Завершить", title: "Завершить выполнение", description: "Отметь только то, что ты явно считаешь завершённым." },
  abandon: { label: "Прекратить выполнение", title: "Прекратить выполнение", description: "Закрой выполнение нейтральной отметкой и, если нужно, причиной." },
};

function stateLabel(value: string): string {
  return STATE_LABELS[value] ?? "Состояние не распознано";
}

function sourceLabel(value: string): string {
  return SOURCE_LABELS[value] ?? "Источник требует проверки";
}

function kindLabel(value: string): string {
  return KIND_LABELS[value] ?? "Пункт плана";
}

function eventLabel(value: string): string {
  return EVENT_LABELS[value] ?? "Событие выполнения";
}

function codeLabel(value: string): string {
  const match = REASON_CODES.find(([code]) => code === value);
  return match?.[1] ?? "Причина требует проверки";
}

function windowLabel(value: string): string {
  return WINDOW_LABELS[value] ?? "Время нельзя сопоставить";
}

function dispositionLabel(value: string | null): string {
  if (!value) return "Не указано";
  return DISPOSITION_LABELS[value] ?? "Результат требует проверки";
}

function caveatLabel(value: string): string {
  return CAVEAT_LABELS[value] ?? "Есть оговорка в данных выполнения.";
}

function formatInstant(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Время требует проверки";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatLocal(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " · ");
}

function formatMinutes(value: number | null): string {
  return value === null ? "Не указано" : `${value} мин`;
}

function snapshotKey(snapshot: ExecutionPlanSummary): string {
  return `${snapshot.planning_snapshot_id}:${snapshot.planning_snapshot_fingerprint}`;
}

function newOperationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `execution-ui-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

function newActionDraft(): ActionDraft {
  return {
    occurredAt: "",
    effortPrecision: "unknown",
    actualEffortMinutes: "",
    actualResultNote: "",
    reasonCodes: [],
    deviationCodes: [],
    resultDisposition: "unknown",
  };
}

function newCorrectionDraft(): CorrectionDraft {
  return { occurredAt: "", reason: "" };
}

function toggleCode(codes: readonly string[], code: string): string[] {
  return codes.includes(code) ? codes.filter((value) => value !== code) : [...codes, code];
}

function validateActionDraft(action: DialogAction, draft: ActionDraft): string | null {
  if (!draft.occurredAt.trim()) return "Укажи время события или нажми «Сейчас».";
  if (draft.effortPrecision === "exact") {
    const effort = Number(draft.actualEffortMinutes);
    if (!Number.isInteger(effort) || effort < 0 || effort > 1440) {
      return "Укажи целое усилие от 0 до 1440 минут или выбери «Не знаю».";
    }
  }
  if (action.eventType === "block" && draft.reasonCodes.length === 0) {
    return "Выбери хотя бы одну причину блокировки.";
  }
  if (action.eventType === "abandon" && draft.reasonCodes.length === 0 && !draft.actualResultNote.trim()) {
    return "Добавь причину или короткую заметку перед прекращением выполнения.";
  }
  if (draft.reasonCodes.length > 3 || draft.deviationCodes.length > 3) {
    return "Можно выбрать не больше трёх причин.";
  }
  return null;
}

function FocusDialog({
  title,
  description,
  children,
  onClose,
}: {
  readonly title: string;
  readonly description: string;
  readonly children: ReactElement;
  readonly onClose: () => void;
}): ReactElement {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const titleId = "execution-feedback-dialog-title";
  const descriptionId = "execution-feedback-dialog-description";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ));
    (focusable[0] ?? dialog).focus();
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const currentFocusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ));
      if (currentFocusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = currentFocusable[0];
      const last = currentFocusable[currentFocusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <div className="execution-dialog-layer" data-execution-dialog onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div
        ref={dialogRef}
        className="execution-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        <div className="execution-dialog-header">
          <div>
            <h3 id={titleId}>{title}</h3>
            <p id={descriptionId}>{description}</p>
          </div>
          <button type="button" className="execution-icon-button" aria-label="Закрыть форму" onClick={onClose}>
            <Icon name="close" size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function TimeField({
  value,
  onChange,
  disabled,
}: {
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly disabled: boolean;
}): ReactElement {
  return (
    <div className="execution-time-field">
      <label htmlFor="execution-event-time">Время события</label>
      <div className="execution-time-row">
        <input
          id="execution-event-time"
          className="execution-input"
          type="text"
          inputMode="text"
          autoComplete="off"
          placeholder="2026-09-16T12:30:00+03:00"
          value={value}
          required
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
        <button type="button" className="execution-button execution-button-secondary" disabled={disabled} onClick={() => onChange(new Date().toISOString())}>
          Сейчас
        </button>
      </div>
      <p className="execution-field-help">Значение видно до отправки. Используй RFC3339 с явным смещением.</p>
    </div>
  );
}

function ReasonPicker({
  label,
  codes,
  onToggle,
}: {
  readonly label: string;
  readonly codes: readonly string[];
  readonly onToggle: (code: string) => void;
}): ReactElement {
  return (
    <fieldset className="execution-choice-group">
      <legend>{label}</legend>
      <div className="execution-choice-list">
        {REASON_CODES.map(([code, text]) => (
          <label className="execution-choice" key={code}>
            <input type="checkbox" checked={codes.includes(code)} onChange={() => onToggle(code)} />
            <span>{text}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function ActionDialog({
  action,
  draft,
  error,
  busy,
  onChange,
  onSubmit,
  onClose,
}: {
  readonly action: DialogAction;
  readonly draft: ActionDraft;
  readonly error: string;
  readonly busy: boolean;
  readonly onChange: (draft: ActionDraft) => void;
  readonly onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  readonly onClose: () => void;
}): ReactElement {
  const terminal = action.eventType === "complete" || action.eventType === "abandon";
  const reasoned = action.eventType === "block" || action.eventType === "abandon";
  const copy = EVENT_ACTIONS[action.eventType];
  return (
    <FocusDialog title={copy.title} description={copy.description} onClose={onClose}>
      <form className="execution-dialog-form" onSubmit={onSubmit}>
        <TimeField value={draft.occurredAt} onChange={(occurredAt) => onChange({ ...draft, occurredAt })} disabled={busy} />
        {terminal ? (
          <fieldset className="execution-choice-group">
            <legend>Фактическое усилие</legend>
            <div className="execution-effort-mode">
              <label className="execution-choice">
                <input type="radio" name="execution-effort" checked={draft.effortPrecision === "unknown"} disabled={busy} onChange={() => onChange({ ...draft, effortPrecision: "unknown", actualEffortMinutes: "" })} />
                <span>Не знаю / не записал</span>
              </label>
              <label className="execution-choice">
                <input type="radio" name="execution-effort" checked={draft.effortPrecision === "exact"} disabled={busy} onChange={() => onChange({ ...draft, effortPrecision: "exact" })} />
                <span>Указать точно</span>
              </label>
            </div>
            {draft.effortPrecision === "exact" ? (
              <label className="execution-field" htmlFor="execution-effort-minutes">
                <span>Минуты, от 0 до 1440</span>
                <input id="execution-effort-minutes" className="execution-input" type="number" min="0" max="1440" step="1" value={draft.actualEffortMinutes} disabled={busy} onChange={(event) => onChange({ ...draft, actualEffortMinutes: event.target.value })} />
              </label>
            ) : null}
          </fieldset>
        ) : null}
        {terminal ? (
          <label className="execution-field" htmlFor="execution-result-disposition">
            <span>Как это соотносится с планом</span>
            <select id="execution-result-disposition" className="execution-input" value={draft.resultDisposition} disabled={busy} onChange={(event) => onChange({ ...draft, resultDisposition: event.target.value as ExecutionResultDisposition })}>
              <option value="as_planned">По плану</option>
              <option value="with_changes">С изменениями</option>
              <option value="partial">Частично</option>
              <option value="unknown">Не знаю</option>
            </select>
          </label>
        ) : null}
        {reasoned ? <ReasonPicker label={action.eventType === "block" ? "Причина блокировки" : "Причина прекращения (необязательно)"} codes={draft.reasonCodes} onToggle={(code) => onChange({ ...draft, reasonCodes: toggleCode(draft.reasonCodes, code) })} /> : null}
        {terminal ? <ReasonPicker label="Что изменилось относительно плана (необязательно)" codes={draft.deviationCodes} onToggle={(code) => onChange({ ...draft, deviationCodes: toggleCode(draft.deviationCodes, code) })} /> : null}
        {terminal || action.eventType === "block" ? (
          <label className="execution-field" htmlFor="execution-result-note">
            <span>{action.eventType === "block" ? "Короткая заметка (необязательно)" : "Короткая заметка о результате (необязательно)"}</span>
            <textarea id="execution-result-note" className="execution-input execution-textarea" maxLength={2048} value={draft.actualResultNote} disabled={busy} onChange={(event) => onChange({ ...draft, actualResultNote: event.target.value })} />
            <span className="execution-field-help">До 2048 символов. Это твоя операционная пометка, не оценка качества.</span>
          </label>
        ) : null}
        {error ? <p className="execution-dialog-error" role="alert">{error}</p> : null}
        <div className="execution-dialog-actions">
          <button type="button" className="execution-button execution-button-secondary" disabled={busy} onClick={onClose}>Отмена</button>
          <button type="submit" className={`execution-button ${action.eventType === "abandon" ? "execution-button-danger" : "execution-button-primary"}`} disabled={busy} aria-busy={busy}>
            <Icon name={action.eventType === "pause" ? "pause" : action.eventType === "start" || action.eventType === "resume" || action.eventType === "unblock" ? "play" : "confirm"} size={18} />
            {busy ? "Сохраняю…" : `Подтвердить: ${copy.label}`}
          </button>
        </div>
      </form>
    </FocusDialog>
  );
}

function CorrectionDialog({
  draft,
  error,
  busy,
  onChange,
  onSubmit,
  onClose,
}: {
  readonly draft: CorrectionDraft;
  readonly error: string;
  readonly busy: boolean;
  readonly onChange: (draft: CorrectionDraft) => void;
  readonly onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  readonly onClose: () => void;
}): ReactElement {
  return (
    <FocusDialog title="Исправить запись" description="Исходная запись останется в истории. Исправление добавит явную аннулирующую запись." onClose={onClose}>
      <form className="execution-dialog-form" onSubmit={onSubmit}>
        <TimeField value={draft.occurredAt} onChange={(occurredAt) => onChange({ ...draft, occurredAt })} disabled={busy} />
        <label className="execution-field" htmlFor="execution-correction-reason">
          <span>Почему запись нужно исправить</span>
          <textarea id="execution-correction-reason" className="execution-input execution-textarea" maxLength={2048} required value={draft.reason} disabled={busy} onChange={(event) => onChange({ ...draft, reason: event.target.value })} />
        </label>
        {error ? <p className="execution-dialog-error" role="alert">{error}</p> : null}
        <div className="execution-dialog-actions">
          <button type="button" className="execution-button execution-button-secondary" disabled={busy} onClick={onClose}>Отмена</button>
          <button type="submit" className="execution-button execution-button-danger" disabled={busy} aria-busy={busy}>{busy ? "Исправляю…" : "Добавить исправление"}</button>
        </div>
      </form>
    </FocusDialog>
  );
}

function ItemProvenance({ item }: { readonly item: ExecutionItemState }): ReactElement {
  return (
    <details className="execution-provenance">
      <summary>Показать точные связи</summary>
      <dl>
        <div><dt>Версия плана</dt><dd>{item.planning_plan_revision}</dd></div>
        <div><dt>Точный ID плана</dt><dd><code>{item.planning_snapshot_id}</code></dd></div>
        <div><dt>Отпечаток плана</dt><dd><code>{item.planning_snapshot_fingerprint}</code></dd></div>
        <div><dt>Точный ID элемента</dt><dd><code>{item.item_id}</code></dd></div>
        <div><dt>Отпечаток элемента</dt><dd><code>{item.accepted_item_fingerprint}</code></dd></div>
        <div><dt>Связей с целью</dt><dd>{item.goal_refs.length}</dd></div>
        <div><dt>Проверенных действий</dt><dd>{item.action_refs.length}</dd></div>
      </dl>
    </details>
  );
}

function History({
  item,
  onCorrect,
  readOnly,
}: {
  readonly item: ExecutionItemState;
  readonly onCorrect: (item: ExecutionItemState, event: ExecutionHistoryEvent) => void;
  readonly readOnly: boolean;
}): ReactElement | null {
  if (item.history.length === 0) return null;
  return (
    <details className="execution-history">
      <summary>История событий · {item.history.length}</summary>
      <ol>
        {item.history.map((event) => (
          <li className={`execution-history-row${event.voided ? " is-voided" : ""}`} key={event.event_id}>
            <div className="execution-history-main">
              <span className="execution-history-event">{eventLabel(event.event_type)}</span>
              <time dateTime={event.occurred_at}>{formatInstant(event.occurred_at)}</time>
              {event.voided ? <span className="execution-history-note">Аннулировано исправлением</span> : null}
              {event.correction ? <span className="execution-history-note">Исправление</span> : null}
            </div>
            {event.effort_precision === "exact" ? <span className="execution-history-detail">Усилие: {formatMinutes(event.actual_effort_minutes)}</span> : null}
            {event.result_disposition ? <span className="execution-history-detail">Результат: {dispositionLabel(event.result_disposition)}</span> : null}
            {event.reason_codes.length > 0 ? <span className="execution-history-detail">Причина: {event.reason_codes.map(codeLabel).join(", ")}</span> : null}
            {event.deviation_codes.length > 0 ? <span className="execution-history-detail">Отклонение: {event.deviation_codes.map(codeLabel).join(", ")}</span> : null}
            {event.actual_result_note ? <p className="execution-history-detail execution-history-note-text">{event.actual_result_note}</p> : null}
            {!readOnly && !event.voided && !event.correction && event.event_type !== "void" ? (
              <button type="button" className="execution-history-correct" onClick={() => onCorrect(item, event)}>Исправить запись</button>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

function ItemActions({
  item,
  readOnly,
  pendingAction,
  onAction,
}: {
  readonly item: ExecutionItemState;
  readonly readOnly: boolean;
  readonly pendingAction: string | null;
  readonly onAction: (item: ExecutionItemState, eventType: ExecutionEventType) => void;
}): ReactElement {
  if (readOnly) return <p className="execution-read-only-note">Историческая версия доступна только для чтения.</p>;
  const actions: ExecutionEventType[] = item.state === "not_started"
    ? item.source_status === "current" ? ["start"] : []
    : item.state === "in_progress"
      ? ["pause", "block", "complete", "abandon"]
      : item.state === "paused"
        ? ["resume", "block", "complete", "abandon"]
        : item.state === "blocked"
          ? ["unblock", "complete", "abandon"]
          : [];
  if (actions.length === 0) {
    return item.state === "not_started" && item.source_status !== "current"
      ? <p className="execution-action-hint">Начало закрыто: {sourceLabel(item.source_status).toLowerCase()}.</p>
      : <p className="execution-action-hint">Новых действий для этого состояния нет.</p>;
  }
  return (
    <div className="execution-actions">
      {actions.map((eventType, index) => {
        const action = EVENT_ACTIONS[eventType];
        const key = `${item.item_id}:${eventType}`;
        const pending = pendingAction === key;
        return (
          <button
            key={eventType}
            type="button"
            data-execution-action={eventType}
            className={`execution-button ${eventType === "abandon" ? "execution-button-danger" : index === 0 ? "execution-button-primary" : "execution-button-secondary"}`}
            disabled={pendingAction !== null}
            aria-busy={pending}
            onClick={() => onAction(item, eventType)}
          >
            <Icon name={eventType === "pause" ? "pause" : eventType === "start" || eventType === "resume" || eventType === "unblock" ? "play" : eventType === "complete" ? "confirm" : "warning"} size={17} />
            {pending ? "Сохраняю…" : action.label}
          </button>
        );
      })}
    </div>
  );
}

function ExecutionItemRow({
  item,
  readOnly,
  pendingAction,
  onAction,
  onCorrect,
}: {
  readonly item: ExecutionItemState;
  readonly readOnly: boolean;
  readonly pendingAction: string | null;
  readonly onAction: (item: ExecutionItemState, eventType: ExecutionEventType) => void;
  readonly onCorrect: (item: ExecutionItemState, event: ExecutionHistoryEvent) => void;
}): ReactElement {
  return (
    <li className="execution-item" data-execution-item={item.item_id} data-execution-state={item.state}>
      <div className="execution-item-header">
        <div>
          <span className="execution-item-kind">{kindLabel(item.item_kind)}</span>
          <h4>{item.title}</h4>
        </div>
        <span className={`execution-state-badge execution-state-${item.state}`} data-execution-state-badge>{stateLabel(item.state)}</span>
      </div>
      {item.description ? <p className="execution-item-description">{item.description}</p> : null}
      <dl className="execution-item-meta">
        <div><dt>Контекст</dt><dd>{item.parent_item_id ? "Вложено в другой пункт плана" : "Самостоятельный пункт плана"}</dd></div>
        <div><dt>Цель</dt><dd>{item.goal_refs.length > 0 ? `Точная связь · ${item.goal_refs.length}` : "Связь не указана"}</dd></div>
        <div><dt>Плановое время</dt><dd>{formatMinutes(item.planned_effort_minutes)}</dd></div>
        <div><dt>Целевое окно</dt><dd>{item.target_start_local && item.target_end_local ? `${formatLocal(item.target_start_local)} — ${formatLocal(item.target_end_local)}` : "Не задано"}</dd></div>
        <div><dt>Время факта</dt><dd>{item.actual_effort_minutes === null ? "Не указано" : `${formatMinutes(item.actual_effort_minutes)} · ${item.effort_precision === "exact" ? "точно" : "неизвестно"}`}</dd></div>
        <div><dt>Сопоставление окна</dt><dd>{windowLabel(item.window_relation)}</dd></div>
      </dl>
      {item.state === "blocked" && item.current_block_reasons.length > 0 ? <p className="execution-item-callout"><strong>Текущая блокировка:</strong> {item.current_block_reasons.map(codeLabel).join(", ")}{item.current_block_note ? ` · ${item.current_block_note}` : ""}</p> : null}
      <ItemActions item={item} readOnly={readOnly} pendingAction={pendingAction} onAction={onAction} />
      <div className="execution-item-details">
        <History item={item} onCorrect={onCorrect} readOnly={readOnly} />
        <ItemProvenance item={item} />
      </div>
    </li>
  );
}

function MetricList({ report }: { readonly report: ExecutionFeedbackReport }): ReactElement {
  const metrics = [
    ["Запланировано", report.planned_executable_item_count],
    ["Есть событие", report.items_with_any_event_count],
    ["Начато", report.items_with_effective_start_count],
    ["Не начато", report.not_started_count],
    ["В работе", report.in_progress_count],
    ["На паузе", report.paused_count],
    ["Заблокировано", report.blocked_count],
    ["Завершено", report.completed_count],
    ["Прекращено", report.abandoned_count],
  ] as const;
  return (
    <dl className="execution-metrics">
      {metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

function EffortSummary({ report }: { readonly report: ExecutionFeedbackReport }): ReactElement {
  const deltaPrefix = report.aggregate_effort_delta_minutes > 0 ? "+" : "";
  return (
    <div className="execution-effort-summary">
      <div><span>Плановое время</span><strong>{report.comparable_planned_effort_minutes} мин</strong></div>
      <div><span>Фактически указано</span><strong>{report.comparable_actual_effort_minutes} мин</strong></div>
      <div><span>Разница по сопоставимым</span><strong>{deltaPrefix}{report.aggregate_effort_delta_minutes} мин</strong></div>
      <p>Сопоставимых оценок времени: <strong>{report.comparable_effort_count}</strong>. Точных терминальных оценок: {report.terminal_exact_effort_count}; неизвестных: {report.terminal_unknown_effort_count}.</p>
    </div>
  );
}

function ReasonSummary({ report }: { readonly report: ExecutionFeedbackReport }): ReactElement | null {
  const groups = [
    ["Причины блокировки", report.blocker_reason_counts],
    ["Причины терминальных записей", report.terminal_reason_counts],
    ["Отклонения", report.deviation_reason_counts],
  ] as const;
  if (groups.every(([, values]) => values.length === 0)) return null;
  return (
    <div className="execution-reason-summary">
      {groups.map(([title, values]) => values.length > 0 ? <div key={title}><h4>{title}</h4><ul>{values.map((entry) => <li key={entry.code}><span>{codeLabel(entry.code)}</span><strong>{entry.count}</strong></li>)}</ul></div> : null)}
    </div>
  );
}

function ExecutionReport({
  report,
  readOnly,
  pendingAction,
  onAction,
  onCorrect,
}: {
  readonly report: ExecutionFeedbackReport;
  readonly readOnly: boolean;
  readonly pendingAction: string | null;
  readonly onAction: (item: ExecutionItemState, eventType: ExecutionEventType) => void;
  readonly onCorrect: (item: ExecutionItemState, event: ExecutionHistoryEvent) => void;
}): ReactElement {
  return (
    <div className="execution-report">
      <div className="execution-report-heading">
        <div>
          <h3>Сводка по плану</h3>
          <p>Прозрачные количества и явные сопоставления — без оценки продуктивности.</p>
        </div>
        <span className={`execution-source-badge execution-source-${report.source_status}`}>{sourceLabel(report.source_status)}</span>
      </div>
      <MetricList report={report} />
      <div className="execution-feedback-band">
        <h4>План и фактически указанное время</h4>
        <EffortSummary report={report} />
      </div>
      <div className="execution-window-band">
        <h4>Сопоставление целевых окон</h4>
        <p>{report.within_window_count} в окне · {report.before_window_count} раньше · {report.after_window_count} после · {report.no_window_count} без окна · {report.unknown_window_count} неизвестно. Знаменатель: {report.window_relation_denominator} терминальных записей с проверяемым временем.</p>
      </div>
      <ReasonSummary report={report} />
      <section className="execution-items" aria-labelledby="execution-items-title">
        <div className="execution-subheading"><h4 id="execution-items-title">Пункты, которые можно выполнить</h4><span>{report.items.length}</span></div>
        {report.items.length > 0 ? <ol>{report.items.map((item) => <ExecutionItemRow key={item.item_id} item={item} readOnly={readOnly} pendingAction={pendingAction} onAction={onAction} onCorrect={onCorrect} />)}</ol> : <p className="execution-empty">В этом плане нет выбранных обязательств или следующих действий.</p>}
      </section>
      {report.caveats.length > 0 ? <div className="execution-caveats" role="note"><h4>Оговорки</h4><ul>{report.caveats.map((caveat) => <li key={caveat}>{caveatLabel(caveat)}</li>)}</ul></div> : null}
    </div>
  );
}

function CalibrationSummary({ calibration }: { readonly calibration: ExecutionCalibration }): ReactElement {
  const prefix = calibration.sum_delta_minutes > 0 ? "+" : "";
  return (
    <div className="execution-calibration-result" data-execution-calibration-result>
      <div className="execution-subheading"><h4>Калибровка выбранного набора</h4><span>{calibration.selected_plan_count} планов</span></div>
      <dl className="execution-calibration-metrics">
        <div><dt>Запланированные пункты</dt><dd>{calibration.selected_executable_item_count}</dd></div>
        <div><dt>Наблюдалось выполнение</dt><dd>{calibration.execution_observed_item_count}</dd></div>
        <div><dt>Завершено</dt><dd>{calibration.completed_count}</dd></div>
        <div><dt>Прекращено</dt><dd>{calibration.abandoned_count}</dd></div>
        <div><dt>Сопоставимых оценок времени</dt><dd>{calibration.effort_comparable_count}</dd></div>
        <div><dt>План по сопоставимым</dt><dd>{calibration.sum_planned_effort_minutes} мин</dd></div>
        <div><dt>Факт по сопоставимым</dt><dd>{calibration.sum_actual_effort_minutes} мин</dd></div>
        <div><dt>Разница</dt><dd>{prefix}{calibration.sum_delta_minutes} мин</dd></div>
      </dl>
      <p className="execution-field-help">Неизвестное усилие исключено из сравнения; этот отчёт ничего не меняет в будущих планах.</p>
    </div>
  );
}

function SnapshotList({
  snapshots,
  selected,
  viewing,
  busy,
  onToggle,
  onView,
}: {
  readonly snapshots: readonly ExecutionPlanSummary[];
  readonly selected: readonly string[];
  readonly viewing: string | null;
  readonly busy: boolean;
  readonly onToggle: (snapshot: ExecutionPlanSummary) => void;
  readonly onView: (snapshot: ExecutionPlanSummary) => void;
}): ReactElement {
  if (snapshots.length === 0) return <p className="execution-empty">Исторические планы пока недоступны для явного выбора.</p>;
  return (
    <ul className="execution-snapshot-list">
      {snapshots.map((snapshot) => {
        const key = snapshotKey(snapshot);
        return (
          <li key={key}>
            <label className="execution-snapshot-choice">
              <input type="checkbox" checked={selected.includes(key)} onChange={() => onToggle(snapshot)} disabled={selected.length >= 32 && !selected.includes(key) || busy} />
              <span><strong>Версия {snapshot.planning_plan_revision}</strong><small>{formatLocal(snapshot.start_local)} — {formatLocal(snapshot.end_local)} · {sourceLabel(snapshot.source_status)}</small></span>
            </label>
            <button type="button" className="execution-history-correct" disabled={busy || viewing === key} onClick={() => onView(snapshot)}>{viewing === key ? "Загружаю…" : "Показать сводку"}</button>
          </li>
        );
      })}
    </ul>
  );
}

export function ExecutionFeedbackSurface(): ReactElement {
  const [data, setData] = useState<ExecutionFeedbackStateResponse | null>(null);
  const [loadStatus, setLoadStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [dialogAction, setDialogAction] = useState<DialogAction | null>(null);
  const [actionDraft, setActionDraft] = useState<ActionDraft>(newActionDraft);
  const [actionError, setActionError] = useState("");
  const [dialogCorrection, setDialogCorrection] = useState<DialogCorrection | null>(null);
  const [correctionDraft, setCorrectionDraft] = useState<CorrectionDraft>(newCorrectionDraft);
  const [correctionError, setCorrectionError] = useState("");
  const [selectedSnapshots, setSelectedSnapshots] = useState<string[]>([]);
  const [calibration, setCalibration] = useState<ExecutionCalibration | null>(null);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [calibrationError, setCalibrationError] = useState("");
  const [viewingSnapshot, setViewingSnapshot] = useState<string | null>(null);
  const [viewedReport, setViewedReport] = useState<{ readonly plan: ExecutionPlanSummary; readonly report: ExecutionFeedbackReport } | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  const closeDialog = (): void => {
    setDialogAction(null);
    setDialogCorrection(null);
    setActionError("");
    setCorrectionError("");
    const target = previousFocus.current;
    previousFocus.current = null;
    window.setTimeout(() => {
      if (target && document.contains(target)) target.focus();
    }, 0);
  };

  const loadExecutionState = async (): Promise<void> => {
    setLoadStatus("loading");
    setLoadError("");
    setNotice("");
    try {
      const response = await loadExecutionFeedbackState();
      setData(response);
      setLoadStatus("ready");
    } catch (error) {
      setLoadStatus("error");
      setLoadError(presentError(error, "Не удалось загрузить состояние выполнения. Проверь вход и повтори попытку."));
    }
  };

  const openAction = (item: ExecutionItemState, eventType: ExecutionEventType): void => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setActionError("");
    setActionDraft(newActionDraft());
    setDialogAction({ item, eventType });
  };

  const updateReportAfterMutation = (response: { readonly plan: ExecutionPlanSummary; readonly report: ExecutionFeedbackReport }): void => {
    setData((current) => {
      if (!current) return current;
      const samePlan = current.current_plan?.planning_snapshot_id === response.plan.planning_snapshot_id
        && current.current_plan.planning_snapshot_fingerprint === response.plan.planning_snapshot_fingerprint;
      if (!samePlan) return current;
      return {
        ...current,
        current_plan: response.plan,
        report: response.report,
        available_snapshots: current.available_snapshots.map((snapshot) => snapshotKey(snapshot) === snapshotKey(response.plan) ? response.plan : snapshot),
        caveats: response.report.caveats,
      };
    });
  };

  const submitAction = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!dialogAction) return;
    const validationError = validateActionDraft(dialogAction, actionDraft);
    if (validationError) {
      setActionError(validationError);
      return;
    }
    const key = `${dialogAction.item.item_id}:${dialogAction.eventType}`;
    setPendingAction(key);
    setActionError("");
    try {
      const response = await recordExecutionFeedbackEvent({
        planning_snapshot_id: dialogAction.item.planning_snapshot_id,
        planning_snapshot_fingerprint: dialogAction.item.planning_snapshot_fingerprint,
        item_id: dialogAction.item.item_id,
        accepted_item_fingerprint: dialogAction.item.accepted_item_fingerprint,
        operation_id: newOperationId(),
        event_type: dialogAction.eventType,
        occurred_at: actionDraft.occurredAt.trim(),
        actual_effort_minutes: actionDraft.effortPrecision === "exact" ? Number(actionDraft.actualEffortMinutes) : null,
        effort_precision: actionDraft.effortPrecision,
        actual_result_note: dialogAction.eventType === "block" || dialogAction.eventType === "complete" || dialogAction.eventType === "abandon" ? actionDraft.actualResultNote : "",
        reason_codes: dialogAction.eventType === "block" || dialogAction.eventType === "abandon" ? actionDraft.reasonCodes : [],
        deviation_codes: dialogAction.eventType === "complete" || dialogAction.eventType === "abandon" ? actionDraft.deviationCodes : [],
        result_disposition: dialogAction.eventType === "complete" || dialogAction.eventType === "abandon" ? actionDraft.resultDisposition : null,
      });
      updateReportAfterMutation(response);
      setNotice(`${eventLabel(response.event.event_type)} записано для «${dialogAction.item.title}».`);
      closeDialog();
    } catch (error) {
      setActionError(presentError(error, "Не удалось записать событие. Обнови состояние и повтори попытку."));
    } finally {
      setPendingAction(null);
    }
  };

  const openCorrection = (item: ExecutionItemState, event: ExecutionHistoryEvent): void => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setCorrectionDraft(newCorrectionDraft());
    setCorrectionError("");
    setDialogCorrection({ item, event });
  };

  const submitCorrection = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!dialogCorrection) return;
    if (!correctionDraft.occurredAt.trim()) {
      setCorrectionError("Укажи время исправления или нажми «Сейчас».");
      return;
    }
    if (!correctionDraft.reason.trim()) {
      setCorrectionError("Опиши причину исправления.");
      return;
    }
    const key = `${dialogCorrection.item.item_id}:correction`;
    setPendingAction(key);
    setCorrectionError("");
    try {
      const response = await correctExecutionFeedback({
        planning_snapshot_id: dialogCorrection.item.planning_snapshot_id,
        planning_snapshot_fingerprint: dialogCorrection.item.planning_snapshot_fingerprint,
        item_id: dialogCorrection.item.item_id,
        accepted_item_fingerprint: dialogCorrection.item.accepted_item_fingerprint,
        operation_id: newOperationId(),
        occurred_at: correctionDraft.occurredAt.trim(),
        void_target_event_id: dialogCorrection.event.event_id,
        void_target_event_fingerprint: dialogCorrection.event.event_fingerprint,
        correction_reason: correctionDraft.reason.trim(),
      });
      updateReportAfterMutation(response);
      setNotice(`Исправление добавлено для «${dialogCorrection.item.title}».`);
      closeDialog();
    } catch (error) {
      setCorrectionError(presentError(error, "Не удалось исправить запись. Обнови состояние и повтори попытку."));
    } finally {
      setPendingAction(null);
    }
  };

  const viewSnapshot = async (snapshot: ExecutionPlanSummary): Promise<void> => {
    const key = snapshotKey(snapshot);
    setViewingSnapshot(key);
    if (data?.current_plan && data.report && snapshotKey(data.current_plan) === key) {
      setViewedReport({ plan: snapshot, report: data.report });
      setViewingSnapshot(null);
      return;
    }
    try {
      const response = await loadExecutionFeedback(snapshot.planning_snapshot_id, snapshot.planning_snapshot_fingerprint);
      setViewedReport({ plan: response.plan, report: response.report });
    } catch (error) {
      setLoadError(presentError(error, "Не удалось открыть сводку выбранного плана."));
    } finally {
      setViewingSnapshot(null);
    }
  };

  const toggleSnapshot = (snapshot: ExecutionPlanSummary): void => {
    const key = snapshotKey(snapshot);
    setSelectedSnapshots((current) => current.includes(key) ? current.filter((value) => value !== key) : current.length < 32 ? [...current, key] : current);
    setCalibration(null);
    setCalibrationError("");
  };

  const runCalibration = async (): Promise<void> => {
    if (!data || selectedSnapshots.length === 0) return;
    const snapshots = data.available_snapshots.filter((snapshot) => selectedSnapshots.includes(snapshotKey(snapshot))).map(({ planning_snapshot_id, planning_snapshot_fingerprint }) => ({ planning_snapshot_id, planning_snapshot_fingerprint }));
    setCalibrationBusy(true);
    setCalibrationError("");
    try {
      const response = await loadExecutionCalibration(snapshots);
      setCalibration(response.calibration);
    } catch (error) {
      setCalibrationError(presentError(error, "Не удалось собрать калибровку выбранных планов."));
    } finally {
      setCalibrationBusy(false);
    }
  };

  const report = data?.report;
  const currentPlan = data?.current_plan;
  const viewedHistorical = viewedReport && (!currentPlan || snapshotKey(viewedReport.plan) !== snapshotKey(currentPlan));

  return (
    <section className="execution-feedback-surface" id="execution-feedback" data-execution-feedback-surface aria-busy={loadStatus === "loading"}>
      <div className="execution-feedback-intro">
        <div>
          <p className="execution-feedback-kicker">Владелец сообщает факт</p>
          <p>Здесь появляется только то выполнение, которое ты явно записал. План сам по себе не становится фактом, а время между событиями не превращается в усилие.</p>
        </div>
        <button type="button" className="execution-button execution-button-primary execution-load-button" onClick={() => void loadExecutionState()} disabled={loadStatus === "loading"} aria-busy={loadStatus === "loading"}>
          <Icon name="refresh" size={18} />
          {loadStatus === "loading" ? "Загружаю…" : data ? "Обновить состояние" : "Загрузить состояние"}
        </button>
      </div>
      <p className="execution-feedback-status" role="status" aria-live="polite">{loadStatus === "idle" ? "Загрузка выполняется только по этой кнопке." : loadStatus === "loading" ? "Проверяю точный план и историю…" : notice}</p>
      {loadError ? <p className="execution-feedback-error" role="alert">{loadError}</p> : null}
      {loadStatus === "ready" && !currentPlan ? (
        <div className="execution-empty-state">
          <Icon name="info" size={32} />
          <h3>Актуальный принятый план не найден</h3>
          <p>Событие выполнения нельзя создать без точного принятого плана. Исторические версии можно открыть для чтения или выбрать для явной калибровки.</p>
        </div>
      ) : null}
      {currentPlan && report ? (
        <>
          <div className="execution-plan-header">
            <div>
              <span className={`execution-source-badge execution-source-${currentPlan.source_status}`}>{sourceLabel(currentPlan.source_status)}</span>
              <h3>Текущий принятый план · версия {currentPlan.planning_plan_revision}</h3>
              <p>{formatLocal(currentPlan.start_local)} — {formatLocal(currentPlan.end_local)} · часовой пояс {currentPlan.planning_timezone} · исполняемых пунктов: {currentPlan.executable_item_count}</p>
            </div>
            {currentPlan.source_status !== "current" ? <p className="execution-plan-warning" role="note"><Icon name="warning" size={20} />{currentPlan.source_status === "stale" ? "Источник изменился. Новое начало закрыто; уже начатое можно безопасно закрыть." : "Этот план нельзя использовать для нового начала."}</p> : null}
          </div>
          <ExecutionReport report={report} readOnly={false} pendingAction={pendingAction} onAction={openAction} onCorrect={openCorrection} />
          <section className="execution-snapshots" aria-labelledby="execution-snapshots-title">
            <div className="execution-subheading"><div><h3 id="execution-snapshots-title">История планов</h3><p>Старые версии сохраняют свою отдельную историю и не получают новые старты.</p></div></div>
            <SnapshotList snapshots={data?.available_snapshots ?? []} selected={selectedSnapshots} viewing={viewingSnapshot} busy={calibrationBusy} onToggle={toggleSnapshot} onView={(snapshot) => void viewSnapshot(snapshot)} />
          </section>
          <section className="execution-calibration" aria-labelledby="execution-calibration-title">
            <div className="execution-subheading"><div><h3 id="execution-calibration-title">Калибровка по выбору владельца</h3><p>Выбери точные версии плана и запроси прозрачные количества. Ничего автоматически не меняется.</p></div><span>{selectedSnapshots.length}/32</span></div>
            <button type="button" className="execution-button execution-button-secondary" disabled={selectedSnapshots.length === 0 || calibrationBusy} onClick={() => void runCalibration()} aria-busy={calibrationBusy}>{calibrationBusy ? "Считаю…" : "Собрать калибровку"}</button>
            {calibrationError ? <p className="execution-feedback-error" role="alert">{calibrationError}</p> : null}
            {calibration ? <CalibrationSummary calibration={calibration} /> : null}
          </section>
          {viewedHistorical ? <section className="execution-historical-report" aria-labelledby="execution-historical-title"><div className="execution-subheading"><div><h3 id="execution-historical-title">Сводка выбранного исторического плана</h3><p>Только чтение: {formatLocal(viewedReport.plan.start_local)} — {formatLocal(viewedReport.plan.end_local)} · версия {viewedReport.plan.planning_plan_revision}.</p></div></div><ExecutionReport report={viewedReport.report} readOnly pendingAction={pendingAction} onAction={openAction} onCorrect={openCorrection} /></section> : null}
        </>
      ) : null}
      {dialogAction ? <ActionDialog action={dialogAction} draft={actionDraft} error={actionError} busy={pendingAction !== null} onChange={setActionDraft} onSubmit={(event) => void submitAction(event)} onClose={closeDialog} /> : null}
      {dialogCorrection ? <CorrectionDialog draft={correctionDraft} error={correctionError} busy={pendingAction !== null} onChange={setCorrectionDraft} onSubmit={(event) => void submitCorrection(event)} onClose={closeDialog} /> : null}
    </section>
  );
}
