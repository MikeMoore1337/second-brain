import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";

import { Icon } from "./icons";
import {
  confirmProspectiveAuditLink,
  executeProspectiveAudit,
  loadProspectiveAuditCalibration,
  loadProspectiveAuditPending,
  ProspectiveAuditApiError,
  reviewProspectiveAuditLink,
  type ProspectiveAuditCalibrationResult,
  type ProspectiveAuditEvent,
  type ProspectiveAuditPendingResponse,
  type ProspectiveAuditReviewResponse,
} from "./prospective-audit-api";
import "./prospective-audit-surface.css";

type OptionRow = { id: string; label: string };
type OperationState = "idle" | "busy" | "prediction" | "abstention" | "cancelled" | "error";
type AsyncState = "idle" | "loading" | "ready" | "error";

const ERROR_TEXT: Record<string, string> = {
  PROSPECTIVE_AUDIT_INVALID_REQUEST: "Запрос аудита прогноза не прошёл проверку.",
  PROSPECTIVE_AUDIT_CONTENT_TOO_LARGE: "Запрос аудита прогноза слишком велик.",
  PROSPECTIVE_AUDIT_RESULT_INVALID: "Результат прогноза не удалось безопасно записать.",
  PROSPECTIVE_AUDIT_STALE_OR_REPLAYED: "Результат прогноза устарел или уже использован.",
  PROSPECTIVE_AUDIT_IDEMPOTENCY_CONFLICT: "Повтор операции конфликтует с уже записанным событием.",
  PROSPECTIVE_AUDIT_STORE_UNAVAILABLE: "Операционный журнал аудита сейчас недоступен.",
  PROSPECTIVE_AUDIT_STORE_CORRUPT: "Операционный журнал аудита не прошёл проверку целостности.",
  PROSPECTIVE_AUDIT_LINK_INVALID: "Явная связь с журналом решений не прошла проверку.",
  PROSPECTIVE_AUDIT_LINK_UNAVAILABLE: "Источник для связи с журналом решений сейчас недоступен.",
  PROSPECTIVE_AUDIT_CANCELLED: "Операция аудита прогноза отменена.",
  PROSPECTIVE_AUDIT_INVALID_RESPONSE: "Сервис аудита прогноза вернул некорректный ответ.",
};

const ABSTENTION_TEXT: Record<string, string> = {
  no_matching_evidence: "Текущие свидетельства не поддержали ни один вариант.",
  multiple_options_supported: "Текущие свидетельства поддержали несколько вариантов.",
  insufficient_or_invalid_current_context: "Текущий контекст недостаточен для прогноза.",
};

const LINKAGE_LABELS: Record<string, string> = {
  decision_target_invalid: "Журнал недопустим",
  decision_identity_conflict: "Конфликт идентичности",
  decision_time_invalid: "Время решения недопустимо",
  decision_precedes_prediction: "Решение было раньше прогноза",
  decision_note_created_before_prediction: "Запись журнала была раньше прогноза",
  decision_record_changed: "Текущий журнал изменился",
  option_mapping_invalid: "Соответствие вариантов недопустимо",
  chosen_option_unmapped: "Выбранный вариант не сопоставлен",
  link_fingerprint_mismatch: "Вариант журнала изменился",
  audit_event_missing_or_deleted: "Событие не найдено",
  decision_target_unavailable: "Журнал решений недоступен",
  canonical_scan_unavailable: "Текущее хранилище недоступно",
  decision_target_expired: "Срок события истёк",
};

function operationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `stage9-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorText(caught: unknown, fallback: string): string {
  if (caught instanceof ProspectiveAuditApiError) return ERROR_TEXT[caught.code] ?? fallback;
  if (typeof caught === "object" && caught !== null && "name" in caught && caught.name === "AbortError") {
    return "Операция отменена.";
  }
  return fallback;
}

function formatTime(value: string): string {
  try {
    return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  } catch {
    return value;
  }
}

function optionLabel(event: ProspectiveAuditEvent): string {
  if (event.predicted_option_label) return event.predicted_option_label;
  if (event.abstention_code) return ABSTENTION_TEXT[event.abstention_code] ?? `Код отказа: ${event.abstention_code}`;
  return "Отказ от прогноза";
}

function ratioText(
  ratio: ProspectiveAuditCalibrationResult["metrics"]["coverage"],
): string {
  return ratio ? `${ratio.numerator} / ${ratio.denominator}` : "Нет данных";
}

function Metric({ label, value }: { label: string; value: number }): ReactElement {
  return <div className="stage9-metric"><dt>{label}</dt><dd>{value}</dd></div>;
}

function EventSummary({ event }: { event: ProspectiveAuditEvent }): ReactElement {
  const prediction = event.kind === "prediction";
  return (
    <article className="stage9-event-summary" aria-labelledby="stage9-event-title">
      <div className="stage9-result-heading">
        <div>
          <p className="eyebrow">Сохранённый результат</p>
          <h3 id="stage9-event-title">{prediction ? "ПРОГНОЗ записан" : "Отказ от прогноза записан"}</h3>
        </div>
        <Icon name={prediction ? "simulate" : "info"} size={32} aria-hidden="true" />
      </div>
      <dl className="stage9-fields">
        <div><dt>Результат</dt><dd>{optionLabel(event)}</dd></div>
        <div><dt>Создано</dt><dd>{formatTime(event.created_at)}</dd></div>
        <div><dt>Идентификатор события</dt><dd><code>{event.event_id}</code></dd></div>
        <div><dt>Версия построения</dt><dd><code>{event.derivation_version}</code></dd></div>
      </dl>
      <p className="stage9-result-note">
        В событии сохранены только ограниченные данные для будущей явной связи: запрос и приватные
        свидетельства в операционное хранилище не попадают.
      </p>
    </article>
  );
}

function PendingEvents({
  data,
  eventId,
  onChange,
}: {
  data: ProspectiveAuditPendingResponse;
  eventId: string;
  onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="stage9-select-field" htmlFor="stage9-event-select">
      <span>Записанный результат</span>
      <select id="stage9-event-select" className="review-input" value={eventId} onChange={(event) => onChange(event.target.value)}>
        <option value="">Выбери событие</option>
        {data.events.map((event) => (
          <option value={event.event_id} key={event.event_id}>
            {formatTime(event.created_at)} · {event.kind === "prediction" ? "ПРОГНОЗ" : "отказ"} · {optionLabel(event)}
          </option>
        ))}
      </select>
    </label>
  );
}

function DecisionTargets({
  data,
  decisionId,
  onChange,
}: {
  data: ProspectiveAuditPendingResponse;
  decisionId: string;
  onChange: (value: string) => void;
}): ReactElement {
  return (
    <label className="stage9-select-field" htmlFor="stage9-decision-select">
      <span>Текущий Журнал решений</span>
      <select id="stage9-decision-select" className="review-input" value={decisionId} onChange={(event) => onChange(event.target.value)}>
        <option value="">Выбери журнал</option>
        {data.decision_journals.map((decision) => (
          <option value={decision.decision_id} key={decision.decision_id}>
            {formatTime(decision.evidence_at)} · {decision.chosen_option}
          </option>
        ))}
      </select>
    </label>
  );
}

function LinkReview({
  review,
  mapping,
  confirmed,
  onMapping,
  onConfirmed,
  onConfirm,
  busy,
}: {
  review: ProspectiveAuditReviewResponse;
  mapping: Readonly<Record<string, string>>;
  confirmed: boolean;
  onMapping: (auditOptionId: string, decisionIndex: string) => void;
  onConfirmed: (value: boolean) => void;
  onConfirm: () => void;
  busy: boolean;
}): ReactElement {
  const predictedId = review.event.predicted_option_id;
  const chosenIndex = String(review.decision.chosen_option_index);
  const predictedMapped = predictedId === null || mapping[predictedId] !== undefined;
  const chosenMapped = review.event.kind === "abstention" || Object.values(mapping).includes(chosenIndex);
  const ready = predictedMapped && chosenMapped;
  return (
    <section className="stage9-link-review" aria-labelledby="stage9-link-review-title">
      <div className="stage9-subheading">
        <div>
          <p className="eyebrow">Проверка владельцем</p>
          <h4 id="stage9-link-review-title">Проверить явное соответствие.</h4>
        </div>
        <Icon name="relation" size={28} aria-hidden="true" />
      </div>
      <p className="stage9-review-copy">
        Связь не выводится автоматически. Сверь время, варианты и фактический выбор; после этого
        подтверди сопоставление одной явной операцией.
      </p>
      <div className="stage9-review-facts">
        <dl className="stage9-fields">
          <div><dt>Прогноз записан</dt><dd>{formatTime(review.event.created_at)}</dd></div>
          <div><dt>Фактический выбор</dt><dd>{review.decision.chosen_option}</dd></div>
          <div><dt>Время свидетельства</dt><dd>{formatTime(review.decision.evidence_at)}</dd></div>
          <div><dt>Создано</dt><dd>{formatTime(review.decision.created)}</dd></div>
        </dl>
      </div>
      {review.event.kind === "prediction" ? (
        <fieldset className="stage9-mapping">
          <legend>Сопоставление вариантов</legend>
          <p>Для каждого варианта можно выбрать ровно один индекс текущего журнала или оставить его без соответствия.</p>
          <div className="stage9-mapping-list">
            {review.event.options.map((option) => (
              <label className="stage9-mapping-row" key={option.id}>
                 <span><strong>{option.label}</strong><small>Идентификатор: {option.id}</small></span>
                <select
                  className="review-input"
                  value={mapping[option.id] ?? ""}
                  onChange={(event) => onMapping(option.id, event.target.value)}
                  disabled={busy}
                  aria-label={`Соответствие варианта ${option.label}`}
                >
                  <option value="">Не сопоставлять</option>
                  {review.decision.options.map((decisionOption) => (
                    <option value={decisionOption.index} key={decisionOption.index}>
                      {decisionOption.index + 1}. {decisionOption.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <p className="stage9-abstention-note">Для отказа от прогноза соответствие вариантов не требуется.</p>
      )}
      <label className="stage9-confirmation">
        <input type="checkbox" checked={confirmed} onChange={(event) => onConfirmed(event.target.checked)} disabled={busy} />
        <span>Подтверждаю явную связь записанного результата с этим текущим Журналом решений.</span>
      </label>
      {!ready ? <p className="stage9-inline-hint">Сопоставь предсказанный и фактически выбранный варианты перед подтверждением.</p> : null}
      <button className="review-button review-button-primary" type="button" disabled={busy || !confirmed || !ready} onClick={onConfirm}>
        {busy ? "Записываю связь…" : "Подтвердить связь"}
      </button>
    </section>
  );
}

function CalibrationResult({ result }: { result: ProspectiveAuditCalibrationResult }): ReactElement {
  const { metrics } = result;
  return (
    <section className="stage9-calibration-result" aria-labelledby="stage9-calibration-result-title">
      <div className="stage9-result-heading">
        <div>
          <p className="eyebrow">Перестраиваемая сводка</p>
          <h4 id="stage9-calibration-result-title">Перспективная калибровка</h4>
        </div>
        <Icon name="growth" size={28} aria-hidden="true" />
      </div>
      <dl className="stage9-metrics" aria-label="Агрегированные метрики перспективной калибровки">
        <Metric label="Аудированных операций" value={metrics.audited_operations} />
        <Metric label="Прогнозов" value={metrics.predictions} />
        <Metric label="Отказов" value={metrics.abstentions} />
        <Metric label="Связанных решений" value={metrics.linked_actual_decisions} />
        <Metric label="Ожидают связи" value={metrics.pending_unlinked_events} />
        <Metric label="Недоступных связей" value={metrics.unavailable_linkage_events} />
        <Metric label="Недействительных связей" value={metrics.invalid_linkage_events} />
        <Metric label="Совпадений вариантов" value={metrics.exact_option_matches} />
      </dl>
      <div className="stage9-ratio-grid">
        <div><span>Покрытие прогнозов</span><strong>{ratioText(metrics.coverage)}</strong><small>Числитель / знаменатель</small></div>
        <div><span>Покрытие явных связей</span><strong>{ratioText(metrics.actual_linkage_coverage)}</strong><small>Числитель / знаменатель</small></div>
        <div><span>Оценённые прогнозы</span><strong>{ratioText(metrics.evaluated_prediction_coverage)}</strong><small>Числитель / знаменатель</small></div>
        <div><span>Совпадение без отказов</span><strong>{ratioText(metrics.accuracy_non_abstained)}</strong><small>Числитель / знаменатель</small></div>
      </div>
      <div className="stage9-count-groups">
        <CountGroup title="Недействительные связи" counts={result.invalid_linkage_by_code} />
        <CountGroup title="Недоступные связи" counts={result.unavailable_linkage_by_code} />
      </div>
      <details className="stage9-technical-details">
        <summary>Технические детали сводки</summary>
        <dl className="stage9-fields">
          <div><dt>Контракт</dt><dd><code>{result.contract_version}</code></dd></div>
          <div><dt>Версия построения</dt><dd><code>{result.derivation_version}</code></dd></div>
          <div><dt>Политика</dt><dd><code>{result.policy_id}</code></dd></div>
          <div><dt>Отпечаток политики</dt><dd><code>{result.policy_fingerprint}</code></dd></div>
          <div><dt>Срок хранения</dt><dd><code>{result.retention_policy}</code></dd></div>
        </dl>
      </details>
    </section>
  );
}

function CountGroup({
  title,
  counts,
}: {
  title: string;
  counts: readonly { readonly code: string; readonly count: number }[];
}): ReactElement {
  return (
    <section className="stage9-count-group" aria-label={title}>
      <h5>{title}</h5>
      <ul>
        {counts.map((item) => <li key={item.code}><span>{LINKAGE_LABELS[item.code] ?? `Код причины: ${item.code}`}</span><strong>{item.count}</strong></li>)}
      </ul>
    </section>
  );
}

export function ProspectiveAuditSurface(): ReactElement {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<OptionRow[]>([{ id: "a", label: "" }]);
  const [operation, setOperation] = useState<{ state: OperationState; event: ProspectiveAuditEvent | null; error: string }>({ state: "idle", event: null, error: "" });
  const [pendingState, setPendingState] = useState<AsyncState>("idle");
  const [pending, setPending] = useState<ProspectiveAuditPendingResponse | null>(null);
  const [pendingError, setPendingError] = useState("");
  const [eventId, setEventId] = useState("");
  const [decisionId, setDecisionId] = useState("");
  const [reviewState, setReviewState] = useState<AsyncState>("idle");
  const [review, setReview] = useState<ProspectiveAuditReviewResponse | null>(null);
  const [reviewError, setReviewError] = useState("");
  const [mapping, setMapping] = useState<Readonly<Record<string, string>>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [linkState, setLinkState] = useState<"idle" | "busy" | "linked" | "error">("idle");
  const [linkError, setLinkError] = useState("");
  const [calibrationState, setCalibrationState] = useState<AsyncState>("idle");
  const [calibration, setCalibration] = useState<ProspectiveAuditCalibrationResult | null>(null);
  const [calibrationError, setCalibrationError] = useState("");
  const [status, setStatus] = useState("");
  const feedbackRef = useRef<HTMLParagraphElement>(null);
  const executeController = useRef<AbortController | null>(null);
  const pendingController = useRef<AbortController | null>(null);
  const reviewController = useRef<AbortController | null>(null);
  const linkController = useRef<AbortController | null>(null);
  const calibrationController = useRef<AbortController | null>(null);
  const executeRequest = useRef(0);
  const pendingRequest = useRef(0);
  const reviewRequest = useRef(0);
  const linkRequest = useRef(0);
  const calibrationRequest = useRef(0);
  const nextOption = useRef(1);

  const busy = operation.state === "busy";

  useEffect(() => {
    if (operation.error || pendingError || reviewError || linkError || calibrationError) feedbackRef.current?.focus();
  }, [operation.error, pendingError, reviewError, linkError, calibrationError]);

  useEffect(() => () => {
    executeController.current?.abort();
    pendingController.current?.abort();
    reviewController.current?.abort();
    linkController.current?.abort();
    calibrationController.current?.abort();
  }, []);

  function updateOption(index: number, key: keyof OptionRow, value: string): void {
    setOptions((current) => current.map((option, optionIndex) => optionIndex === index ? { ...option, [key]: value } : option));
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy) return;
    if (!query.trim() || options.some((option) => !option.id.trim() || !option.label.trim())) {
      setOperation({ state: "error", event: null, error: "Заполни запрос, идентификаторы и названия всех вариантов." });
      setStatus("");
      return;
    }
    const requestId = ++executeRequest.current;
    const controller = new AbortController();
    executeController.current = controller;
    setOperation({ state: "busy", event: null, error: "" });
    setStatus("Проверяю результат и записываю его в операционное хранилище…");
    try {
      const result = await executeProspectiveAudit(operationId(), query, options, fetch, controller.signal);
      if (requestId !== executeRequest.current) return;
      setOperation({ state: result.kind, event: result, error: "" });
      setStatus(result.kind === "prediction" ? "ПРОГНОЗ записан до решения." : "Отказ от прогноза записан до решения.");
    } catch (caught) {
      if (requestId !== executeRequest.current) return;
      const cancelled = caught instanceof DOMException && caught.name === "AbortError";
      setOperation({ state: cancelled ? "cancelled" : "error", event: null, error: cancelled ? "Операция отменена." : errorText(caught, "Не удалось записать результат аудита.") });
      setStatus("");
    } finally {
      if (requestId === executeRequest.current) executeController.current = null;
    }
  }

  function cancelExecution(): void {
    executeRequest.current += 1;
    executeController.current?.abort();
    executeController.current = null;
    setOperation({ state: "cancelled", event: null, error: "Операция отменена." });
    setStatus("");
  }

  async function refreshPending(): Promise<void> {
    const requestId = ++pendingRequest.current;
    pendingController.current?.abort();
    const controller = new AbortController();
    pendingController.current = controller;
    setPendingState("loading");
    setPendingError("");
    setReview(null);
    setReviewState("idle");
    setLinkState("idle");
    try {
      const result = await loadProspectiveAuditPending(fetch, controller.signal);
      if (requestId !== pendingRequest.current) return;
      setPending(result);
      setEventId(result.events[0]?.event_id ?? "");
      setDecisionId(result.decision_journals[0]?.decision_id ?? "");
      setPendingState("ready");
    } catch (caught) {
      if (requestId !== pendingRequest.current) return;
      setPending(null);
      setPendingState("error");
      setPendingError(errorText(caught, "Ожидающие связи сейчас недоступны."));
    } finally {
      if (requestId === pendingRequest.current) pendingController.current = null;
    }
  }

  async function openReview(): Promise<void> {
    if (!eventId || !decisionId) return;
    const requestId = ++reviewRequest.current;
    reviewController.current?.abort();
    const controller = new AbortController();
    reviewController.current = controller;
    setReviewState("loading");
    setReviewError("");
    setLinkState("idle");
    try {
      const result = await reviewProspectiveAuditLink(eventId, decisionId, fetch, controller.signal);
      if (requestId !== reviewRequest.current) return;
      setReview(result);
      setMapping({});
      setConfirmed(false);
      setReviewState("ready");
    } catch (caught) {
      if (requestId !== reviewRequest.current) return;
      setReview(null);
      setReviewState("error");
      setReviewError(errorText(caught, "Не удалось перечитать текущий Журнал решений."));
    } finally {
      if (requestId === reviewRequest.current) reviewController.current = null;
    }
  }

  async function confirmLink(): Promise<void> {
    if (!review || !confirmed) return;
    const requestId = ++linkRequest.current;
    const controller = new AbortController();
    linkController.current?.abort();
    linkController.current = controller;
    const linkMapping = review.event.kind === "abstention" ? [] : Object.entries(mapping).flatMap(([auditOptionId, decisionIndex]) => {
      const option = review.decision.options.find((item) => item.index === Number(decisionIndex));
      if (!option || !option.fingerprint) return [];
      return [{ audit_option_id: auditOptionId, decision_option_index: option.index, decision_option_fingerprint: option.fingerprint }];
    });
    setLinkState("busy");
    setLinkError("");
    try {
      await confirmProspectiveAuditLink(review.event.event_id, review.decision.decision_id, operationId(), linkMapping, true, fetch, controller.signal);
      if (requestId !== linkRequest.current) return;
      setLinkState("linked");
      setPending((current) => current ? { ...current, events: current.events.filter((event) => event.event_id !== review.event.event_id) } : current);
      setReview(null);
      setReviewState("idle");
      setMapping({});
      setConfirmed(false);
    } catch (caught) {
      if (requestId !== linkRequest.current) return;
      setLinkState("error");
      setLinkError(errorText(caught, "Не удалось подтвердить связь."));
    } finally {
      if (requestId === linkRequest.current) linkController.current = null;
    }
  }

  async function refreshCalibration(): Promise<void> {
    const requestId = ++calibrationRequest.current;
    calibrationController.current?.abort();
    const controller = new AbortController();
    calibrationController.current = controller;
    setCalibrationState("loading");
    setCalibrationError("");
    try {
      const result = await loadProspectiveAuditCalibration(fetch, controller.signal);
      if (requestId !== calibrationRequest.current) return;
      setCalibration(result);
      setCalibrationState("ready");
    } catch (caught) {
      if (requestId !== calibrationRequest.current) return;
      setCalibration(null);
      setCalibrationState("error");
      setCalibrationError(errorText(caught, "Перспективная сводка сейчас недоступна."));
    } finally {
      if (requestId === calibrationRequest.current) calibrationController.current = null;
    }
  }

  const selectedEvent = pending?.events.find((item) => item.event_id === eventId) ?? null;
  const resultMessage = operation.event ? `${operation.event.kind === "prediction" ? "ПРОГНОЗ" : "Отказ от прогноза"}: ${optionLabel(operation.event)}` : "";

  return (
    <section
      className="stage9-surface signal-plane"
      id="prospective-audit"
      aria-labelledby="prospective-audit-title"
      aria-busy={busy || pendingState === "loading" || reviewState === "loading" || calibrationState === "loading"}
      data-stage9-state={operation.state}
    >
      <div className="section-heading stage9-heading">
        <p className="eyebrow">Шаг 9 · перспективный аудит</p>
        <h2 id="prospective-audit-title">Сохранить прогноз до решения.</h2>
        <p>
          Здесь результат текущего прогноза можно явно записать в отдельное операционное хранилище,
          чтобы позже сопоставить его с текущим Журналом решений. Обычный раздел «Прогноз» выше
          остаётся доступным только для чтения и ничего сюда не записывает.
        </p>
      </div>

      <aside className="stage9-boundary" aria-label="Граница перспективного аудита">
        <Icon name="info" size={20} aria-hidden="true" />
        <p>
          Запись запускается только этой кнопкой и происходит после проверки terminal result ядром.
          В операционное хранилище не попадают исходный запрос, приватные свидетельства или содержимое хранилища.
        </p>
      </aside>

      <form className="stage9-operation-panel" onSubmit={(event) => void submit(event)}>
        <label className="stage9-field stage9-field-wide" htmlFor="stage9-query">
          <span>Задача или запрос</span>
          <textarea id="stage9-query" className="capture-input capture-textarea" rows={3} required value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <fieldset className="stage9-options">
          <legend>Варианты пользователя</legend>
          <p>Идентификаторы нужны для точной связи; названия остаются ограниченными данными для проверки.</p>
          <div className="stage9-option-list">
            {options.map((option, index) => (
              <div className="stage9-option-row" key={index}>
                <label className="stage9-field"><span>Идентификатор</span><input className="review-input" value={option.id} autoComplete="off" required disabled={busy} onChange={(event) => updateOption(index, "id", event.target.value)} /></label>
                <label className="stage9-field"><span>Название</span><input className="review-input" value={option.label} autoComplete="off" required disabled={busy} onChange={(event) => updateOption(index, "label", event.target.value)} /></label>
                {options.length > 1 ? <button className="review-button review-button-quiet" type="button" disabled={busy} onClick={() => setOptions((current) => current.filter((_, optionIndex) => optionIndex !== index))}>Удалить</button> : null}
              </div>
            ))}
          </div>
          <button className="review-button review-button-secondary" type="button" disabled={busy || options.length >= 8} onClick={() => { const id = `option-${nextOption.current++}`; setOptions((current) => [...current, { id, label: "" }]); }}>Добавить вариант</button>
        </fieldset>
        <div className="stage9-actions">
          <button className="review-button review-button-primary" type="submit" disabled={busy} aria-busy={busy}>{busy ? "Проверяю и записываю…" : "Выполнить и записать"}</button>
          {busy ? <button className="review-button review-button-quiet" type="button" onClick={cancelExecution}>Отменить</button> : null}
          <p className="stage9-status" role="status" aria-live="polite">{status}</p>
        </div>
      </form>

      {operation.error ? <p ref={feedbackRef} className="stage9-error" role="alert" tabIndex={-1}>{operation.error}</p> : null}
      {operation.event ? <EventSummary event={operation.event} /> : null}
      {operation.state === "cancelled" && !operation.error ? <p className="stage9-cancelled" role="status">Операция отменена. Если сервер успел завершить запись, обнови очередь явно.</p> : null}

      <section className="stage9-review-panel" aria-labelledby="stage9-review-title">
        <div className="stage9-subheading">
          <div><p className="eyebrow">Явная связь</p><h3 id="stage9-review-title">Связать с Журналом решений.</h3></div>
          <Icon name="relation" size={32} aria-hidden="true" />
        </div>
        <p className="stage9-review-copy">Открой ожидающие события, перечитай текущий журнал решений и только затем создай связь, проверенную владельцем. Автоматического сопоставления по тексту нет.</p>
        <div className="stage9-actions">
          <button className="review-button review-button-secondary" type="button" onClick={() => void refreshPending()} disabled={pendingState === "loading"}>{pendingState === "loading" ? "Обновляю очередь…" : "Показать ожидающие связи"}</button>
          {linkState === "linked" ? <p className="stage9-status" role="status">Связь записана. Событие убрано из ожидающей очереди.</p> : null}
        </div>
        {pendingState === "ready" && pending ? (
          pending.events.length ? (
            <div className="stage9-review-controls">
              <PendingEvents data={pending} eventId={eventId} onChange={(value) => { setEventId(value); setReview(null); setReviewState("idle"); }} />
              <DecisionTargets data={pending} decisionId={decisionId} onChange={(value) => { setDecisionId(value); setReview(null); setReviewState("idle"); }} />
              <button className="review-button review-button-primary" type="button" disabled={!eventId || !decisionId || reviewState === "loading"} onClick={() => void openReview()}>{reviewState === "loading" ? "Перечитываю…" : "Перечитать и проверить"}</button>
            </div>
          ) : <p className="stage9-empty">Ожидающих явной связи событий нет.</p>
        ) : null}
        {pendingState === "ready" && pending && pending.events.length > 0 && pending.decision_journals.length === 0 ? <p className="stage9-empty">Текущих подходящих Журналов решений не найдено.</p> : null}
        {pendingError ? <p ref={feedbackRef} className="stage9-error" role="alert" tabIndex={-1}>{pendingError}</p> : null}
        {reviewError ? <p ref={feedbackRef} className="stage9-error" role="alert" tabIndex={-1}>{reviewError}</p> : null}
        {linkError ? <p ref={feedbackRef} className="stage9-error" role="alert" tabIndex={-1}>{linkError}</p> : null}
        {review && selectedEvent ? (
          <LinkReview
            review={review}
            mapping={mapping}
            confirmed={confirmed}
            onMapping={(auditOptionId, decisionIndex) => setMapping((current) => decisionIndex ? { ...current, [auditOptionId]: decisionIndex } : Object.fromEntries(Object.entries(current).filter(([key]) => key !== auditOptionId)))}
            onConfirmed={setConfirmed}
            onConfirm={() => void confirmLink()}
            busy={linkState === "busy"}
          />
        ) : null}
      </section>

      <section className="stage9-calibration-panel" aria-labelledby="stage9-calibration-title">
        <div className="stage9-subheading">
          <div><p className="eyebrow">Производная модель для чтения</p><h3 id="stage9-calibration-title">Просмотреть перспективную калибровку.</h3></div>
          <Icon name="growth" size={32} aria-hidden="true" />
        </div>
        <p className="stage9-review-copy">Сводка перестраивается из проверенной истории действующих аудитов и связей и показывает только ограниченные счётчики и целочисленные отношения.</p>
        <div className="stage9-actions">
          <button className="review-button review-button-secondary" type="button" onClick={() => void refreshCalibration()} disabled={calibrationState === "loading"}>{calibrationState === "loading" ? "Перестраиваю…" : "Обновить сводку"}</button>
          <p className="stage9-status" role="status" aria-live="polite">{calibrationState === "ready" ? "Сводка обновлена." : ""}</p>
        </div>
        {calibrationError ? <p ref={feedbackRef} className="stage9-error" role="alert" tabIndex={-1}>{calibrationError}</p> : null}
        {calibration ? <CalibrationResult result={calibration} /> : null}
      </section>
      <p className="stage9-screen-reader-note" aria-live="polite">{resultMessage}</p>
    </section>
  );
}
