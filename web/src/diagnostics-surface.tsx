import {
  ApiRequestError,
  loadDiagnostics,
  type DiagnosticsLayer,
  type DiagnosticsResponse,
  type DiagnosticsStatus,
} from "./api";
import { Icon } from "./icons";
import { SectionHeading } from "./parity-surfaces";
import { useEffect, useRef, useState, type ReactElement } from "react";

const STATUS_COPY: Readonly<Record<DiagnosticsStatus, {
  readonly label: string;
  readonly description: string;
  readonly icon: "success" | "warning" | "error";
}>> = {
  healthy: {
    label: "Стабильно",
    description: "Обязательные локальные слои отвечают без диагностических сигналов.",
    icon: "success",
  },
  degraded: {
    label: "Требует внимания",
    description: "Отчёт сформирован, но один или несколько сигналов требуют проверки.",
    icon: "warning",
  },
  unavailable: {
    label: "Недоступна",
    description: "Отчёт не может подтвердить состояние обязательных локальных слоёв.",
    icon: "error",
  },
};

const LAYER_LABELS: Readonly<Record<"timeline" | "self_model" | "self_retrieval", string>> = {
  timeline: "Хронология",
  self_model: "Модель себя",
  self_retrieval: "Сбор контекста",
};

function statusCopy(status: DiagnosticsStatus): typeof STATUS_COPY[DiagnosticsStatus] {
  return STATUS_COPY[status];
}

function formatCount(value: number | null): string {
  return value === null || !Number.isSafeInteger(value) || value < 0
    ? "Недоступно"
    : new Intl.NumberFormat("ru-RU").format(value);
}

function formatBytes(value: number | null): string {
  const count = formatCount(value);
  return count === "Недоступно" ? count : `${count} Б`;
}

function formatGeneratedAt(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "время не подтверждено";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function availability(value: boolean): string {
  return value ? "Доступна" : "Недоступна";
}

function StatusBadge({ status }: { status: DiagnosticsStatus }): ReactElement {
  const copy = statusCopy(status);
  return (
    <span className={`diagnostics-status-badge diagnostics-status-${status}`} data-status={status}>
      <Icon name={copy.icon} size={18} />
      <span>{copy.label}</span>
    </span>
  );
}

function LayerRow({ label, layer }: { label: string; layer: DiagnosticsLayer }): ReactElement {
  const copy = statusCopy(layer.status);
  return (
    <div className="diagnostics-layer-row">
      <div className="diagnostics-layer-name">
        <span>{label}</span>
        <span className="diagnostics-layer-requirement">
          {layer.required ? "Обязательный слой" : "Необязательный слой"}
        </span>
      </div>
      <StatusBadge status={layer.status} />
      {layer.code ? (
        <code className="diagnostics-code" title={`Код: ${layer.code}`}>
          {layer.code}
        </code>
      ) : (
        <span className="diagnostics-code diagnostics-code-empty">Без сигнала</span>
      )}
      <span className="sr-only">{copy.description}</span>
    </div>
  );
}

function DiagnosticsReport({ report }: { report: DiagnosticsResponse }): ReactElement {
  const copy = statusCopy(report.status);
  return (
    <div className="diagnostics-report" data-diagnostics-report>
      <div className="diagnostics-overview" data-diagnostics-status={report.status}>
        <div className="diagnostics-overview-icon" aria-hidden="true">
          <Icon name={copy.icon} size={24} />
        </div>
        <div className="diagnostics-overview-copy">
          <p className="diagnostics-kicker">Сводка локального отчёта</p>
          <h3>{copy.label}</h3>
          <p>{copy.description}</p>
        </div>
        <StatusBadge status={report.status} />
      </div>

      <p className="diagnostics-generated">
        Последний сформированный отчёт: <time dateTime={report.generated_at}>{formatGeneratedAt(report.generated_at)}</time>
      </p>

      <section className="diagnostics-group" aria-labelledby="diagnostics-availability-title">
        <div className="diagnostics-group-heading">
          <div>
            <p className="eyebrow">Опорные слои</p>
            <h3 id="diagnostics-availability-title">Доступность рабочего пространства</h3>
          </div>
        </div>
        <dl className="diagnostics-facts">
          <div><dt>Конфигурация</dt><dd>{availability(report.config.resolvable)}</dd></div>
          <div><dt>Manifest</dt><dd>{availability(report.manifest.available)}</dd></div>
          <div><dt>Корни контента</dt><dd>{availability(report.vault.content_roots_available)}</dd></div>
          <div><dt>Сканирование вложений</dt><dd>{report.attachments.scan_complete ? "Завершено" : "Не завершено"}</dd></div>
          <div><dt>Версия manifest</dt><dd>{report.manifest.schema_version === null ? "Недоступно" : formatCount(report.manifest.schema_version)}</dd></div>
        </dl>
      </section>

      <section className="diagnostics-group" aria-labelledby="diagnostics-counts-title">
        <div className="diagnostics-group-heading">
          <div>
            <p className="eyebrow">Безопасные счётчики</p>
            <h3 id="diagnostics-counts-title">Что подтверждено отчётом</h3>
          </div>
        </div>
        <dl className="diagnostics-facts diagnostics-facts-counts">
          <div><dt>Управляемые заметки</dt><dd>{formatCount(report.counts.managed_notes)}</dd></div>
          <div><dt>Личная память</dt><dd>{formatCount(report.counts.enrolled_personal_memory)}</dd></div>
          <div><dt>Журналы решений</dt><dd>{formatCount(report.counts.valid_decision_journals)}</dd></div>
          <div><dt>Наблюдения результатов</dt><dd>{formatCount(report.counts.valid_outcome_observations)}</dd></div>
          <div><dt>Вложения</dt><dd>{formatCount(report.attachments.count)}</dd></div>
          <div><dt>Объём вложений</dt><dd>{formatBytes(report.attachments.total_bytes)}</dd></div>
        </dl>
      </section>

      <section className="diagnostics-group" aria-labelledby="diagnostics-layers-title">
        <div className="diagnostics-group-heading">
          <div>
            <p className="eyebrow">Производные слои</p>
            <h3 id="diagnostics-layers-title">Состояние рабочих маршрутов</h3>
          </div>
        </div>
        <div className="diagnostics-layers">
          {(Object.entries(LAYER_LABELS) as [keyof typeof LAYER_LABELS, string][]).map(([key, label]) => (
            <LayerRow key={key} label={label} layer={report[key]} />
          ))}
        </div>
      </section>

      <section className="diagnostics-group" aria-labelledby="diagnostics-signals-title">
        <div className="diagnostics-group-heading">
          <div>
            <p className="eyebrow">Сигналы проверки</p>
            <h3 id="diagnostics-signals-title">Сгруппированные коды</h3>
          </div>
          <p className="diagnostics-signal-total">Ошибки: {formatCount(report.errors)} · предупреждения: {formatCount(report.warnings)}</p>
        </div>
        {report.diagnostics.length === 0 ? (
          <p className="diagnostics-empty">Сигналы не обнаружены.</p>
        ) : (
          <ul className="diagnostics-signal-list">
            {report.diagnostics.map((item) => (
              <li className="diagnostics-signal-row" key={`${item.severity}-${item.code}`}>
                <span className={`diagnostics-severity diagnostics-severity-${item.severity}`}>
                  {item.severity === "error" ? "Ошибка" : "Предупреждение"}
                </span>
                <code className="diagnostics-code" title={`Код: ${item.code}`}>{item.code}</code>
                <span className="diagnostics-signal-count">{formatCount(item.count)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export function DiagnosticsSurface(): ReactElement {
  const [report, setReport] = useState<DiagnosticsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const errorRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function refresh(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError("");
    setStatus("Формирую текущий отчёт…");
    try {
      setReport(await loadDiagnostics());
      setStatus("Отчёт обновлён.");
    } catch (caught) {
      setStatus("");
      setError(caught instanceof ApiRequestError ? caught.message : "Не удалось получить диагностику рабочего пространства.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="diagnostics-surface" id="diagnostics" aria-labelledby="diagnostics-title" data-diagnostics-surface aria-busy={busy}>
      <SectionHeading eyebrow="Локальная проверка" title="Проверить состояние." id="diagnostics-title">
        Короткий отчёт о доступности текущих локальных слоёв — без содержимого заметок и без действий исправления.
      </SectionHeading>
      <div className="diagnostics-panel">
        <div className="diagnostics-controls">
          <div>
            <p className="diagnostics-control-label">Ручной запрос</p>
            <p className="diagnostics-control-help">Отчёт формируется только после явного обновления.</p>
          </div>
          <button className="review-button review-button-secondary diagnostics-refresh" type="button" disabled={busy} aria-busy={busy} onClick={() => void refresh()}>
            <Icon name="refresh" size={18} />
            {busy ? "Обновляю…" : "Обновить"}
          </button>
        </div>
        <p className="diagnostics-status" role="status" aria-live="polite">{status}</p>
        {error ? <p className="capture-error diagnostics-error" role="alert" tabIndex={-1} ref={errorRef}>{error}</p> : null}
        {!report && !busy && !error ? <p className="diagnostics-empty">Нажми «Обновить», чтобы вручную сформировать первый отчёт.</p> : null}
        {busy && !report ? <p className="diagnostics-loading" role="status" aria-live="polite">Проверяю локальные слои…</p> : null}
        {report ? <DiagnosticsReport report={report} /> : null}
      </div>
    </section>
  );
}
