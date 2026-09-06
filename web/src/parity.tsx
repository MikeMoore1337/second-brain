import {
  applyPersonalMemory,
  applySave,
  createTextDraft,
  createUrlDraft,
  applyDecision,
  applyOutcome,
  previewDraft,
  loadSelfModel,
  loadSelfRetrieval,
  loadTimeline,
  prepareDecision,
  prepareOutcome,
  preparePersonalMemory,
  prepareSave,
  retrieveNote,
  searchNotes,
  simulateMe,
  transcribeAudio,
  type DraftResponse,
  type NoteDraft,
  type PersonalMemoryPayload,
  type SavePlanResponse,
  type SavedNoteResponse,
} from "./api";
import { Icon } from "./icons";
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type ReactElement, type ReactNode } from "react";

const NOTE_TYPES = ["project", "area", "resource", "zettel"] as const;
const AUDIO_TYPES = ["audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/x-m4a"] as const;
const MAX_AUDIO_BYTES = 15 * 1024 * 1024;

export function displayValue(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.length > 0) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "да" : "нет";
  return fallback;
}

export function lines(value: string): string[] {
  return value.split(/\r?\n/).filter((item) => item.length > 0);
}

function responseError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function Field({ label, value }: { label: string; value: unknown }): ReactElement {
  return (
    <div className="draft-field">
      <dt className="draft-field-label">{label}</dt>
      <dd className="draft-field-value">{displayValue(value)}</dd>
    </div>
  );
}

function ErrorMessage({ message }: { message: string }): ReactElement | null {
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => { errorRef.current?.focus(); }, [message]);
  if (!message) return null;
  return (
    <p className="capture-error" role="alert" tabIndex={-1} ref={errorRef}>
      {message}
    </p>
  );
}

function SavePlan({ plan, title }: { plan: SavePlanResponse; title: string }): ReactElement {
  return (
    <section className="save-plan" tabIndex={-1}>
      <h4>{title}</h4>
      <dl className="draft-fields">
        <Field label="Тип" value={plan.note.type} />
        <Field label="Путь" value={plan.note.relative_path} />
      </dl>
      <h5>Предлагаемый Markdown-файл</h5>
      <pre className="save-diff">{plan.diff}</pre>
    </section>
  );
}

function SavedNote({ payload, label, addOutcome }: { payload: SavedNoteResponse; label: string; addOutcome?: () => void }): ReactElement {
  return (
    <section className="saved-note" tabIndex={-1}>
      <h4>{label}</h4>
      <dl className="draft-fields">
        <Field label="Путь" value={payload.note.relative_path} />
        <Field label="ID" value={payload.note.id} />
        <Field label="Создано" value={payload.note.created} />
      </dl>
      {addOutcome ? <button className="review-button review-button-secondary" type="button" onClick={addOutcome}>Добавить outcome</button> : null}
    </section>
  );
}

interface DraftReviewProps {
  response: DraftResponse;
  onReset: () => void;
  allowPersonalMemory: boolean;
}

function DraftReview({ response, onReset, allowPersonalMemory }: DraftReviewProps): ReactElement {
  const [draft, setDraft] = useState<NoteDraft>({
    title: response.draft.title,
    note_type: NOTE_TYPES.includes(response.draft.note_type as (typeof NOTE_TYPES)[number]) ? response.draft.note_type : "resource",
    content: response.draft.content,
    tags: [...response.draft.tags],
    links: [...response.draft.links],
  });
  const [personalMemoryEnabled, setPersonalMemoryEnabled] = useState(false);
  const [evidenceKind, setEvidenceKind] = useState("");
  const [selfKind, setSelfKind] = useState("");
  const [timeMode, setTimeMode] = useState<"exact" | "unknown">("unknown");
  const [evidenceAt, setEvidenceAt] = useState("unknown");
  const [domain, setDomain] = useState("");
  const [plan, setPlan] = useState<SavePlanResponse | null>(null);
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedNoteResponse | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  function invalidate(message = ""): void {
    if (!saved) {
      setPlan(null);
      setConfirmationToken(null);
      if (message) setStatus(message);
    }
  }

  function updateDraft<K extends keyof NoteDraft>(key: K, value: NoteDraft[K]): void {
    setDraft((current) => ({ ...current, [key]: value }));
    invalidate("Изменения требуют новой подготовки Safe Write.");
  }

  function personalMemoryPayload(): PersonalMemoryPayload | null {
    if (!personalMemoryEnabled) return null;
    return {
      evidence_kind: evidenceKind,
      self_kind: selfKind,
      evidence_at: timeMode === "exact" ? evidenceAt : "unknown",
      evidence_at_precision: timeMode,
      domain: domain.trim() ? domain : null,
    };
  }

  async function handlePreview(): Promise<void> {
    setBusy(true);
    setError("");
    setStatus("Строю preview…");
    try {
      const payload = await previewDraft(draft.content);
      setPreview(payload.html);
      setStatus("Preview построен.");
    } catch (caught) {
      setError(responseError(caught, "Не удалось построить preview."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function handlePrepare(): Promise<void> {
    const memory = personalMemoryPayload();
    if (memory && (!memory.evidence_kind || !memory.self_kind || (memory.evidence_at_precision === "exact" && !memory.evidence_at))) {
      setError("Заполни обязательные поля Personal Memory.");
      return;
    }
    setBusy(true);
    setError("");
    setStatus("Выполняю Safe Write dry-run…");
    try {
      const nextPlan = memory
        ? await preparePersonalMemory(response.review_token, draft, memory)
        : await prepareSave(response.review_token, draft);
      setPlan(nextPlan);
      setConfirmationToken(nextPlan.confirmation_token);
      setStatus("Проверь полный diff и подтверди сохранение.");
    } catch (caught) {
      setError(responseError(caught, "Не удалось подготовить сохранение."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(): Promise<void> {
    if (!confirmationToken) return;
    const memory = personalMemoryPayload();
    setBusy(true);
    setError("");
    setStatus("Сохраняю заметку в vault…");
    try {
      const result = memory
        ? await applyPersonalMemory(response.review_token, confirmationToken, draft, memory)
        : await applySave(response.review_token, confirmationToken, draft);
      setSaved(result);
      setConfirmationToken(null);
      setStatus("Заметка сохранена в vault.");
    } catch (caught) {
      setError(responseError(caught, "Не удалось сохранить заметку."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="draft-result" tabIndex={-1}>
      <h3>Черновик готов</h3>
      {response.sources.map((source, index) => (
        <section className="provenance-block" key={`${source.uri ?? "source"}-${index}`}>
          <h4>Источник (только чтение)</h4>
          <dl className="draft-fields provenance-fields">
            <Field label="URI" value={source.uri} />
            <Field label="Тип" value={source.kind} />
            <Field label="Получен" value={source.retrieved_at} />
            <Field label="Опубликован" value={source.published_at} />
            <Field label="Заголовок" value={source.title} />
            <Field label="Автор" value={source.author} />
            <Field label="Upstream ID" value={source.upstream_id} />
          </dl>
        </section>
      ))}
      <section className="review-editor" aria-busy={busy}>
        <h4>Проверь и отредактируй</h4>
        <div className="review-fields">
          <label className="review-field draft-field-label">Название<input className="review-input" type="text" value={draft.title} disabled={busy || Boolean(saved)} onChange={(event) => updateDraft("title", event.target.value)} /></label>
          <label className="review-field draft-field-label">Тип заметки<select className="review-input" value={draft.note_type} disabled={busy || Boolean(saved)} onChange={(event) => updateDraft("note_type", event.target.value)}>{NOTE_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="review-field draft-field-label">Теги (один на строку)<textarea className="review-input" rows={3} value={draft.tags.join("\n")} disabled={busy || Boolean(saved)} onChange={(event) => updateDraft("tags", lines(event.target.value))} /></label>
          <label className="review-field draft-field-label">Ссылки (одна на строку)<textarea className="review-input" rows={3} value={draft.links.join("\n")} disabled={busy || Boolean(saved)} onChange={(event) => updateDraft("links", lines(event.target.value))} /></label>
          <label className="review-field review-field-wide draft-field-label">Содержание<textarea className="review-input" rows={12} value={draft.content} disabled={busy || Boolean(saved)} onChange={(event) => updateDraft("content", event.target.value)} /></label>
        </div>
        {allowPersonalMemory ? <section className="personal-memory-panel">
          <h5>Личная память</h5>
          <p className="personal-memory-description">Включи режим только после проверки draft и явно укажи Stage 1 metadata.</p>
          <label className="personal-memory-toggle"><input type="checkbox" checked={personalMemoryEnabled} disabled={busy || Boolean(saved)} aria-controls="personal-memory-fields" aria-expanded={personalMemoryEnabled} onChange={(event) => { setPersonalMemoryEnabled(event.target.checked); invalidate("Изменения требуют новой подготовки Safe Write."); }} /><span>Сохранить как Personal Memory</span></label>
          <div className="personal-memory-fields" id="personal-memory-fields" hidden={!personalMemoryEnabled}>
            <label className="review-field draft-field-label">Основание<select className="review-input" value={evidenceKind} disabled={busy || Boolean(saved)} onChange={(event) => { setEvidenceKind(event.target.value); invalidate(); }}><option value="" disabled>Выбери тип основания</option><option value="explicit_user_fact">Факт обо мне / моей ситуации</option><option value="user_statement">Моё утверждение, мнение, цель или самоописание</option></select></label>
            <label className="review-field draft-field-label">Что сохраняем о себе<select className="review-input" value={selfKind} disabled={busy || Boolean(saved)} onChange={(event) => { setSelfKind(event.target.value); invalidate(); }}><option value="" disabled>Выбери тип памяти</option><option value="memory">Память</option><option value="preference">Предпочтение</option><option value="belief">Убеждение</option><option value="goal">Цель</option></select></label>
            <label className="review-field draft-field-label">Домен (необязательно)<input className="review-input" type="text" value={domain} disabled={busy || Boolean(saved)} onChange={(event) => { setDomain(event.target.value); invalidate(); }} /></label>
            <label className="review-field draft-field-label">Время факта<select className="review-input" value={timeMode} disabled={busy || Boolean(saved)} onChange={(event) => { const value = event.target.value as "exact" | "unknown"; setTimeMode(value); setEvidenceAt(value === "exact" ? "" : "unknown"); invalidate(); }}><option value="exact">Точное время</option><option value="unknown">Время неизвестно</option></select></label>
            {timeMode === "exact" ? <div className="personal-memory-time-field"><label className="draft-field-label" htmlFor="personal-memory-evidence-at">RFC3339 время факта</label><input className="review-input" id="personal-memory-evidence-at" type="text" value={evidenceAt} disabled={busy || Boolean(saved)} onChange={(event) => { setEvidenceAt(event.target.value); invalidate(); }} /><button className="review-button review-button-secondary" type="button" disabled={busy || Boolean(saved)} onClick={() => { setEvidenceAt(new Date().toISOString()); invalidate(); }}>Сейчас</button></div> : null}
          </div>
        </section> : null}
        <div className="review-actions">
          <button className="review-button review-button-secondary" type="button" disabled={busy || Boolean(saved)} aria-busy={busy} onClick={() => void handlePreview()}>Предпросмотр</button>
          <button className="review-button review-button-primary" type="button" disabled={busy || Boolean(saved)} aria-busy={busy} onClick={() => void handlePrepare()}>Подготовить сохранение</button>
          <button className="review-button review-button-primary" type="button" hidden={!confirmationToken} disabled={busy || !confirmationToken || Boolean(saved)} aria-busy={busy} onClick={() => void handleConfirm()}>Подтвердить сохранение</button>
          <button className="review-button review-button-quiet" type="button" disabled={busy} onClick={onReset}>Добавить ещё</button>
        </div>
        <p className="review-status" role="status" aria-live="polite">{status}</p>
        <ErrorMessage message={error} />
        {plan ? <SavePlan plan={plan} title={personalMemoryEnabled ? "План Personal Memory Safe Write (dry-run)" : "План Safe Write (dry-run)"} /> : null}
        {preview !== null ? <section className="markdown-preview"><h4>Preview</h4><div dangerouslySetInnerHTML={{ __html: preview }} /></section> : null}
        {saved ? <SavedNote payload={saved} label="Заметка сохранена" /> : null}
      </section>
    </div>
  );
}

export function CaptureSurface(): ReactElement {
  const [mode, setMode] = useState<"url" | "text" | "voice">("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [draftResponse, setDraftResponse] = useState<DraftResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioType, setAudioType] = useState("");
  const [voiceStatus, setVoiceStatus] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const microphoneGenerationRef = useRef(0);
  const [microphonePending, setMicrophonePending] = useState(false);

  const canRecord = typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && typeof window !== "undefined" && typeof window.MediaRecorder === "function";

  function stopStream(): void {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }

  function clearAudioState(): void {
    microphoneGenerationRef.current += 1;
    setMicrophonePending(false);
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        try { recorder.stop(); } catch { /* recorder may already be stopping */ }
      }
    }
    recorderRef.current = null;
    stopStream();
    chunksRef.current = [];
    setRecording(false);
    setAudioBlob(null);
    setAudioType("");
    setVoiceStatus("");
  }

  useEffect(() => () => {
    microphoneGenerationRef.current += 1;
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        try { recorder.stop(); } catch { /* component is unmounting */ }
      }
    }
    stopStream();
  }, []);

  function resetReview(): void {
    clearAudioState();
    setDraftResponse(null);
    setUrl("");
    setText("");
    setStatus("");
    setError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function makeDraft(): Promise<void> {
    setBusy(true);
    setError("");
    setStatus("Выполняю операцию…");
    try {
      const result = mode === "url" ? await createUrlDraft(url) : await createTextDraft(text);
      setDraftResponse(result);
      setStatus("Черновик готов к проверке.");
    } catch (caught) {
      setError(responseError(caught, "Не удалось создать черновик."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (mode === "voice" || busy) return;
    await makeDraft();
  }

  async function startRecording(): Promise<void> {
    if (!canRecord || busy || recording || microphonePending) return;
    const generation = ++microphoneGenerationRef.current;
    setMicrophonePending(true);
    setError("");
    setVoiceStatus("Запрашиваю доступ к микрофону…");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation !== microphoneGenerationRef.current || mode !== "voice") {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const mimeType = typeof MediaRecorder.isTypeSupported === "function"
        ? ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/ogg"].find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? ""
        : "";
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => { if (event.data.size > 0) chunksRef.current.push(event.data); };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        recorderRef.current = null;
        setRecording(false);
        if (blob.size > MAX_AUDIO_BYTES) {
          setAudioBlob(null);
          setVoiceStatus("Запись превысила лимит 15 MiB.");
        } else if (blob.size === 0) {
          setAudioBlob(null);
          setVoiceStatus("Запись не содержит audio-данных.");
        } else {
          setAudioBlob(blob);
          setAudioType((recorder.mimeType || "audio/webm").split(";", 1)[0].toLowerCase());
          setVoiceStatus("Аудио готово к распознаванию.");
        }
      };
      recorderRef.current = recorder;
      recorder.start(1000);
      setRecording(true);
      setVoiceStatus("Идёт запись…");
    } catch {
      if (generation === microphoneGenerationRef.current) {
        clearAudioState();
        setError("Не удалось получить доступ к microphone.");
        setVoiceStatus("");
      }
    } finally {
      if (generation === microphoneGenerationRef.current) setMicrophonePending(false);
    }
  }

  function stopRecording(): void {
    if (recording && recorderRef.current && recorderRef.current.state !== "inactive") recorderRef.current.stop();
  }

  function handleAudioFile(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    if (!file) return;
    const type = file.type.split(";", 1)[0].toLowerCase();
    if (!AUDIO_TYPES.includes(type as (typeof AUDIO_TYPES)[number])) {
      setAudioBlob(null);
      setError("Выбери поддерживаемый audio-файл.");
      return;
    }
    if (file.size === 0 || file.size > MAX_AUDIO_BYTES) {
      setAudioBlob(null);
      setError("Размер audio-файла должен быть от 1 байта до 15 MiB.");
      return;
    }
    setError("");
    setAudioBlob(file);
    setAudioType(type);
    setVoiceStatus("Аудио готово к распознаванию.");
  }

  async function handleTranscribe(): Promise<void> {
    if (!audioBlob || busy) return;
    setBusy(true);
    setError("");
    setVoiceStatus("Распознаю audio…");
    try {
      const transcript = await transcribeAudio(audioBlob, audioType || audioBlob.type || "audio/webm");
      setText(transcript.transcript.text);
      clearAudioState();
      setMode("text");
      setStatus("Проверь расшифровку и затем создай черновик.");
    } catch (caught) {
      setError(responseError(caught, "Не удалось распознать audio."));
      setVoiceStatus("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="entry-point" aria-labelledby="entry-title">
      <div className="entry-icon" aria-hidden="true"><Icon name="add" size={24} /></div>
      <div className="entry-copy">
        <p className="entry-kicker">Новая заметка</p>
        <h2 id="entry-title">Добавить знание</h2>
        <p id="add-description">Получи структурированный черновик из текста, голоса или публичной страницы.</p>
        {!draftResponse ? <div className="capture-panel" data-capture-panel aria-busy={busy}>
          <div className="mode-switch" role="group" aria-label="Режим добавления">
            {(["url", "text", "voice"] as const).map((item) => <button className={`mode-button${mode === item ? " is-active" : ""}`} key={item} type="button" disabled={busy || microphonePending} aria-pressed={mode === item} onClick={() => { if (item !== "voice") clearAudioState(); setMode(item); setError(""); }}>{item === "url" ? "URL" : item === "text" ? "Text" : "Voice"}</button>)}
          </div>
          {mode !== "voice" ? <form className="capture-form" aria-describedby="add-description" onSubmit={(event) => void handleSubmit(event)}>
            <label className="capture-label" htmlFor={mode === "url" ? "source-input" : "text-input"}>{mode === "url" ? "Публичный URL" : "Текст материала"}</label>
            {mode === "url" ? <input className="capture-input" id="source-input" type="url" placeholder="example.com/article" autoComplete="off" required value={url} disabled={busy} onChange={(event) => setUrl(event.target.value)} /> : <textarea className="capture-input capture-textarea" id="text-input" rows={7} placeholder="Вставь текст, который нужно превратить в заметку" autoComplete="off" required value={text} disabled={busy} onChange={(event) => setText(event.target.value)} />}
            <div className="capture-actions"><button className="capture-submit" type="submit" disabled={busy} aria-busy={busy}>{busy ? "Создаю…" : "Создать черновик"}</button><span className="capture-status" role="status" aria-live="polite">{status}</span></div>
          </form> : <section className="voice-panel" aria-labelledby="voice-title" aria-describedby="voice-description">
            <div><p className="voice-kicker">Голосовой вход</p><h3 id="voice-title">Скажи, что хочешь сохранить</h3><p id="voice-description" className="voice-description">Запиши короткую заметку или выбери audio-файл. После распознавания проверь текст перед созданием черновика.</p></div>
            <div className="voice-actions"><button className="voice-button voice-button-primary" type="button" disabled={busy || recording || microphonePending || !canRecord} onClick={() => void startRecording()}>Записать голос</button><button className="voice-button voice-button-secondary" type="button" disabled={busy || !recording} hidden={!recording} onClick={stopRecording}>Остановить</button><label className="voice-file-label">Выбрать audio-файл<input ref={fileInputRef} className="voice-file-input" type="file" accept={AUDIO_TYPES.join(",")} disabled={busy || recording || microphonePending} onChange={handleAudioFile} /></label><button className="voice-button voice-button-primary" type="button" disabled={busy || recording || microphonePending || !audioBlob} onClick={() => void handleTranscribe()}>Распознать</button></div>
            <p className="voice-status" role="status" aria-live="polite">{voiceStatus}</p>
          </section>}
          {error ? <ErrorMessage message={error} /> : null}
        </div> : <>
          <p className="capture-status" role="status" aria-live="polite">{status}</p>
          <DraftReview response={draftResponse} onReset={resetReview} allowPersonalMemory={draftResponse.sources.length === 0} />
        </>}
      </div>
    </section>
  );
}

export function SectionHeading({ eyebrow, title, children, id }: { eyebrow: string; title: string; children?: ReactNode; id: string }): ReactElement {
  return <div className="section-heading"><div><p className="eyebrow">{eyebrow}</p><h2 id={id}>{title}</h2></div>{children ? <p className="timeline-lede">{children}</p> : null}</div>;
}

export function TimelineSurface(): ReactElement {
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [data, setData] = useState<import("./api").TimelineResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function load(nextOrder = order): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError("");
    setStatus("Обновляю текущую Timeline…");
    try {
      setData(await loadTimeline(nextOrder));
      setStatus("Показана текущая Timeline из vault.");
    } catch (caught) {
      setError(responseError(caught, "Сервис Timeline недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const renderItem = (item: import("./api").TimelineItem, known: boolean): ReactElement => <article className={known ? "timeline-item" : "timeline-item timeline-item-unknown"} key={item.id}><h4 className="timeline-item-title">{known ? "Событие" : "Время неизвестно"}</h4><dl className="timeline-fields">{known ? <Field label="Время события" value={item.event_at} /> : null}<Field label="Событие" value={item.event_kind} /><Field label="Evidence" value={item.evidence_kind} /><Field label="Summary" value={item.summary} /><Field label="Домен" value={item.domain} /><Field label="Путь" value={item.relative_path} /><Field label="UUID" value={item.id} /><Field label="Сохранено" value={item.storage_created_at} />{item.storage_updated_at ? <Field label="Обновлено в хранилище" value={item.storage_updated_at} /> : null}{item.related_note_ids?.length ? <Field label="Decision" value={item.related_note_ids.join(", ")} /> : null}</dl></article>;
  const total = (shown: number, count: number): string => shown < count ? `Показано: ${shown} / ${count} — история ограничена лимитом` : `Показано: ${shown} / ${count}`;

  return <section className="timeline-surface" id="timeline" aria-labelledby="timeline-title" data-timeline-surface aria-busy={busy}>
    <SectionHeading eyebrow="Stage 3 · current evidence" title="Personal Timeline." id="timeline-title">Текущая chronology из canonical evidence — с честным разделением события и времени хранения.</SectionHeading>
    <div className="timeline-panel"><div className="timeline-controls"><label className="timeline-order-control"><span className="draft-field-label">Порядок</span><select className="review-input" value={order} disabled={busy} onChange={(event) => { const next = event.target.value as "asc" | "desc"; setOrder(next); void load(next); }}><option value="desc">Сначала новые</option><option value="asc">Сначала старые</option></select></label><button className="review-button review-button-secondary" type="button" disabled={busy} aria-busy={busy} onClick={() => void load()}>Обновить</button><p className="timeline-status" role="status" aria-live="polite">{status}</p></div>{error ? <p className="capture-error timeline-error" role="alert" tabIndex={-1}>{error}</p> : null}
      <section className="timeline-group" aria-labelledby="timeline-known-title"><div className="timeline-group-heading"><div><p className="eyebrow">Known event time</p><h3 id="timeline-known-title">События с известным временем</h3></div><p className="timeline-total">{data ? total(data.known_items.length, data.known_total) : ""}</p></div><ol className="timeline-list">{data?.known_items.map((item) => renderItem(item, true))}</ol>{data && data.known_items.length === 0 ? <p className="timeline-empty">Пока нет событий с известным временем.</p> : null}</section>
      <section className="timeline-group timeline-group-unknown" aria-labelledby="timeline-unknown-title"><div className="timeline-group-heading"><div><p className="eyebrow">Unknown event time</p><h3 id="timeline-unknown-title">Время неизвестно</h3></div><p className="timeline-total">{data ? total(data.unknown_items.length, data.unknown_total) : ""}</p></div><div className="timeline-list">{data?.unknown_items.map((item) => renderItem(item, false))}</div>{data && data.unknown_items.length === 0 ? <p className="timeline-empty">Пока нет событий без известного времени.</p> : null}</section>
    </div>
  </section>;
}

export function SelfModelSurface(): ReactElement {
  const [data, setData] = useState<import("./api").SelfModelResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function load(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError("");
    setStatus("Строю текущий Self Model…");
    try {
      setData(await loadSelfModel());
      setStatus("Показан текущий Self Model из vault.");
    } catch (caught) {
      setError(responseError(caught, "Сервис Self Model недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  const renderEvidence = (ref: import("./api").SelfModelEvidence): ReactElement => <li className="self-model-evidence" key={`${ref.id ?? "evidence"}-${ref.evidence_kind ?? "kind"}`}><dl className="self-model-fields"><Field label="Canonical UUID" value={ref.id} /><Field label="Evidence" value={ref.evidence_kind} /><Field label="Self kind" value={ref.self_kind} /><Field label="Домен" value={ref.domain} /><Field label="Evidence time" value={ref.evidence_at} /><Field label="Precision" value={ref.evidence_at_precision} />{ref.related_note_ids?.length ? <Field label="Related UUIDs" value={ref.related_note_ids.join(", ")} /> : null}</dl></li>;
  const evidenceGroup = (label: string, refs: readonly import("./api").SelfModelEvidence[]): ReactElement | null => refs.length ? <><h4 className="self-model-evidence-heading">{label}</h4><ul className="self-model-evidence-list">{refs.map(renderEvidence)}</ul></> : null;
  const renderClaim = (claim: import("./api").SelfModelClaim): ReactElement => <article className="self-model-claim" key={`${claim.dimension ?? "claim"}-${claim.claim ?? "text"}`}><h3 className="self-model-claim-title">{displayValue(claim.dimension, "Claim")}</h3><p className="self-model-claim-text">{displayValue(claim.claim)}</p><dl className="self-model-fields"><Field label="Домен" value={claim.domain} /><Field label="Confidence" value={claim.confidence.state} /><Field label="Confidence policy" value={claim.confidence.policy_version} /><Field label="Supporting evidence" value={claim.confidence.supporting_evidence_count} /><Field label="Contradicting evidence" value={claim.confidence.contradicting_evidence_count} /><Field label="Unknown time" value={claim.confidence.unknown_time_count} /><Field label="Earliest evidence time" value={claim.temporal_context.earliest_known_evidence_at} /><Field label="Latest evidence time" value={claim.temporal_context.latest_known_evidence_at} /><Field label="Known evidence" value={claim.temporal_context.known_evidence_count} /><Field label="Unknown evidence" value={claim.temporal_context.unknown_evidence_count} /><Field label="Generated" value={claim.generated_at} /><Field label="Derivation" value={claim.derivation_version} /></dl><div className="self-model-evidence">{evidenceGroup("Supporting evidence", claim.supporting_evidence)}{evidenceGroup("Contradicting evidence", claim.contradicting_evidence)}{evidenceGroup("Contextual evidence", claim.contextual_evidence)}</div></article>;

  return <section className="self-model-surface" id="self-model" aria-labelledby="self-model-title" data-self-model-surface aria-busy={busy}><SectionHeading eyebrow="Stage 4 · derived, current, explainable" title="Self Model." id="self-model-title">Прямые preference, belief и goal claims из текущего vault — с canonical UUID evidence и без скрытого профиля.</SectionHeading><div className="self-model-panel"><div className="self-model-controls"><button className="review-button review-button-secondary" type="button" disabled={busy} aria-busy={busy} onClick={() => void load()}>Построить / обновить</button><p className="self-model-status" role="status" aria-live="polite">{status}</p></div>{error ? <p className="capture-error self-model-error" role="alert" tabIndex={-1}>{error}</p> : null}<p className="self-model-summary">{data ? `Claims: ${data.claims.length} · Eligible evidence: ${displayValue(data.eligible_evidence_count)} · Represented evidence: ${displayValue(data.represented_evidence_count)} · Generated: ${displayValue(data.generated_at)} · Derivation: ${displayValue(data.derivation_version)} · Policy fingerprint: ${displayValue(data.policy_fingerprint)}` : ""}</p><div className="self-model-list">{data?.claims.map(renderClaim)}</div>{data && data.claims.length === 0 ? <p className="self-model-empty">Прямых preference, belief или goal claims пока нет.</p> : null}</div></section>;
}

export function SelfRetrievalSurface(): ReactElement {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<import("./api").SelfRetrievalResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy) return;
    if (!query.trim()) {
      setError("Введи поисковый запрос.");
      return;
    }
    setBusy(true);
    setError("");
    setData(null);
    setStatus("Собираю текущий контекст…");
    try {
      setData(await loadSelfRetrieval(query));
      setStatus("Показан текущий bounded context из vault.");
    } catch (caught) {
      setError(responseError(caught, "Сервис Self Retrieval недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  const tags = (values: readonly string[] | undefined): ReactElement => <div className="search-tags">{values?.length ? values.map((tag) => <span className="search-tag" key={tag}>{tag}</span>) : <span className="search-tag search-tag-empty">без тегов</span>}</div>;
  const claim = (value: import("./api").SelfRetrievalClaim): ReactElement => <li className="self-retrieval-claim" key={`${value.dimension ?? "claim"}-${value.claim ?? "text"}`}><dl className="self-retrieval-fields"><Field label="Dimension" value={value.dimension} /><Field label="Claim (already derived)" value={value.claim} /><Field label="Supporting UUIDs" value={value.supporting_note_ids.join(", ")} /><Field label="Derivation" value={value.derivation_version} /><Field label="Policy fingerprint" value={value.policy_fingerprint} /></dl></li>;
  const item = (value: import("./api").SelfRetrievalItem): ReactElement => <article className="self-retrieval-item" key={value.note_id}><h3 className="self-retrieval-item-title">{displayValue(value.title, "Без названия")}</h3><dl className="self-retrieval-fields"><Field label="Canonical UUID" value={value.note_id} /><Field label="Тип заметки" value={value.note_type} /><Field label="Search order (audit only)" value={value.search_rank} /><Field label="Создано" value={value.created} /><Field label="Обновлено" value={value.updated} /></dl>{tags(value.tags)}<h4>Current reread body (только чтение)</h4><pre className="self-retrieval-body">{value.body}</pre><h4>Exact Self Model claim links</h4><ul className="self-retrieval-claims">{value.self_model_claims.length ? value.self_model_claims.map(claim) : <li className="self-retrieval-no-claims">Для этой заметки нет supporting UUID link.</li>}</ul></article>;

  return <section className="self-retrieval-surface" id="self-retrieval" aria-labelledby="self-retrieval-title" data-self-retrieval-surface aria-busy={busy}><SectionHeading eyebrow="Stage 5 · current context" title="Собрать контекст." id="self-retrieval-title">Лексический запрос, текущая перечитка заметок и только точные supporting UUID links из Self Model.</SectionHeading><form className="self-retrieval-form" onSubmit={(event) => void handleSubmit(event)}><label className="capture-label" htmlFor="self-retrieval-input">Запрос для контекста</label><div className="self-retrieval-form-row"><input className="capture-input" id="self-retrieval-input" type="search" placeholder="Например, fastapi testing" autoComplete="off" required value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /><button className="capture-submit" type="submit" data-self-retrieval-submit disabled={busy} aria-busy={busy}>Собрать</button></div><p className="self-retrieval-status" role="status" aria-live="polite">{status}</p></form>{error ? <p className="capture-error self-retrieval-error" role="alert" tabIndex={-1}>{error}</p> : null}<p className="self-retrieval-summary">{data ? `Candidates: ${displayValue(data.candidate_count)} · Current items: ${displayValue(data.included_count)} · Excluded: ${displayValue(data.excluded_count)} · Content bytes: ${displayValue(data.content_bytes)} · Truncated: ${displayValue(data.truncated)} · Derivation: ${displayValue(data.self_model_derivation_version)} · Policy fingerprint: ${displayValue(data.self_model_policy_fingerprint)}` : ""}</p>{data && data.items.length === 0 ? <p className="self-retrieval-empty">По этому запросу текущий контекст не найден.</p> : null}<div className="self-retrieval-results" aria-live="polite">{data?.items.map(item)}{data?.exclusions.length ? <section className="self-retrieval-exclusions"><h3>Исключения текущей сборки</h3><ul>{data.exclusions.map((exclusion, index) => <li key={`${exclusion.search_rank ?? index}-${exclusion.reason ?? "reason"}`}>Search order {displayValue(exclusion.search_rank)} · {displayValue(exclusion.reason)}</li>)}</ul></section> : null}</div></section>;
}

export function SimulateMeSurface(): ReactElement {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState([{ id: "a", label: "" }]);
  const [data, setData] = useState<import("./api").SimulateMeResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  function updateOption(index: number, key: "id" | "label", value: string): void {
    setOptions((current) => current.map((option, optionIndex) => optionIndex === index ? { ...option, [key]: value } : option));
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setData(null);
    setStatus("Строю текущий прогноз…");
    try {
      setData(await simulateMe(query, options));
      setStatus("Показан текущий результат core.");
    } catch (caught) {
      setError(responseError(caught, "Сервис Simulate Me недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  const evidence = (value: import("./api").SimulateMeEvidence): ReactElement => <li className="simulate-me-evidence-item" key={`${value.claim_id ?? "claim"}-${value.dimension ?? "dimension"}`}><dl className="simulate-me-fields"><Field label="Claim UUID" value={value.claim_id} /><Field label="Dimension" value={value.dimension} /><Field label="Canonical note UUIDs" value={value.note_ids.join(", ")} /><Field label="Evidence time" value={value.evidence_at} /></dl></li>;
  const referenceGroup = (title: string, values: readonly import("./api").SimulateMeEvidence[]): ReactElement | null => values.length ? <><h4>{title}</h4><ul className="simulate-me-evidence-list">{values.map(evidence)}</ul></> : null;

  return <section className="simulate-me-surface" id="simulate-me" aria-labelledby="simulate-me-title" data-simulate-me-surface aria-busy={busy}><SectionHeading eyebrow="Stage 6 · mechanical, current, bounded" title="Simulate Me." id="simulate-me-title">Получай только механический <strong>ПРОГНОЗ</strong> по exact current evidence — без изменения vault.</SectionHeading><div className="simulate-me-panel"><form className="simulate-me-form" onSubmit={(event) => void submit(event)}><label className="simulate-me-field simulate-me-field-wide" htmlFor="simulate-me-query"><span className="draft-field-label">Literal task / query</span><textarea className="capture-input capture-textarea" id="simulate-me-query" rows={3} autoComplete="off" required value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /></label><fieldset className="simulate-me-options"><legend>Caller-owned options</legend><p className="simulate-me-help">Введи варианты как есть. Core сам вернёт prediction или abstention; интерфейс только покажет этот результат.</p><div className="simulate-me-option-list">{options.map((option, index) => <div className="simulate-me-option-row" data-simulate-me-option key={index}><label className="simulate-me-field"><span className="draft-field-label">Option id</span><input className="review-input" type="text" value={option.id} autoComplete="off" disabled={busy} onChange={(event) => updateOption(index, "id", event.target.value)} /></label><label className="simulate-me-field"><span className="draft-field-label">Label</span><input className="review-input" type="text" required value={option.label} autoComplete="off" disabled={busy} onChange={(event) => updateOption(index, "label", event.target.value)} /></label>{options.length > 1 ? <button className="review-button review-button-quiet" type="button" disabled={busy} onClick={() => setOptions((current) => current.filter((_, optionIndex) => optionIndex !== index))}>Удалить</button> : null}</div>)}</div><button className="review-button review-button-secondary" type="button" disabled={busy || options.length >= 8} onClick={() => setOptions((current) => [...current, { id: "", label: "" }])}>Добавить вариант</button></fieldset><div className="simulate-me-actions"><button className="review-button review-button-primary" type="submit" disabled={busy} aria-busy={busy}>Получить прогноз</button><p className="simulate-me-status" role="status" aria-live="polite">{status}</p></div></form>{error ? <p className="capture-error simulate-me-error" role="alert" tabIndex={-1}>{error}</p> : null}{data ? <section className="simulate-me-result" aria-labelledby="simulate-me-result-title"><h3 id="simulate-me-result-title">Результат core</h3><div><h4 className="simulate-me-result-heading">{data.kind === "prediction" ? "ПРОГНОЗ" : "Прогноз не построен"}</h4><dl className="simulate-me-fields">{data.kind === "prediction" && data.selected_option ? <><Field label="Предсказанный вариант" value={data.selected_option.label} /><Field label="Option id" value={data.selected_option.id} /></> : <><Field label="Состояние" value="Недостаточно evidence" /><Field label="Abstention" value={data.abstention_code} /></>}<Field label="Derivation" value={data.derivation_version} /><Field label="Policy" value={data.policy_id} /><Field label="Policy fingerprint" value={data.policy_fingerprint} /></dl><div className="simulate-me-evidence">{referenceGroup("Supporting evidence", data.evidence_refs)}{referenceGroup("Contextual belief evidence", data.contextual_evidence_refs)}{data.temporal_caveats.length ? <><h4>Temporal caveats</h4><ul className="simulate-me-caveats">{data.temporal_caveats.map((caveat, index) => <li key={`${caveat.code ?? index}-${caveat.claim_id ?? "claim"}`}>{displayValue(caveat.code)} · claim {displayValue(caveat.claim_id)}</li>)}</ul></> : null}</div></div></section> : null}</div></section>;
}

export function SearchSurface(): ReactElement {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<import("./api").SearchResponse | null>(null);
  const [note, setNote] = useState<import("./api").RetrievedNote | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy || !query.trim()) return;
    setBusy(true);
    setError("");
    setNote(null);
    setStatus("Ищу в локальной памяти…");
    try {
      setData(await searchNotes(query));
      setStatus("Поиск завершён.");
    } catch (caught) {
      setError(responseError(caught, "Сервис Search недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function openNote(id: string): Promise<void> {
    if (opening) return;
    setOpening(id);
    setError("");
    try {
      setNote(await retrieveNote(id));
    } catch (caught) {
      setError(responseError(caught, "Не удалось открыть заметку."));
    } finally {
      setOpening(null);
    }
  }

  return <section className="search-surface" id="search" aria-labelledby="search-title" data-search-surface aria-busy={busy}><SectionHeading eyebrow="Private retrieval" title="Найти в памяти." id="search-title" /><form className="search-form" onSubmit={(event) => void submit(event)}><label className="capture-label" htmlFor="search-input">Поисковый запрос</label><div className="search-form-row"><input className="capture-input" id="search-input" name="query" type="search" placeholder="Например, fastapi testing" autoComplete="off" required value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /><button className="capture-submit" type="submit" disabled={busy} aria-busy={busy}>Найти</button></div><p className="search-status" role="status" aria-live="polite">{status}</p></form>{error ? <p className="capture-error search-error" role="alert" tabIndex={-1}>{error}</p> : null}{data && data.hits.length === 0 ? <p className="search-empty">По этому запросу ничего не найдено.</p> : null}<div className="search-results" aria-live="polite">{data?.hits.map((hit) => <article className="search-hit" key={hit.id}><h3 className="search-hit-title">{hit.title}</h3><dl className="search-hit-fields"><Field label="ID" value={hit.id} /><Field label="Тип" value={hit.type} /><Field label="Путь" value={hit.relative_path} /></dl>{hit.snippet ? <p className="search-hit-snippet">{hit.snippet}</p> : null}{hit.tags?.length ? <div className="search-tags">{hit.tags.map((tag) => <span className="search-tag" key={tag}>{tag}</span>)}</div> : null}<button className="review-button review-button-secondary" type="button" disabled={opening !== null} onClick={() => void openNote(hit.id)}>{opening === hit.id ? "Открываю…" : "Открыть"}</button></article>)}</div>{note ? <section className="retrieved-note" aria-labelledby="retrieved-note-title" tabIndex={-1}><h3 id="retrieved-note-title">{displayValue(note.title, "Заметка")}</h3><dl className="draft-fields"><Field label="ID" value={note.id} /><Field label="Тип" value={note.type} /><Field label="Путь" value={note.relative_path} /><Field label="Создано" value={note.created} /><Field label="Обновлено" value={note.updated} /></dl>{note.tags?.length ? <div className="search-tags">{note.tags.map((tag) => <span className="search-tag" key={tag}>{tag}</span>)}</div> : null}<h4>Содержание (только чтение)</h4><pre className="retrieved-note-body">{note.content}</pre></section> : null}</section>;
}

interface JournalDecisionFormProps {
  onAddOutcome: (id: string) => void;
  onBusyChange: (busy: boolean) => void;
}

function JournalDecisionForm({ onAddOutcome, onBusyChange }: JournalDecisionFormProps): ReactElement {
  const [value, setValue] = useState({ title: "", note_type: "project", tags: "", links: "", timeMode: "unknown" as "exact" | "unknown", evidenceAt: "unknown", domain: "", situation: "", options: "", information: "", criteria: "", chosen: "", reasons: "", confidence: "", expected: "" });
  const [plan, setPlan] = useState<SavePlanResponse | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedNoteResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  const optionValues = lines(value.options);
  const update = <K extends keyof typeof value>(key: K, next: (typeof value)[K]): void => { setValue((current) => ({ ...current, [key]: next })); setPlan(null); setToken(null); };
  const payload = (): import("./api").DecisionPayload => ({ title: value.title, note_type: value.note_type, tags: lines(value.tags), links: lines(value.links), evidence_at: value.timeMode === "exact" ? value.evidenceAt : "unknown", evidence_at_precision: value.timeMode, domain: value.domain.trim() ? value.domain : null, situation: value.situation, available_options: optionValues, information_known_at_decision_time: value.information, criteria: lines(value.criteria), chosen_option: value.chosen, reasons: value.reasons, confidence: value.confidence, expected_result: value.expected });
  async function prepare(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); if (busy || saved || optionValues.length < 2) return; setBusy(true); setError(""); setStatus("Готовлю Decision Journal dry-run…"); try { const next = await prepareDecision(payload()); setPlan(next); setToken(next.confirmation_token); setStatus("Проверь полный diff и подтверди сохранение."); } catch (caught) { setError(responseError(caught, "Не удалось подготовить Decision Journal.")); setStatus(""); } finally { setBusy(false); } }
  async function confirm(): Promise<void> { if (!token || busy) return; setBusy(true); setError(""); setStatus("Сохраняю Decision Journal в vault…"); try { const result = await applyDecision(token, payload()); setSaved(result); setToken(null); setStatus("Decision Journal сохранён. Можно добавить outcome."); } catch (caught) { setError(responseError(caught, "Не удалось сохранить Decision Journal.")); setStatus(""); } finally { setBusy(false); } }
  const text = (key: "title" | "tags" | "links" | "domain" | "situation" | "options" | "information" | "criteria" | "reasons" | "confidence" | "expected", label: string, required = false, rows?: number): ReactElement => <label className={`journal-field${rows ? " journal-field-wide" : ""}`}><span className="draft-field-label">{label}</span>{rows ? <textarea className="review-input" rows={rows} required={required} value={value[key]} disabled={busy || Boolean(saved)} onChange={(event) => update(key, event.target.value)} /> : <input className="review-input" type="text" required={required} value={value[key]} disabled={busy || Boolean(saved)} onChange={(event) => update(key, event.target.value)} />}</label>;
  return <form id="decision-form" className="journal-form" onSubmit={(event) => void prepare(event)}><p className="journal-form-intro">Сначала запиши только ситуацию, варианты и ожидание. Поздние результаты добавляются отдельной Outcome-записью позже.</p><div className="journal-fields">{text("title", "Название", true)}<label className="journal-field"><span className="draft-field-label">Тип заметки</span><select className="review-input" value={value.note_type} disabled={busy || Boolean(saved)} onChange={(event) => update("note_type", event.target.value)}>{NOTE_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>{text("tags", "Теги (один на строку)", false, 3)}{text("links", "Ссылки (одна на строку)", false, 3)}<div className="journal-time-grid"><label className="journal-field"><span className="draft-field-label">Время решения</span><select className="review-input" value={value.timeMode} disabled={busy || Boolean(saved)} onChange={(event) => { const next = event.target.value as "exact" | "unknown"; update("timeMode", next); update("evidenceAt", next === "exact" ? "" : "unknown"); }}><option value="exact">Точное время</option><option value="unknown">Время неизвестно</option></select></label>{value.timeMode === "exact" ? <label className="journal-field journal-evidence-field"><span className="draft-field-label">RFC3339 время</span><input className="review-input" type="text" required value={value.evidenceAt} disabled={busy || Boolean(saved)} onChange={(event) => update("evidenceAt", event.target.value)} /><button className="review-button review-button-secondary" type="button" disabled={busy || Boolean(saved)} onClick={() => update("evidenceAt", new Date().toISOString())}>Сейчас</button></label> : null}</div>{text("domain", "Домен (необязательно)")}{text("situation", "Situation · ситуация", true, 4)}{text("options", "Available options · варианты (один на строку)", true, 5)}<span className="journal-help">От 2 до 20 вариантов.</span>{text("information", "Information known at decision time · известная информация", true, 4)}{text("criteria", "Criteria · критерии (один на строку)", true, 4)}<label className="journal-field journal-field-wide"><span className="draft-field-label">Chosen option · выбранный вариант</span><select className="review-input" required value={value.chosen} disabled={busy || Boolean(saved)} onChange={(event) => update("chosen", event.target.value)}><option value="" disabled>{optionValues.length ? "Выбери вариант" : "Сначала добавь варианты"}</option>{optionValues.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>{text("reasons", "Reasons · причины", true, 4)}{text("confidence", "Confidence · уверенность", true, 3)}{text("expected", "Expected result · ожидаемый результат", true, 3)}</div><div className="journal-actions"><button className="review-button review-button-primary" type="submit" disabled={busy || Boolean(saved)} aria-busy={busy}>Подготовить сохранение</button><button className="review-button review-button-primary" type="button" hidden={!token} disabled={busy || !token || Boolean(saved)} aria-busy={busy} onClick={() => void confirm()}>Подтвердить сохранение</button></div><p className="review-status" role="status" aria-live="polite">{status}</p>{error ? <p className="capture-error" role="alert" tabIndex={-1}>{error}</p> : null}{plan ? <SavePlan plan={plan} title="План Decision Journal Safe Write (dry-run)" /> : null}{saved ? <SavedNote payload={saved} label="Decision Journal сохранён" addOutcome={() => onAddOutcome(saved.note.id)} /> : null}</form>;
}

interface JournalOutcomeFormProps { initialDecisionId: string; onBusyChange: (busy: boolean) => void; }

function JournalOutcomeForm({ initialDecisionId, onBusyChange }: JournalOutcomeFormProps): ReactElement {
  const [value, setValue] = useState({ title: "", note_type: "project", tags: "", links: "", decision_id: initialDecisionId, timeMode: "unknown" as "exact" | "unknown", evidenceAt: "unknown", domain: "", actual: "", reassessment: "", notes: "" });
  const [plan, setPlan] = useState<SavePlanResponse | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedNoteResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  const update = <K extends keyof typeof value>(key: K, next: (typeof value)[K]): void => { setValue((current) => ({ ...current, [key]: next })); setPlan(null); setToken(null); };
  const payload = (): import("./api").OutcomePayload => ({ title: value.title, note_type: value.note_type, tags: lines(value.tags), links: lines(value.links), decision_id: value.decision_id, evidence_at: value.timeMode === "exact" ? value.evidenceAt : "unknown", evidence_at_precision: value.timeMode, domain: value.domain.trim() ? value.domain : null, actual_result: value.actual, reassessment: value.reassessment, notes: value.notes });
  async function prepare(event: FormEvent<HTMLFormElement>): Promise<void> { event.preventDefault(); if (busy || saved) return; setBusy(true); setError(""); setStatus("Готовлю Outcome dry-run…"); try { const next = await prepareOutcome(payload()); setPlan(next); setToken(next.confirmation_token); setStatus("Проверь полный diff и подтверди сохранение."); } catch (caught) { setError(responseError(caught, "Не удалось подготовить Outcome.")); setStatus(""); } finally { setBusy(false); } }
  async function confirm(): Promise<void> { if (!token || busy) return; setBusy(true); setError(""); setStatus("Сохраняю Outcome в vault…"); try { const result = await applyOutcome(token, payload()); setSaved(result); setToken(null); setStatus("Outcome сохранён отдельной связанной заметкой."); } catch (caught) { setError(responseError(caught, "Не удалось сохранить Outcome.")); setStatus(""); } finally { setBusy(false); } }
  const text = (key: "title" | "tags" | "links" | "decision_id" | "domain" | "actual" | "reassessment" | "notes", label: string, required = false, rows?: number): ReactElement => <label className={`journal-field${rows ? " journal-field-wide" : ""}`}><span className="draft-field-label">{label}</span>{rows ? <textarea className="review-input" rows={rows} required={required} value={value[key]} disabled={busy || Boolean(saved)} onChange={(event) => update(key, event.target.value)} /> : <input className="review-input" type="text" required={required} value={value[key]} disabled={busy || Boolean(saved)} onChange={(event) => update(key, event.target.value)} />}</label>;
  return <form id="outcome-form" className="journal-form" onSubmit={(event) => void prepare(event)}><p className="journal-form-intro">Outcome — отдельная заметка, связанная с current Decision Journal по UUIDv7.</p><div className="journal-fields">{text("title", "Название", true)}<label className="journal-field"><span className="draft-field-label">Тип заметки</span><select className="review-input" value={value.note_type} disabled={busy || Boolean(saved)} onChange={(event) => update("note_type", event.target.value)}>{NOTE_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>{text("tags", "Теги (один на строку)", false, 3)}{text("links", "Ссылки (одна на строку)", false, 3)}{text("decision_id", "Decision UUIDv7", true)}<div className="journal-time-grid"><label className="journal-field"><span className="draft-field-label">Время outcome</span><select className="review-input" value={value.timeMode} disabled={busy || Boolean(saved)} onChange={(event) => { const next = event.target.value as "exact" | "unknown"; update("timeMode", next); update("evidenceAt", next === "exact" ? "" : "unknown"); }}><option value="exact">Точное время</option><option value="unknown">Время неизвестно</option></select></label>{value.timeMode === "exact" ? <label className="journal-field journal-evidence-field"><span className="draft-field-label">RFC3339 время</span><input className="review-input" type="text" required value={value.evidenceAt} disabled={busy || Boolean(saved)} onChange={(event) => update("evidenceAt", event.target.value)} /><button className="review-button review-button-secondary" type="button" disabled={busy || Boolean(saved)} onClick={() => update("evidenceAt", new Date().toISOString())}>Сейчас</button></label> : null}</div>{text("domain", "Домен (необязательно)")}{text("actual", "Actual result · фактический результат", false, 4)}{text("reassessment", "Reassessment · переоценка", false, 4)}{text("notes", "Notes · заметки", false, 4)}<span className="journal-help">Заполни Actual result или Reassessment.</span></div><div className="journal-actions"><button className="review-button review-button-primary" type="submit" disabled={busy || Boolean(saved)} aria-busy={busy}>Подготовить сохранение</button><button className="review-button review-button-primary" type="button" hidden={!token} disabled={busy || !token || Boolean(saved)} aria-busy={busy} onClick={() => void confirm()}>Подтвердить сохранение</button></div><p className="review-status" role="status" aria-live="polite">{status}</p>{error ? <p className="capture-error" role="alert" tabIndex={-1}>{error}</p> : null}{plan ? <SavePlan plan={plan} title="План Outcome Observation Safe Write (dry-run)" /> : null}{saved ? <SavedNote payload={saved} label="Outcome Observation сохранён" /> : null}</form>;
}

export function DecisionJournalSurface(): ReactElement {
  const [mode, setMode] = useState<"decision" | "outcome">("decision");
  const [decisionId, setDecisionId] = useState("");
  const [busy, setBusy] = useState(false);
  return <section className="decision-journal-surface" id="decision-journal" aria-labelledby="decision-journal-title" data-decision-journal-surface aria-busy={busy}><SectionHeading eyebrow="Stage 2 · deliberate capture" title="Decision Journal." id="decision-journal-title">Зафиксируй выбор и поздний результат отдельно — только явным structured вводом.</SectionHeading><div className="journal-panel"><div className="mode-switch" role="group" aria-label="Режим Decision Journal"><button className={`mode-button${mode === "decision" ? " is-active" : ""}`} type="button" disabled={busy} aria-pressed={mode === "decision"} onClick={() => setMode("decision")}>Decision</button><button className={`mode-button${mode === "outcome" ? " is-active" : ""}`} type="button" disabled={busy} aria-pressed={mode === "outcome"} onClick={() => setMode("outcome")}>Outcome</button></div>{mode === "decision" ? <JournalDecisionForm onBusyChange={setBusy} onAddOutcome={(id) => { setDecisionId(id); setMode("outcome"); }} /> : <JournalOutcomeForm onBusyChange={setBusy} initialDecisionId={decisionId} />}</div></section>;
}
