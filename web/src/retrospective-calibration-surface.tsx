import { motion, useReducedMotion } from "motion/react";
import { useRef, useState, type ReactElement } from "react";

import { Icon } from "./icons";
import {
  requestRetrospectiveCalibration,
  Stage7ApiError,
  type RetrospectiveCalibrationCount,
  type RetrospectiveCalibrationRatio,
  type RetrospectiveCalibrationResult,
} from "./stage7-api";
import "./retrospective-calibration-surface.css";

type CalibrationUiState =
  | "idle"
  | "loading"
  | "success"
  | "zero"
  | "source-unavailable"
  | "invalid"
  | "too-large"
  | "cancelled"
  | "error";

type CalibrationError = {
  state: Exclude<CalibrationUiState, "idle" | "loading" | "success" | "zero">;
  message: string;
};

const ERROR_TEXT: Record<string, CalibrationError> = {
  RETROSPECTIVE_CALIBRATION_INVALID_REQUEST: {
    state: "invalid",
    message: "Не удалось проверить запрос ретроспективной калибровки.",
  },
  RETROSPECTIVE_CALIBRATION_CANCELLED: {
    state: "cancelled",
    message: "Ретроспективная калибровка отменена.",
  },
  RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE: {
    state: "source-unavailable",
    message: "Источники ретроспективной калибровки сейчас недоступны.",
  },
  RETROSPECTIVE_CALIBRATION_TOO_LARGE: {
    state: "too-large",
    message: "Объём данных для ретроспективной калибровки превышает допустимый предел.",
  },
  RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE: {
    state: "too-large",
    message: "Результат ретроспективной калибровки слишком велик.",
  },
};

const COUNT_LABELS: Record<string, string> = {
  decision_identity_invalid_or_duplicate: "Неверная или повторяющаяся идентичность",
  decision_time_unknown: "Время решения неизвестно",
  decision_time_not_exact_or_invalid: "Время решения неточное или недопустимо",
  decision_body_created_after_cutoff: "Запись создана после точки отсечения",
  decision_body_edited_after_cutoff: "Запись изменена после точки отсечения",
  decision_note_metadata_invalid: "Метаданные решения недопустимы",
  decision_option_count_unsupported: "Число вариантов не поддерживается",
  decision_option_identity_invalid: "Идентичность вариантов недопустима",
  decision_body_invalid: "Содержимое решения недопустимо",
  prechoice_context_unavailable: "Контекст до выбора недоступен",
  historical_context_unreconstructable: "Исторический контекст нельзя восстановить",
  simulate_me_unavailable: "Повторный прогноз недоступен",
  prechoice_request_invalid: "Запрос до выбора недопустим",
  simulate_me_result_invalid: "Результат повторного прогноза недопустим",
  simulate_me_policy_mismatch: "Политика повторного прогноза не совпала",
  calibration_composition_invalid: "Состав результата не прошёл проверку",
  unknown_evidence_excluded: "Свидетельства с неизвестным временем исключены",
  later_evidence_excluded: "Поздние свидетельства исключены",
  created_after_cutoff_excluded: "Созданное после точки отсечения исключено",
  edited_after_cutoff_excluded: "Изменённое после точки отсечения исключено",
  historical_snapshot_unavailable: "Исторический снимок недоступен",
};

function errorFor(caught: unknown): CalibrationError {
  if (caught instanceof Stage7ApiError) {
    return ERROR_TEXT[caught.code] ?? {
      state: "error",
      message: "Не удалось завершить ретроспективную калибровку.",
    };
  }
  if (typeof caught === "object" && caught !== null && "name" in caught && caught.name === "AbortError") {
    return ERROR_TEXT.RETROSPECTIVE_CALIBRATION_CANCELLED;
  }
  return {
    state: "error",
    message: "Не удалось завершить ретроспективную калибровку.",
  };
}

function countLabel(code: string): string {
  return COUNT_LABELS[code] ?? "Техническая причина скрыта";
}

function ratioText(ratio: RetrospectiveCalibrationRatio | null, emptyLabel: string): string {
  return ratio === null ? emptyLabel : `${ratio.numerator} / ${ratio.denominator}`;
}

function CountSection({
  id,
  title,
  counts,
}: {
  id: string;
  title: string;
  counts: readonly RetrospectiveCalibrationCount[];
}): ReactElement {
  return (
    <section className="calibration-count-section" aria-labelledby={id}>
      <h4 id={id}>{title}</h4>
      <ul className="calibration-count-list">
        {counts.map((item) => (
          <li key={item.code}>
            <span>{countLabel(item.code)}</span>
            <strong>{item.count}</strong>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CalibrationResult({ result }: { result: RetrospectiveCalibrationResult }): ReactElement {
  const reduceMotion = useReducedMotion();
  const { metrics } = result;
  return (
    <motion.div
      className="calibration-result"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: "easeOut" }}
    >
      <div className="calibration-result-heading">
        <div>
          <p className="eyebrow">Агрегированный результат</p>
          <h3>Результат ретроспективной проверки</h3>
        </div>
        <Icon name="growth" size={32} aria-hidden="true" />
      </div>

      <dl className="calibration-metrics" aria-label="Агрегированные метрики калибровки">
        <div><dt>Решений увидено</dt><dd>{metrics.decision_notes_seen}</dd></div>
        <div><dt>Подходящих решений</dt><dd>{metrics.eligible_decisions}</dd></div>
        <div><dt>Построено прогнозов</dt><dd>{metrics.predicted_decisions}</dd></div>
        <div><dt>Отказов от прогноза</dt><dd>{metrics.abstentions}</dd></div>
        <div><dt>Точных совпадений</dt><dd>{metrics.exact_option_match_count}</dd></div>
        <div><dt>Несовпадений</dt><dd>{metrics.mismatch_count}</dd></div>
        <div><dt>Недоступных повторов</dt><dd>{metrics.unavailable_count}</dd></div>
        <div><dt>Некорректных повторов</dt><dd>{metrics.invalid_count}</dd></div>
      </dl>

      <div className="calibration-ratio-grid">
        <article>
          <h4>Покрытие</h4>
          <p className="calibration-ratio">{ratioText(metrics.coverage, "Нет подходящих решений")}</p>
          <p>Числитель / знаменатель из результата ядра.</p>
        </article>
        <article>
          <h4>Точность без отказов</h4>
          <p className="calibration-ratio">
            {ratioText(metrics.accuracy_non_abstained, "Нет построенных прогнозов")}
          </p>
          <p>Числитель / знаменатель из результата ядра.</p>
        </article>
      </div>

      <div className="calibration-count-grid">
        <CountSection id="calibration-excluded" title="Исключённые решения" counts={result.excluded_decisions} />
        <CountSection id="calibration-unavailable" title="Недоступные повторы" counts={result.replay_unavailable} />
        <CountSection id="calibration-invalid" title="Некорректные повторы" counts={result.replay_invalid} />
        <CountSection id="calibration-temporal" title="Временные оговорки" counts={result.temporal_caveats} />
      </div>

      <details className="calibration-technical-details">
        <summary>Технические детали построения</summary>
        <dl>
          <div><dt>Версия построения</dt><dd><code>{result.derivation_version}</code></dd></div>
          <div><dt>Идентификатор политики</dt><dd><code>{result.policy_id}</code></dd></div>
          <div><dt>Отпечаток политики</dt><dd><code>{result.policy_fingerprint}</code></dd></div>
          <div><dt>Режим реконструкции</dt><dd><code>{result.reconstruction_mode}</code></dd></div>
        </dl>
      </details>
    </motion.div>
  );
}

export function RetrospectiveCalibrationSurface(): ReactElement {
  const reduceMotion = useReducedMotion();
  const [state, setState] = useState<CalibrationUiState>("idle");
  const [result, setResult] = useState<RetrospectiveCalibrationResult | null>(null);
  const [error, setError] = useState<CalibrationError | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);

  async function refresh(): Promise<void> {
    controllerRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState("loading");
    setResult(null);
    setError(null);
    try {
      const next = await requestRetrospectiveCalibration(controller.signal);
      if (requestId !== requestIdRef.current) return;
      setResult(next);
      setState(next.metrics.eligible_decisions === 0 ? "zero" : "success");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      const nextError = errorFor(caught);
      setError(nextError);
      setState(nextError.state);
    } finally {
      if (requestId === requestIdRef.current) controllerRef.current = null;
    }
  }

  const cancel = (): void => {
    controllerRef.current?.abort();
  };

  const liveText = state === "loading"
    ? "Проверяем текущее хранилище…"
    : state === "zero"
      ? "Подходящих решений не найдено."
      : result
        ? "Результат ретроспективной проверки обновлён."
        : error?.message ?? "";

  return (
    <section
      id="retrospective-calibration"
      className="stage7-surface signal-plane retrospective-calibration-surface"
      aria-labelledby="retrospective-calibration-title"
      aria-busy={state === "loading"}
      data-calibration-state={state}
    >
      <div className="section-heading stage7-heading">
        <p className="eyebrow">Шаг 7 · ретроспективная проверка</p>
        <h2 id="retrospective-calibration-title">Проверить качество воспроизведения решений.</h2>
        <p>
          Система повторно проигрывает исторические решения, используя только информацию,
          которая доказуемо была доступна до момента выбора, и сравнивает прогноз с фактическим
          выбранным вариантом.
        </p>
      </div>

      <aside className="calibration-boundary" aria-label="Граница ретроспективной проверки">
        <Icon name="info" size={20} aria-hidden="true" />
        <p>
          Это агрегированная проверка текущего хранилища только для чтения. Она не меняет журнал
          решений, не сохраняет прогнозы и не показывает отдельные случаи или их содержимое.
        </p>
      </aside>

      <div className="calibration-actions">
        <button
          className="primary-control"
          type="button"
          disabled={state === "loading"}
          aria-busy={state === "loading"}
          onClick={() => void refresh()}
        >
          <Icon name="growth" size={18} />
          {state === "loading" ? "Проверяем…" : "Проверить ретроспективно"}
        </button>
        {state === "loading" ? (
          <button className="secondary-control" type="button" onClick={cancel}>
            Отменить
          </button>
        ) : null}
      </div>

      <div className="calibration-live" role="status" aria-live="polite">{liveText}</div>

      {error ? (
        <motion.div
          className="calibration-error"
          role="alert"
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
        >
          <Icon name="error" size={18} aria-hidden="true" />
          <span>{error.message}</span>
        </motion.div>
      ) : null}

      {state === "idle" ? (
        <p className="calibration-idle">Нажми кнопку, чтобы построить одно актуальное измерение только для чтения.</p>
      ) : null}
      {state === "zero" ? (
        <p className="calibration-zero">В текущем источнике нет решений, прошедших точные критерии допуска. Это не считается нулевой точностью.</p>
      ) : null}
      {result ? <CalibrationResult result={result} /> : null}
    </section>
  );
}
