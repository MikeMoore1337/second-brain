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

const ELIGIBLE_PATTERN_TYPES = new Set([
  "repeated_exact_choice",
  "stable_over_time",
  "insufficient_evidence",
]);

const PATTERN_LABELS: Record<string, string> = {
  repeated_exact_choice: "Повторяемый exact-выбор",
  mixed_exact_choices: "Смешанные exact-выборы",
  stable_over_time: "Стабильный exact pattern",
  changed_over_time: "Разные exact-выборы в окнах",
  insufficient_evidence: "Недостаточно сопоставимых данных",
  not_comparable: "Не сопоставимо по текущей exact policy",
};

const COMPOSITION_LABELS: Record<string, string> = {
  aligned: "Явно сопоставленный stated option совпадает с текущим exact observed subject.",
  divergent: "Явно сопоставленный stated option отличается от текущего exact observed subject.",
  stated_evidence_missing: "Stated-свидетельство для этой связи отсутствует в текущем хранилище.",
  behavioral_evidence_insufficient: "Сопоставимых behavioral-свидетельств пока недостаточно.",
  not_comparable: "Источники нельзя сопоставить по текущей exact policy.",
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
  return PATTERN_LABELS[value] ?? "Exact pattern";
}

function stateLabel(value: string): string {
  return value === "current"
    ? "текущее окно"
    : value === "historical"
      ? "историческое окно"
      : value === "insufficient"
        ? "данных недостаточно"
        : value;
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
  return error instanceof Error && error.message ? error.message : fallback;
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
      <p className="cognitive-twin-claim-text">{safeText(claim.claim, "Текст stated-утверждения недоступен.")}</p>
      <dl className="cognitive-twin-fields">
        <div><dt>Домен</dt><dd>{safeText(claim.domain)}</dd></div>
        <div><dt>Stated UUID</dt><dd>{safeText(evidence?.id)}</dd></div>
        <div><dt>Свидетельство</dt><dd>{safeText(evidence?.evidence_kind)}</dd></div>
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
                option {support.option.option_index}: {exactRatio(support.support_ratio)}
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
        <div><dt>Cohort fingerprint</dt><dd>{safeText(pattern.cohort?.cohort_fingerprint)}</dd></div>
        <div><dt>Provenance fingerprint</dt><dd>{safeText(pattern.provenance.provenance_fingerprint)}</dd></div>
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
        {selected ? `option ${selected.option_index}: ${exactRatio(pattern.support_ratio)}` : `${pattern.total_comparable_observations} наблюдений`}
      </span>
    </li>
  );
}

function CompositionResult({ result }: { readonly result: StatedObservedCompositionResponse }): ReactElement {
  return (
    <section className={`cognitive-twin-composition cognitive-twin-composition-${result.state}`} aria-labelledby="cognitive-twin-composition-title">
      <h5 id="cognitive-twin-composition-title">Текущее сравнение</h5>
      <p className="cognitive-twin-composition-state">{COMPOSITION_LABELS[result.state] ?? "Результат comparison недоступен."}</p>
      {result.observed_option ? <p>Текущий exact observed option: {result.observed_option.option_index} · поддержка отображается в Observed-слое.</p> : null}
      <details className="cognitive-twin-technical">
        <summary>Коды и caveats</summary>
        <dl className="cognitive-twin-fields cognitive-twin-fields-technical">
          <div><dt>Состояние</dt><dd>{result.state}</dd></div>
          <div><dt>Причина</dt><dd>{safeText(result.reason_code)}</dd></div>
          <div><dt>Mapping UUID</dt><dd>{safeText(result.mapping_id)}</dd></div>
          <div><dt>Сформировано</dt><dd>{safeText(result.generated_at)}</dd></div>
          <div><dt>Caveats</dt><dd>{result.caveats.join(", ") || "—"}</dd></div>
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
    setStatus("Собираю Stated, Observed и lifecycle из текущего хранилища…");
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
      setStatus("Показаны данные из текущего rebuild. Автоматического обновления нет.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== refreshSequence.current) return;
      setError(responseError(caught, "Cognitive Twin сейчас недоступен."));
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
    setStatus("Проверяю текущий exact candidate для review…");
    try {
      const next = await reviewStatedObservedMapping({
        source_note_uuid: selectedClaim.supporting_evidence[0].id ?? "",
        behavioral_cohort_fingerprint: selectedTarget.pattern.cohort?.cohort_fingerprint ?? "",
        behavioral_option_index: selectedTarget.option.option_index,
        behavioral_option_fingerprint: selectedTarget.option.option_fingerprint,
      }, undefined, controller.signal);
      if (controller.signal.aborted || sequence !== reviewSequence.current) return;
      setReview(next);
      setStatus("Review готов. Проверь три слоя и подтверди связь явно.");
    } catch (caught) {
      if (isAbortError(caught) || controller.signal.aborted || sequence !== reviewSequence.current) return;
      setError(responseError(caught, "Не удалось подготовить review."));
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
      setError("Не удалось создать operation UUID. Повтори подтверждение.");
      return;
    }
    operationId.current = token;
    confirmController.current?.abort();
    const controller = new AbortController();
    confirmController.current = controller;
    setConfirmBusy(true);
    setError("");
    setStatus("Повторно проверяю источники и сохраняю только explicit mapping…");
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
      setStatus("Связь принята. Создан новый immutable mapping record.");
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
      setError(responseError(caught, "Связь не принята. Проверь актуальность review и повтори его."));
      setStatus("");
    } finally {
      setConfirmBusy(false);
    }
  }

  function cancelConfirm(): void {
    confirmController.current?.abort();
    setConfirmBusy(false);
    setStatus("Подтверждение отменено; accepted result не показан. Можно безопасно повторить с тем же operation UUID.");
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
      setCompositionError(responseError(caught, "Текущее comparison недоступно."));
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
          <h3 id="cognitive-twin-title">Stated / Observed / Mapped comparison</h3>
          <p>Три раздельных слоя: явное утверждение, наблюдаемые reviewed decisions в exact contexts и связь, которую владелец подтвердил отдельно.</p>
        </div>
        <div className="cognitive-twin-heading-meta">
          <span>Stage 10D</span>
          <span>read-only rebuild + explicit mapping</span>
        </div>
      </div>
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
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">01</span><div><h4 id="cognitive-twin-stated-title">Stated</h4><p>Что я явно сообщил или предпочёл.</p></div></div>
            <label className="cognitive-twin-label" htmlFor="cognitive-twin-stated-select">Выбери preference assertion</label>
            <select className="cognitive-twin-select" id="cognitive-twin-stated-select" value={selectedStatedId} disabled={busy || confirmBusy} onChange={(event) => selectStated(event.target.value)}>
              <option value="">Выбери stated preference</option>
              {statedClaims.map((claim) => <option key={claim.supporting_evidence[0]?.id} value={claim.supporting_evidence[0]?.id}>{safeText(claim.domain)} · {safeText(claim.claim, "Без текста")}</option>)}
            </select>
            {selectedClaim ? <ClaimOption claim={selectedClaim} /> : <p className="cognitive-twin-muted">Доступны только preference claims с одним текущим подтверждающим свидетельством.</p>}
            <details className="cognitive-twin-technical"><summary>Почему claim доступен для mapping</summary><p>Stage 10C принимает только dimension preference, непустой exact domain, одно подтверждающее свидетельство и отсутствие contradicting/contextual evidence.</p></details>
          </section>

          <section className="cognitive-twin-layer" aria-labelledby="cognitive-twin-observed-title">
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">02</span><div><h4 id="cognitive-twin-observed-title">Observed</h4><p>Что видно из повторяемых reviewed decisions в exact comparable contexts.</p></div></div>
            <label className="cognitive-twin-label" htmlFor="cognitive-twin-observed-select">Выбери behavioral target</label>
            <select className="cognitive-twin-select" id="cognitive-twin-observed-select" value={selectedTargetKey} disabled={busy || confirmBusy} onChange={(event) => selectTarget(event.target.value)}>
              <option value="">Выбери exact cohort и option</option>
              {behavioralTargets.map((target) => <option key={target.key} value={target.key}>{safeText(target.pattern.cohort?.domain)} · {patternLabel(target.pattern.pattern_type)} · option {target.option.option_index}</option>)}
            </select>
            {selectedTarget ? <div className="cognitive-twin-selection-summary"><p><strong>{patternLabel(selectedTarget.pattern.pattern_type)}</strong> · {stateLabel(selectedTarget.pattern.state)}</p><p>Поддержка выбранного option: {exactRatio(selectedTarget.pattern.choice_support.find((item) => item.option.option_index === selectedTarget.option.option_index)?.support_ratio ?? null)}</p><WindowSummary pattern={selectedTarget.pattern} /><PatternTechnicalDetails pattern={selectedTarget.pattern} /></div> : <p className="cognitive-twin-muted">Для explicit mapping доступны только repeated, stable или insufficient exact patterns.</p>}
            <details className="cognitive-twin-technical"><summary>Все текущие exact patterns</summary><ul className="cognitive-twin-pattern-list">{bundle.behavioral.patterns.map((pattern, index) => <PatternRow pattern={pattern} key={`${pattern.cohort?.cohort_fingerprint ?? "none"}-${index}`} />)}</ul></details>
          </section>

          <section className="cognitive-twin-layer cognitive-twin-layer-mapped" aria-labelledby="cognitive-twin-mapped-title">
            <div className="cognitive-twin-layer-heading"><span className="cognitive-twin-layer-index">03</span><div><h4 id="cognitive-twin-mapped-title">Mapped comparison</h4><p>Явно подтверждённая владельцем связь между stated preference и exact behavioral cohort/option.</p></div></div>
            <div className="cognitive-twin-mapping-actions">
              <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" disabled={!selectedClaim || !selectedTarget || busy || reviewBusy || compositionBusy || confirmBusy} onClick={() => void startReview()}>{reviewBusy ? "Готовлю review…" : "Показать review"}</button>
              <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" disabled={!selectedStatedId || busy || reviewBusy || compositionBusy || confirmBusy} onClick={() => void checkComposition()}>{compositionBusy ? "Проверяю…" : "Проверить текущее сравнение"}</button>
            </div>
            <p className="cognitive-twin-muted">Активных связей: {bundle.mappings.active_mapping_count}. Review и composition не сохраняются в браузере.</p>
            {activeMapping ? <p className="cognitive-twin-inline-state">Для выбранного stated UUID есть active mapping: {activeMapping.mapping_id}. Новая связь будет replacement только после fresh review.</p> : null}
            {review ? <section className="cognitive-twin-review" aria-labelledby="cognitive-twin-review-title"><h5 id="cognitive-twin-review-title">Review готов к проверке владельца</h5><p className="cognitive-twin-claim-text">{review.claim_text}</p><dl className="cognitive-twin-fields"><div><dt>Dimension</dt><dd>{review.stated.dimension}</dd></div><div><dt>Домен</dt><dd>{review.cohort_domain}</dd></div><div><dt>Evidence time</dt><dd>{review.stated.evidence_at}</dd></div><div><dt>Pattern</dt><dd>{patternLabel(review.pattern_type)} · {stateLabel(review.pattern_state)}</dd></div></dl><div className="cognitive-twin-review-context"><h6>Текущий decision context</h6><p><strong>Situation:</strong> {safeText(review.situation)}</p><p><strong>Information known:</strong> {safeText(review.information_known_at_decision_time)}</p><p><strong>Criteria:</strong> {review.criteria.join(" · ") || "—"}</p><p><strong>Options:</strong> {review.ordered_options.map((option) => `${option.option_index}: ${option.label}`).join(" · ")}</p></div><p className="cognitive-twin-review-notice">Labels shown here are only for human review. The application does not automatically match them by text.</p><details className="cognitive-twin-technical"><summary>Provenance и exact fingerprints</summary><dl className="cognitive-twin-fields cognitive-twin-fields-technical"><div><dt>Stated fingerprint</dt><dd>{review.stated.claim_fingerprint}</dd></div><div><dt>Cohort fingerprint</dt><dd>{safeText(review.behavioral.cohort?.cohort_fingerprint)}</dd></div><div><dt>Selected option fingerprint</dt><dd>{review.behavioral.option.option_fingerprint}</dd></div><div><dt>Candidate mapping fingerprint</dt><dd>{review.candidate_mapping_fingerprint}</dd></div><div><dt>Support</dt><dd>{review.behavioral.source_count} exact source UUID</dd></div></dl></details><label className="cognitive-twin-confirm-label"><input type="checkbox" checked={confirmed} disabled={confirmBusy || sameMappingAlreadyActive} onChange={(event) => setConfirmed(event.target.checked)} />Я проверил Stated, Observed и context выше и явно подтверждаю эту exact связь.</label><div className="cognitive-twin-confirm-actions"><button className="cognitive-twin-button cognitive-twin-button-primary" type="button" disabled={!confirmed || confirmBusy || sameMappingAlreadyActive} aria-busy={confirmBusy} onClick={() => void confirmReview()}>{confirmBusy ? "Подтверждаю…" : sameMappingAlreadyActive ? "Эта связь уже active" : "Подтвердить mapping"}</button>{confirmBusy ? <button className="cognitive-twin-button cognitive-twin-button-secondary" type="button" onClick={cancelConfirm}>Отменить</button> : null}</div></section> : null}
            {acceptedMappingId ? <p className="cognitive-twin-success" ref={feedbackRef} tabIndex={-1} role="status" aria-live="polite">Accepted mapping UUID: {acceptedMappingId}. История immutable; in-place edit не выполняется.</p> : null}
            {compositionError ? <p className="capture-error cognitive-twin-error" role="alert" tabIndex={-1}>{compositionError}</p> : null}
            {composition ? <CompositionResult result={composition} /> : null}
            <details className="cognitive-twin-technical"><summary>Lifecycle</summary><ul className="cognitive-twin-lifecycle-list">{bundle.mappings.mappings.map((mapping) => <li key={mapping.mapping_id}><span>{mapping.lifecycle_state}</span><span>{mapping.source_note_uuid}</span><span>{mapping.mapping_id}</span></li>)}</ul>{bundle.mappings.mappings.length === 0 ? <p>Active mapping отсутствует.</p> : null}</details>
          </section>
        </div>
      )}
    </section>
  );
}
