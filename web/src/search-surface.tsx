import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";
import { SectionHeading } from "./parity-surfaces";

import { retrieveNote, searchNotes } from "./api";
import { presentValue } from "./presentation";

function displayValue(value: unknown, fallback = "—"): string {
  return presentValue(value, fallback);
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

export function SearchSurface(): ReactElement {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<import("./api").SearchResponse | null>(null);
  const [note, setNote] = useState<import("./api").RetrievedNote | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const noteRef = useRef<HTMLElement>(null);
  const openGenerationRef = useRef(0);

  useEffect(() => {
    if (note) noteRef.current?.focus();
  }, [note]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (busy || !query.trim()) return;
    openGenerationRef.current += 1;
    setOpening(null);
    setBusy(true);
    setError("");
    setNote(null);
    setStatus("Ищу в локальной памяти…");
    try {
      setData(await searchNotes(query));
      setStatus("Поиск завершён.");
    } catch (caught) {
      setError(responseError(caught, "Сервис поиска недоступен."));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function openNote(id: string): Promise<void> {
    if (opening) return;
    const generation = ++openGenerationRef.current;
    setOpening(id);
    setError("");
    try {
      const nextNote = await retrieveNote(id);
      if (generation === openGenerationRef.current) setNote(nextNote);
    } catch (caught) {
      if (generation === openGenerationRef.current) {
        setError(responseError(caught, "Не удалось открыть заметку."));
      }
    } finally {
      if (generation === openGenerationRef.current) setOpening(null);
    }
  }

  return <section className="search-surface" id="search" aria-labelledby="search-title" data-search-surface aria-busy={busy}><SectionHeading eyebrow="Личный поиск" title="Найти в памяти." id="search-title" /><form className="search-form" onSubmit={(event) => void submit(event)}><label className="capture-label" htmlFor="search-input">Поисковый запрос</label><div className="search-form-row"><input className="capture-input" id="search-input" name="query" type="search" placeholder="Например, проверка FastAPI" autoComplete="off" required value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /><button className="capture-submit" type="submit" disabled={busy} aria-busy={busy}>Найти</button></div><p className="search-status" role="status" aria-live="polite">{status}</p></form>{error ? <p className="capture-error search-error" role="alert" tabIndex={-1}>{error}</p> : null}{data && data.hits.length === 0 ? <p className="search-empty">По этому запросу ничего не найдено.</p> : null}<div className="search-results" aria-live="polite">{data?.hits.map((hit) => <article className="search-hit" key={hit.id}><h3 className="search-hit-title">{hit.title}</h3><dl className="search-hit-fields"><Field label="ID" value={hit.id} /><Field label="Тип" value={hit.type} /><Field label="Путь" value={hit.relative_path} /></dl>{hit.snippet ? <p className="search-hit-snippet">{hit.snippet}</p> : null}{hit.tags?.length ? <div className="search-tags">{hit.tags.map((tag) => <span className="search-tag" key={tag}>{tag}</span>)}</div> : null}<button className="review-button review-button-secondary" type="button" disabled={busy || opening !== null} onClick={() => void openNote(hit.id)}>{opening === hit.id ? "Открываю…" : "Открыть"}</button></article>)}</div>{note ? <section ref={noteRef} className="retrieved-note" aria-labelledby="retrieved-note-title" tabIndex={-1}><h3 id="retrieved-note-title">{displayValue(note.title, "Заметка")}</h3><dl className="draft-fields"><Field label="ID" value={note.id} /><Field label="Тип" value={note.type} /><Field label="Путь" value={note.relative_path} /><Field label="Создано" value={note.created} /><Field label="Обновлено" value={note.updated} /></dl>{note.tags?.length ? <div className="search-tags">{note.tags.map((tag) => <span className="search-tag" key={tag}>{tag}</span>)}</div> : null}<h4>Содержание (только чтение)</h4><pre className="retrieved-note-body" tabIndex={0} aria-label="Содержание заметки">{note.content}</pre></section> : null}</section>;
}
