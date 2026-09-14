import {
  confirmStatedObservedMapping,
  loadBehavioralSelfModel,
  loadSelfModel,
  loadStatedObservedComposition,
  loadStatedObservedMappingStatus,
  reviewStatedObservedMapping,
  type BehavioralPattern,
  type BehavioralSelfModelResponse,
  type SelfModelClaim,
  type StatedObservedCompositionResponse,
  type StatedObservedMappingReviewResponse,
  type StatedObservedMappingSelector,
  type StatedObservedMappingStatusResponse,
} from "./api";
import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { presentCode, presentError } from "./presentation";
import { GrowthSurface } from "./growth-surface";

const ELIGIBLE_PATTERN_TYPES = new Set([
  "repeated_exact_choice",
  "stable_over_time",
  "insufficient_evidence",
]);

const PATTERN_LABELS: Record<string, string> = {
  repeated_exact_choice: "Повторяемый точный выбор",
  mixed_exact_choices: "Смешанные точные выборы",
  stable_over_time: "Стабильный шаблон точного выбора",
  changed_over_time: "Разные точные выборы в разных окнах",
  insufficient_evidence: "Недостаточно сопоставимых данных",
  not_comparable: "Нельзя сопоставить по текущей политике точного сравнения",
};

const COMPOSITION_LABELS: Record<string, string> = {
  aligned: "Явно сопоставленный вариант утверждения совпадает с текущим наблюдаемым вариантом.",
  divergent: "Явно сопоставленный вариант утверждения отличается от текущего наблюдаемого варианта.",
  stated_evidence_missing: "Свидетельство явного утверждения для этой связи отсутствует в текущем хранилище.",
  behavioral_evidence_insufficient: "Сопоставимых поведенческих свидетельств пока недостаточно.",
  not_comparable: "Источники нельзя сопоставить по текущей политике точного сравнения.",
};

const STATE_LABELS: Record<string, string> = {
  current: "текущее окно",
  historical: "историческое окно",
  mixed: "смешанное состояние",
  stable: "стабильное состояние",
  changed: "изменившееся состояние",
  insufficient: "данных недостаточно",
  not_comparable: "нельзя сопоставить",
};

const LIFECYCLE_LABELS: Record<string, string> = {
  active: "Действующее",
  superseded: "Заменённое",
  invalidated: "Признанное недействительным",
  deleted: "Удалённое",
};

type SurfaceBundle = {
  readonly stated: import("./api").SelfModelResponse;
  readonly behavioral: BehavioralSelfModelResponse;
  readonly mappings: StatedObservedMappingStatusResponse;
};

type BehavioralTarget = {
  readonly key: string;
  readonly pattern: BehavioralPattern;
  readonly option: { readonly option_index: number; readonly option_fingerprint: string };
};

function safeText(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function exactRatio(ratio: { readonly numerator: number; readonly denominator: number } | null): string {
  return ratio ? `${ratio.numerator} / ${ratio.denominator}` : "—";
}

function patternLabel(value: string): string {
  return PATTERN_LABELS[value] ?? presentCode(value, "Шаблон точного выбора не определён.");
}

function stateLabel(value: string): string {
  return STATE_LABELS[value] ?? presentCode(value, "Состояние не определено");
}

function lifecycleLabel(value: string): string {
  return LIFECYCLE_LABELS[value] ?? presentCode(value, "Состояние жизненного цикла не определено");
}

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
  return error instanceof DOMException && error.name === "AbortError";
}

function responseError(error: unknown, fallback: string): string {
  return presentError(error, fallback);
}

function ErrorMessage({ message }: { readonly message: string }): ReactElement | null {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (message) ref.current?.focus();
  }, [message]);
  return message ? <p className="capture-error cognitive-twin-error" role="alert" tabIndex={-1} ref={ref}>{message}</p> : null;
}

function ClaimOption({ claim }: { readonly claim: SelfModelClaim }): ReactElement {
  const evidence = claim.supporting_evidence[0];
  return (
    <div className="cognitive-twin-selection-summary">
      <p className="cognitive-twin-claim-text">{safeText(claim.claim, "Текст явного утверждения недоступен.")}</p>
      <dl className="cognitive-twin-fields">
        <div><dt>Домен</dt><dd>{safeText(claim.domain)}</dd></div>
        <div><dt>UUID явного утверждения</dt><dd>{safeText(evidence?.id)}</dd></div>
        <div><dt>Свидетельство</dt><dd>{presentCode(evidence?.evidence_kind, "Свидетельство не указано")}</dd></div>
        <div><dt>Время свидетельства</dt><dd>{safeText(evidence?.evidence_at)}</dd></div>
      </dl>
    </div>
  );
}

function WindowSummary({ pattern }: { readonly pattern: BehavioralPattern }): ReactElement {
  return (
    <div className="cognitive-twin-window-grid">
      {pattern.windows.map((window) => (
        <div className="cognitive-twin-window" key={window.window}>
          <span className="cognitive-twin-window-name">{stateLabel(window.window)}</span>
          <strong>{window.observation_count}</strong>
          <span>сопоставимых наблюдений</span>
          <ul>
            {window.choice_support.map((support) => (
              <li key={`${support.option.option_index}-${support.option.option_fingerprint}`}>
                 вариант {support.option.option_index}: {exactRatio(support.support_ratio)}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function PatternTechnicalDetails({ pattern }: { readonly pattern: BehavioralPattern }): ReactElement {
  return (
    <details className="cognitive-twin-technical">
      <summary>Технические сведения</summary>
      <dl className="cognitive-twin-fields cognitive-twin-fields-technical">
        <div><dt>Домен</dt><dd>{safeText(pattern.cohort?.domain)}</dd></div>
        <div><dt>Отпечаток группы наблюдений</dt><dd>{safeText(pattern.cohort?.cohort_fingerprint)}</dd></div>
        <div><dt>Отпечаток происхождения</dt><dd>{safeText(pattern.provenance.provenance_fingerprint)}</dd></div>
        <div><dt>Временной диапазон</dt><dd>{safeText(pattern.temporal_span.earliest_evidence_at)} — {safeText(pattern.temporal_span.latest_evidence_at)}</dd></div>
        <div><dt>Источник</dt><dd>{pattern.provenance.source_count} UUID</dd></div>
      </dl>
    </details>
  );
}

function PatternRow({ pattern }: { readonly pattern: BehavioralPattern }): ReactElement {
  const selected = pattern.selected_option;
  return (
    <li className="cognitive-twin-pattern-row">
      <div>
        <strong>{patternLabel(pattern.pattern_type)}</strong>
        <span>{stateLabel(pattern.state)} · {safeText(pattern.cohort?.domain)}</span>
      </div>
      <span className="cognitive-twin-support">
         {selected ? `вариант ${selected.option_index}: ${exactRatio(pattern.support_ratio)}` : `${pattern.total_comparable_observations} наблюдений`}
      </span>
    </li>
  );
}

function CompositionResult({ result }: { readonly result: StatedObservedCompositionResponse }): ReactElement {
  return (
    <section className={`cognitive-twin-composition cognitive-twin-composition-${result.state}`} aria-labelledby="cognitive-twin-composition-title">
      <h5 id="cognitive-twin-composition-title">Текущее сравнение</h5>
      <p className="cognitive-twin-composition-state">{COMPOSITION_LABELS[result.state] ?? "Результат сравнения недоступен."}</p>
      {result.observed_option ? <p>Текущий наблюдаемый вариант точного выбора: {result.observed_option.option_index} · поддержка отображается в слое наблюдений.</p> : null}
      <details className="cognitive-twin-technical">
        <summary>Коды и ограничения</summary>
        <dl className="cognitive-twin-fields cognitive-twin-fields-technical">
          <div><dt>Состояние</dt><dd>{presentCode(result.state, "Состояние не указано")}</dd></div>
          <div><dt>Причина</dt><dd>{presentCode(result.reason_code, "Причина не указана")}</dd></div>
          <div><dt>UUID сопоставления</dt><dd>{safeText(result.mapping_id)}</dd></div>
          <div><dt>Сформировано</dt><dd>{safeText(result.generated_at)}</dd></div>
          <div><dt>Ограничения</dt><dd>{result.caveats.map((code) => presentCode(code, "Техническое ограничение")).join(", ") || "—"}</dd></div>
        </dl>
      </details>
    </section>
  );
}

export function CognitiveTwinSurface(): ReactElement {
  const [bundle, setBundle] = useState<SurfaceBundle | null>(null);
  const [selectedStatedId, setSelectedStatedId] = useState("");
  const [selectedTargetKey, setSelectedTargetKey] = useState("");
  const [review, setReview] = useState<StatedObservedMappingReviewResponse | null>(null);
  const [composition, setComposition] = useState<StatedObservedCompositionResponse | null>(null);
  const [acceptedMappingId, setAcceptedMappingId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [compositionBusy, setCompositionBusy] = useState(false);
  const [status, setStatus] = useState("Данные загружаются только после явного обновления.");
  const [error, setError] = useState("");
  const [compositionError, setCompositionError] = useState("");
  const refreshController = useRef<AbortController | null>(null);
  const reviewController = useRef<AbortController | null>(null);
  const confirmController = useRef<AbortController | null>(null);
  const compositionController = useRef<AbortController | null>(null);
  const refreshSequence = useRef(0);
  const reviewSequence = useRef(0);
  const compositionSequence = useRef(0);
  const operationId = useRef<string | null>(null);
  const feedbackRef = useRef<HTMLParagraphElement>(null);

  const statedClaims = useMemo(
    () => bundle?.stated.claims.filter((claim) => (
      claim.dimension === "preference"
      && typeof claim.domain === "string"
      && claim.domain.length > 0
      && claim.supporting_evidence.length === 1
      && claim.contradicting_evidence.length === 0
      && claim.contextual_evidence.length === 0
    )) ?? [],
    [bundle],
  );

  const behavioralTargets = useMemo<readonly BehavioralTarget[]>(() => {
    if (!bundle) return [];
    return bundle.behavioral.patterns.flatMap((pattern) => {
      if (!pattern.cohort || !ELIGIBLE_PATTERN_TYPES.has(pattern.pattern_type)) return [];
      const options = pattern.selected_option ? [pattern.selected_option] : pattern.choice_support.map((item) => item.option);
      return options.map((option) => ({
        key: `${pattern.cohort?.cohort_fingerprint}:${option.option_index}:${option.option_fingerprint}`,
        pattern,
        option,
      }));
    });
  }, [bundle]);

  const selectedClaim = statedClaims.find((claim) => claim.supporting_evidence[0]?.id === selectedStatedId) ?? null;
  const selectedTarget = behavioralTargets.find((target) => target.key === selectedTargetKey) ?? null;
  const activeMapping = bundle?.mappings.mappings.find((mapping) => (
    mapping.lifecycle_state === "active" && mapping.source_note_uuid === selectedStatedId
  )) ?? null;
  const sameMappingAlreadyActive = Boolean(activeMapping && review && activeMapping.mapping_fingerprint === review.candidate_mapping_fingerprint);

  function cancelReviewRequest(): void {
    reviewController.current?.abort();
    reviewController.current = null;
    reviewSequence.current += 1;
    setReviewBusy(false);
  }

  function cancelCompositionRequest(): void {
    compositionController.current?.abort();
    compositionController.current = null;
    compositionSequence.current += 1;
    setCompositionBusy(false);
  }

  function clearSelectionResults(): void {
    cancelReviewRequest();
    cancelCompositionRequest();
    setReview(null);
    setComposition(null);
    setAcceptedMappingId("");
    setConfirmed(false);
    setCompositionError("");
    operationId.current = null;
  }

  async function refresh(): Promise<void> {
    if (busy || reviewBusy || compositionBusy || confirmBusy) return;
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const sequence = ++refreshSequence.current;
    setBusy(true);
    setError("");
     setStatus("Собираю явные утверждения, наблюдения и жизненный цикл из текущего хранилища…");
    clearSelectionResults();
    try {
      const [stated, behavioral, mappings] = await Promise.all([
        loadSelfModel(undefined, controller.signal),
        loadBehavioralSelfModel(undefined, controller.signal),
        loadStatedObservedMappingStatus(undefined, controller.signal),
      ]);
      if (controller.signal.aborted || sequence !== refreshSequence.current) return;
      setBundle({ stated, behavioral, mappings });
      setSelectedStatedId("");
      setSelectedTargetKey("");
      setStatus("Показаны данные из текущего перестроения. Автоматического обновления нет.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== refreshSequence.current) return;
      setError(responseError(caught, "Модель связей сейчас недоступна."));
      setStatus("");
    } finally {
      if (sequence === refreshSequence.current) setBusy(false);
    }
  }

  function selectStated(value: string): void {
    if (busy || confirmBusy) return;
    setSelectedStatedId(value);
    clearSelectionResults();
  }

  function selectTarget(value: string): void {
    if (busy || confirmBusy) return;
    setSelectedTargetKey(value);
    clearSelectionResults();
  }

  function selectorFromReview(): StatedObservedMappingSelector | null {
    if (!review) return null;
    return {
      source_note_uuid: review.stated.source_note_uuid,
      behavioral_cohort_fingerprint: review.behavioral.cohort?.cohort_fingerprint ?? "",
      behavioral_option_index: review.behavioral.option.option_index,
      behavioral_option_fingerprint: review.behavioral.option.option_fingerprint,
    };
  }

  async function startReview(): Promise<void> {
    if (!selectedClaim || !selectedTarget || busy || reviewBusy || compositionBusy || confirmBusy) return;
    cancelReviewRequest();
    const controller = new AbortController();
    reviewController.current = controller;
    const sequence = reviewSequence.current;
    setReviewBusy(true);
    setError("");
    setReview(null);
    setComposition(null);
    setConfirmed(false);
    operationId.current = null;
    setStatus("Проверяю текущего кандидата на точное сопоставление…");
    try {
      const next = await reviewStatedObservedMapping({
        source_note_uuid: selectedClaim.supporting_evidence[0].id ?? "",
        behavioral_cohort_fingerprint: selectedTarget.pattern.cohort?.cohort_fingerprint ?? "",
        behavioral_option_index: selectedTarget.option.option_index,
        behavioral_option_fingerprint: selectedTarget.option.option_fingerprint,
      }, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== reviewSequence.current) return;
      setReview(next);
      setStatus("Проверка готова. Проверь три слоя и явно подтверди связь.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== reviewSequence.current) return;
      setError(responseError(caught, "Не удалось подготовить проверку."));
      setStatus("");
    } finally {
      if (sequence === reviewSequence.current) setReviewBusy(false);
    }
  }

  async function confirmReview(): Promise<void> {
    const selector = selectorFromReview();
    if (!selector || !confirmed || confirmBusy || sameMappingAlreadyActive) return;
    let token = operationId.current;
    try {
      token ??= uuidv7();
    } catch {
      setError("Не удалось создать UUID операции. Повтори подтверждение.");
      return;
    }
    operationId.current = token;
    confirmController.current?.abort();
    const controller = new AbortController();
    confirmController.current = controller;
    setConfirmBusy(true);
    setError("");
    setStatus("Повторно проверяю источники и сохраняю только явно подтверждённое сопоставление…");
    try {
      const result = await confirmStatedObservedMapping(
        selector,
        token,
        true,
        activeMapping && activeMapping.mapping_fingerprint !== review?.candidate_mapping_fingerprint
          ? activeMapping.mapping_id
          : null,
        undefined,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setAcceptedMappingId(result.mapping.mapping_id);
      operationId.current = null;
      setStatus("Связь принята. Создана новая неизменяемая запись сопоставления.");
      feedbackRef.current?.focus();
      const [mappings, nextComposition] = await Promise.all([
        loadStatedObservedMappingStatus(undefined, controller.signal),
        loadStatedObservedComposition(selector.source_note_uuid, undefined, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      setBundle((current) => current ? { ...current, mappings } : current);
      setComposition(nextComposition);
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted) return;
      setError(responseError(caught, "Связь не принята. Проверь актуальность проверки и повтори её."));
      setStatus("");
    } finally {
      setConfirmBusy(false);
    }
  }

  function cancelConfirm(): void {
    confirmController.current?.abort();
    setConfirmBusy(false);
    setStatus("Подтверждение отменено; принятый результат не показан. Можно безопасно повторить с тем же UUID операции.");
  }

  async function checkComposition(): Promise<void> {
    if (!selectedStatedId || compositionBusy || reviewBusy || confirmBusy) return;
    cancelCompositionRequest();
    const controller = new AbortController();
    compositionController.current = controller;
    const sequence = compositionSequence.current;
    setCompositionBusy(true);
    setCompositionError("");
    try {
      const result = await loadStatedObservedComposition(selectedStatedId, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== compositionSequence.current) return;
      setComposition(result);
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== compositionSequence.current) return;
      setCompositionError(responseError(caught, "Текущее сравнение недоступно."));
    } finally {
      if (!controller.signal.aborted && sequence === compositionSequence.current) setCompositionBusy(false);
    }
  }

  useEffect(() => () => {
    refreshController.current?.abort();
    reviewController.current?.abort();
    confirmController.current?.abort();
    compositionController.current?.abort();
    refreshSequence.current += 1;
    reviewSequence.current += 1;
    compositionSequence.current += 1;
  }, []);

  return (
    <section className="cognitive-twin-surface" id="cognitive-twin" aria-labelledby="cognitive-twin-title" aria-busy={busy || reviewBusy || confirmBusy} data-cognitive-twin-surface>
      <div className="cognitive-twin-heading">
        <div>
          <h3 id="cognitive-twin-title">Явное / наблюдаемое / сопоставленное сравнение</h3>
          <p>Три раздельных слоя: явное утверждение, повторяемые проверенные решения в точных контекстах и связь, которую владелец подтвердил отдельно.</p>
        </div>
        <div className="cognitive-twin-heading-meta">
          <span>Этап 10D</span>
          <span>перестроение только для чтения + явное сопоставление</span>
        </div>
      </div>
      <GrowthSurface />
      <div className="cognitive-twin-toolbar">
        <button className="cognitive-twin-button cognitive-twin-button-primary" type="button" disabled={busy || reviewBusy || compositionBusy || confirmBusy} aria-busy={busy} onClick={() => void refresh()}>
          {busy ? "Обновляю…" : "Обновить текущие данные"}
        </button>
        <p className="cognitive-twin-status" role="status" aria-live="polite">{status}</p>
      </div>
      <ErrorMessage message={error} />
      {!bundle ? <p className="cognitive-twin-empty">Нажми «Обновить текущие данные», чтобы явно построить три слоя из текущего хранилища.</p> : (
        <div className="cognitive-twin-layers">
          <section className="cognitive-twin-layer" aria-labelledby="cognitive-twin-stated-title">
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">01</span><div><h4 id="cognitive-twin-stated-title">Явное</h4><p>Что я явно сообщил или предпочёл.</p></div></div>
            <label className="cognitive-twin-label" htmlFor="cognitive-twin-stated-select">Выбери утверждение о предпочтении</label>
            <select className="cognitive-twin-select" id="cognitive-twin-stated-select" value={selectedStatedId} disabled={busy || confirmBusy} onChange={(event) => selectStated(event.target.value)}>
              <option value="">Выбери явное утверждение о предпочтении</option>
              {statedClaims.map((claim) => <option key={claim.supporting_evidence[0]?.id} value={claim.supporting_evidence[0]?.id}>{safeText(claim.domain)} · {safeText(claim.claim, "Без текста")}</option>)}
            </select>
            {selectedClaim ? <ClaimOption claim={selectedClaim} /> : <p className="cognitive-twin-muted">Доступны только утверждения о предпочтениях с одним текущим подтверждающим свидетельством.</p>}
            <details className="cognitive-twin-technical"><summary>Почему утверждение доступно для сопоставления</summary><p>Этап 10C принимает только измерение «предпочтение», непустой точный домен, одно подтверждающее свидетельство и отсутствие противоречащих или контекстных свидетельств.</p></details>
          </section>

          <section className="cognitive-twin-layer" aria-labelledby="cognitive-twin-observed-title">
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">02</span><div><h4 id="cognitive-twin-observed-title">Наблюдаемое</h4><p>Что видно из повторяемых проверенных решений в точных сопоставимых контекстах.</p></div></div>
            <label className="cognitive-twin-label" htmlFor="cognitive-twin-observed-select">Выбери поведенческую цель</label>
            <select className="cognitive-twin-select" id="cognitive-twin-observed-select" value={selectedTargetKey} disabled={busy || confirmBusy} onChange={(event) => selectTarget(event.target.value)}>
              <option value="">Выбери точную группу наблюдений и вариант</option>
              {behavioralTargets.map((target) => <option key={target.key} value={target.key}>{safeText(target.pattern.cohort?.domain)} · {patternLabel(target.pattern.pattern_type)} · вариант {target.option.option_index}</option>)}
            </select>
            {selectedTarget ? <div className="cognitive-twin-selection-summary"><p><strong>{patternLabel(selectedTarget.pattern.pattern_type)}</strong> · {stateLabel(selectedTarget.pattern.state)}</p><p>Поддержка выбранного варианта: {exactRatio(selectedTarget.pattern.choice_support.find((item) => item.option.option_index === selectedTarget.option.option_index)?.support_ratio ?? null)}</p><WindowSummary pattern={selectedTarget.pattern} /><PatternTechnicalDetails pattern={selectedTarget.pattern} /></div> : <p className="cognitive-twin-muted">Для явного сопоставления доступны только повторяемые, стабильные или недостаточные точные модели.</p>}
            <details className="cognitive-twin-technical"><summary>Все текущие модели точного выбора</summary><ul className="cognitive-twin-pattern-list">{bundle.behavioral.patterns.map((pattern, index) => <PatternRow pattern={pattern} key={`${pattern.cohort?.cohort_fingerprint ?? "none"}-${index}`} />)}</ul></details>
          </section>

          <section className="cognitive-twin-layer cognitive-twin-layer-mapped" aria-labelledby="cognitive-twin-mapped-title">
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">03</span><div><h4 id="cognitive-twin-mapped-title">Сопоставление</h4><p>Явно подтверждённая владельцем связь между утверждением о предпочтении и точной поведенческой группой/вариантом.</p></div></div>
            <div className="cognitive-twin-mapping-actions">
              <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" disabled={!selectedClaim || !selectedTarget || busy || reviewBusy || compositionBusy || confirmBusy} onClick={() => void startReview()}>{reviewBusy ? "Готовлю проверку…" : "Показать проверку"}</button>
              <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" disabled={!selectedStatedId || busy || reviewBusy || compositionBusy || confirmBusy} onClick={() => void checkComposition()}>{compositionBusy ? "Проверяю…" : "Проверить текущее сравнение"}</button>
            </div>
            <p className="cognitive-twin-muted">Активных связей: {bundle.mappings.active_mapping_count}. Проверка и результат сравнения не сохраняются в браузере.</p>
            {activeMapping ? <p className="cognitive-twin-inline-state">Для выбранного UUID явного утверждения есть действующее сопоставление: {activeMapping.mapping_id}. Новая связь заменит его только после новой проверки.</p> : null}
            {review ? <section className="cognitive-twin-review" aria-labelledby="cognitive-twin-review-title"><h5 id="cognitive-twin-review-title">Результат готов к проверке владельцем</h5><p className="cognitive-twin-claim-text">{review.claim_text}</p><dl className="cognitive-twin-fields"><div><dt>Измерение</dt><dd>{presentCode(review.stated.dimension, "Измерение не указано")}</dd></div><div><dt>Домен</dt><dd>{review.cohort_domain}</dd></div><div><dt>Время свидетельства</dt><dd>{review.stated.evidence_at}</dd></div><div><dt>Модель точного выбора</dt><dd>{patternLabel(review.pattern_type)} · {stateLabel(review.pattern_state)}</dd></div></dl><div className="cognitive-twin-review-context"><h6>Текущий контекст решения</h6><p><strong>Ситуация:</strong> {safeText(review.situation)}</p><p><strong>Известная информация:</strong> {safeText(review.information_known_at_decision_time)}</p><p><strong>Критерии:</strong> {review.criteria.join(" · ") || "—"}</p><p><strong>Варианты:</strong> {review.ordered_options.map((option) => `${option.option_index}: ${option.label}`).join(" · ")}</p></div><p className="cognitive-twin-review-notice">Подписи показаны только для проверки человеком. Приложение не сопоставляет их автоматически по тексту.</p><details className="cognitive-twin-technical"><summary>Происхождение и отпечатки точного выбора</summary><dl className="cognitive-twin-fields cognitive-twin-fields-technical"><div><dt>Отпечаток явного утверждения</dt><dd>{review.stated.claim_fingerprint}</dd></div><div><dt>Отпечаток группы наблюдений</dt><dd>{safeText(review.behavioral.cohort?.cohort_fingerprint)}</dd></div><div><dt>Отпечаток выбранного варианта</dt><dd>{review.behavioral.option.option_fingerprint}</dd></div><div><dt>Отпечаток кандидата на сопоставление</dt><dd>{review.candidate_mapping_fingerprint}</dd></div><div><dt>Поддержка</dt><dd>{review.behavioral.source_count} UUID точных источников</dd></div></dl></details><label className="cognitive-twin-confirm-label"><input type="checkbox" checked={confirmed} disabled={confirmBusy || sameMappingAlreadyActive} onChange={(event) => setConfirmed(event.target.checked)} />Я проверил явное утверждение, наблюдения и контекст выше и явно подтверждаю эту точную связь.</label><div className="cognitive-twin-confirm-actions"><button className="cognitive-twin-button cognitive-twin-button-primary" type="button" disabled={!confirmed || confirmBusy || sameMappingAlreadyActive} aria-busy={confirmBusy} onClick={() => void confirmReview()}>{confirmBusy ? "Подтверждаю…" : sameMappingAlreadyActive ? "Эта связь уже действует" : "Подтвердить сопоставление"}</button>{confirmBusy ? <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" onClick={cancelConfirm}>Отменить</button> : null}</div></section> : null}
            {acceptedMappingId ? <p className="cognitive-twin-success" ref={feedbackRef} tabIndex={-1} role="status" aria-live="polite">UUID принятого сопоставления: {acceptedMappingId}. История неизменяема; редактирование на месте не выполняется.</p> : null}
            {compositionError ? <p className="capture-error cognitive-twin-error" role="alert" tabIndex={-1}>{compositionError}</p> : null}
            {composition ? <CompositionResult result={composition} /> : null}
            <details className="cognitive-twin-technical"><summary>Жизненный цикл</summary><ul className="cognitive-twin-lifecycle-list">{bundle.mappings.mappings.map((mapping) => <li key={mapping.mapping_id}><span>{lifecycleLabel(mapping.lifecycle_state)}</span><span>{mapping.source_note_uuid}</span><span>{mapping.mapping_id}</span></li>)}</ul>{bundle.mappings.mappings.length === 0 ? <p>Действующих сопоставлений нет.</p> : null}</details>
          </section>
        </div>
      )}
    </section>
  );
}
