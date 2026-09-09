import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { type FormEvent, type ReactElement, useRef, useState } from "react";

import { Icon } from "./icons";
import {
  type AssistantResult,
  type CompareResult,
  type SimulateMeResult,
  type Stage7Request,
  type Stage7Option,
  Stage7ApiError,
  requestAssistant,
  requestCompare,
} from "./stage7-api";
import "./assistant-compare-surface.css";

type RequestKind = "assistant" | "compare";
type UiResult =
  | { kind: "assistant"; data: AssistantResult; options: Stage7Option[] }
  | { kind: "compare"; data: CompareResult; options: Stage7Option[] };

const INTERNAL_PROVIDER_TOKENS = [
  "explicit_constraints",
  "explicit_context",
  "objectives_used",
  "constraints_used",
  "evidence_refs",
] as const;

type PresentationBranch = "assistant" | "simulate_me";
type CompareRelation =
  | "same_selected_option"
  | "different_selected_options"
  | "assistant_only_selected"
  | "simulate_me_only_selected"
  | "neither_selected"
  | "assistant_error"
  | "simulate_me_error"
  | "both_error";

const COMPARE_RELATION_TEXT: Record<CompareRelation, string> = {
  same_selected_option: "Независимый совет и прогноз вашего выбора совпали.",
  different_selected_options: "Независимый совет и прогноз вашего выбора указывают на разные варианты.",
  assistant_only_selected: "Независимый совет рекомендует вариант, а прогноз вашего выбора не смог определить вариант.",
  simulate_me_only_selected: "Прогноз вашего выбора определил вариант, а независимый совет не выбрал вариант.",
  neither_selected: "Ни независимый совет, ни прогноз вашего выбора не определили вариант.",
  assistant_error: "Не удалось получить независимый совет; прогноз вашего выбора сохранён отдельно.",
  simulate_me_error: "Не удалось получить прогноз вашего выбора; независимый совет сохранён отдельно.",
  both_error: "Не удалось получить ни независимый совет, ни прогноз вашего выбора.",
};

const STAGE7_ERROR_TEXT: Record<string, string> = {
  ASSISTANT_INVALID_REQUEST: "Не удалось проверить введённые данные.",
  ASSISTANT_CANCELLED: "Запрос был отменён.",
  ASSISTANT_TIMEOUT: "Сервис не ответил вовремя.",
  ASSISTANT_PROVIDER_UNAVAILABLE: "Сервис сейчас недоступен.",
  ASSISTANT_PROVIDER_FAILURE: "Не удалось получить независимый совет.",
  ASSISTANT_MALFORMED_RESULT: "Не удалось получить корректный результат.",
  ASSISTANT_RESULT_TOO_LARGE: "Не удалось получить корректный результат.",
  ASSISTANT_RESULT_INVALID: "Не удалось получить корректный результат.",
  COMPARE_BRANCH_INVALID_REQUEST: "Не удалось проверить введённые данные.",
  COMPARE_BRANCH_CANCELLED: "Запрос был отменён.",
  COMPARE_BRANCH_TIMEOUT: "Сервис не ответил вовремя.",
  COMPARE_BRANCH_UNAVAILABLE: "Сервис сейчас недоступен.",
  COMPARE_BRANCH_FAILURE: "Не удалось получить результат.",
  COMPARE_BRANCH_MALFORMED_RESULT: "Не удалось получить корректный результат.",
  COMPARE_BRANCH_RESULT_TOO_LARGE: "Не удалось получить корректный результат.",
  COMPARE_BRANCH_RESULT_INVALID: "Не удалось получить корректный результат.",
  COMPARE_INVALID_REQUEST: "Не удалось проверить введённые данные.",
  COMPARE_CANCELLED: "Запрос был отменён.",
  COMPARE_COMPOSITION_INVALID: "Не удалось получить корректный результат.",
  COMPARE_RESULT_TOO_LARGE: "Не удалось получить корректный результат.",
};

function hasTechnicalProviderText(value: string, options: readonly Stage7Option[]): boolean {
  const trimmed = value.trim();
  return (
    options.some((option) => option.id === trimmed) ||
    /^option-\d+$/iu.test(trimmed) ||
    /(^|[^\p{L}\p{N}_])option-\d+(?=$|[^\p{L}\p{N}_])/iu.test(value) ||
    INTERNAL_PROVIDER_TOKENS.some((token) => value.includes(token))
  );
}

function humanProviderText(value: string, options: readonly Stage7Option[]): string | null {
  return value.trim() && !hasTechnicalProviderText(value, options) ? value : null;
}

function visibleProviderText(
  values: readonly string[],
  options: readonly Stage7Option[],
): { visible: string[]; hiddenCount: number } {
  const visible: string[] = [];
  let hiddenCount = 0;
  for (const value of values) {
    const text = humanProviderText(value, options);
    if (text) visible.push(text);
    else hiddenCount += 1;
  }
  return { visible, hiddenCount };
}

function optionLabel(
  selected: Stage7Option,
  options: readonly Stage7Option[],
): string | null {
  const requestOption = options.find((option) => option.id === selected.id);
  return requestOption ? humanProviderText(requestOption.label, []) : null;
}

function displaySelection(
  selectedId: string | null,
  options: readonly Stage7Option[],
): { text: string; hidden: boolean } {
  if (selectedId === null) return { text: "вариант не выбран", hidden: false };
  const selected = options.find((option) => option.id === selectedId);
  if (!selected) return { text: "неизвестный вариант", hidden: true };
  const label = humanProviderText(selected.label, []);
  return label ? { text: label, hidden: false } : { text: "вариант скрыт", hidden: true };
}

function qualityWarning(hiddenCount: number, subject: string): ReactElement | null {
  return hiddenCount > 0
    ? <p className="stage7-quality-warning">{subject}: технический текст скрыт, чтобы не показывать внутренние обозначения.</p>
    : null;
}

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function assistantSummary(result: AssistantResult, options: readonly Stage7Option[]): ReactElement {
  if (result.kind === "abstention") {
    return <p>Нет рекомендации: недостаточно оснований для независимого совета.</p>;
  }
  const recommendation = result.recommendation
    ? humanProviderText(result.recommendation, options)
    : null;
  const rationale = visibleProviderText(result.rationale, options);
  const uncertainty = visibleProviderText(result.uncertainty, options);
  const selectedLabel = result.selected_option ? optionLabel(result.selected_option, options) : null;
  const hiddenCount = (result.recommendation && !recommendation ? 1 : 0) + rationale.hiddenCount + uncertainty.hiddenCount;
  return (
    <>
      {result.selected_option && selectedLabel ? <p className="stage7-choice">Выбранный вариант: {selectedLabel}</p> : null}
      {recommendation ? <p>{recommendation}</p> : null}
      {rationale.visible.length > 0 ? <ul>{rationale.visible.map((item) => <li key={item}>{item}</li>)}</ul> : null}
      {uncertainty.visible.length > 0 ? <p className="stage7-muted">Неопределённость: {uncertainty.visible.join("; ")}</p> : null}
      {result.selected_option && !selectedLabel ? qualityWarning(1, "Выбор") : null}
      {qualityWarning(hiddenCount, "Пояснения совета")}
    </>
  );
}

function branchState(branch: PresentationBranch, state: string): string {
  if (state === "result") return "Результат";
  if (state === "abstention") {
    return branch === "assistant" ? "Нет рекомендации" : "Недостаточно данных";
  }
  if (state === "error") return "Ошибка";
  return "Недоступно";
}

function simulateSummary(result: SimulateMeResult, options: readonly Stage7Option[]): ReactElement {
  if (result.kind === "abstention") {
    return <p>Недостаточно данных, чтобы определить ваш вероятный выбор.</p>;
  }
  if (!result.selected_option) {
    return <p>Вероятный вариант не определён.</p>;
  }
  const label = optionLabel(result.selected_option, options);
  return label
    ? <p className="stage7-choice">Вероятный вариант: {label}</p>
    : <p className="stage7-quality-warning">Выбор прогноза скрыт: техническая подпись недоступна.</p>;
}

function relationExplanation(relation: string): string {
  return COMPARE_RELATION_TEXT[relation as CompareRelation] ?? "Не удалось определить, как соотносятся результаты.";
}

function branchErrorText(error: { code: string; message: string }): string {
  return STAGE7_ERROR_TEXT[error.code] ?? "Не удалось получить результат.";
}

function requestErrorText(caught: unknown): string {
  if (caught instanceof Stage7ApiError) {
    return STAGE7_ERROR_TEXT[caught.code] ?? "Не удалось получить результат.";
  }
  return "Не удалось получить результат.";
}

function ComparePanels({ result, options }: { result: CompareResult; options: readonly Stage7Option[] }): ReactElement {
  const assistantSelection = displaySelection(result.delta.assistant_selected_option_id, options);
  const simulateSelection = displaySelection(result.delta.simulate_me_selected_option_id, options);
  return (
    <div className="stage7-result-grid" aria-label="Результат сравнения">
      <article className="stage7-result-panel">
        <div className="stage7-result-title"><Icon name="relation" size={18} /><h3>Независимый совет</h3></div>
        <p className="stage7-status">{branchState("assistant", result.assistant.state)}</p>
        {result.assistant.result
          ? assistantSummary(result.assistant.result, options)
          : result.assistant.error
            ? <p className="stage7-branch-error" role="alert">{branchErrorText(result.assistant.error)}</p>
            : <p>Ветка независимого совета не вернула результата.</p>}
      </article>
      <article className="stage7-result-panel">
        <div className="stage7-result-title"><Icon name="simulate" size={18} /><h3>Прогноз вашего выбора</h3></div>
        <p className="stage7-status">{branchState("simulate_me", result.simulate_me.state)}</p>
        {result.simulate_me.result
          ? simulateSummary(result.simulate_me.result, options)
          : result.simulate_me.error
            ? <p className="stage7-branch-error" role="alert">{branchErrorText(result.simulate_me.error)}</p>
            : <p>Прогноз вашего выбора не вернул результата.</p>}
      </article>
      <article className="stage7-result-panel stage7-delta-panel">
        <div className="stage7-result-title"><Icon name="growth" size={18} /><h3>Структурное сравнение</h3></div>
        <p>{relationExplanation(result.delta.relation)}</p>
        <dl className="stage7-delta-list">
          <div><dt>Независимый совет</dt><dd>{assistantSelection.text}</dd></div>
          <div><dt>Прогноз вашего выбора</dt><dd>{simulateSelection.text}</dd></div>
        </dl>
        {assistantSelection.hidden || simulateSelection.hidden
          ? <p className="stage7-quality-warning">Технические идентификаторы структурного результата скрыты; показаны доступные названия вариантов.</p>
          : null}
      </article>
    </div>
  );
}

export function AssistantCompareSurface(): ReactElement {
  const reduceMotion = useReducedMotion();
  const [task, setTask] = useState("");
  const [optionLabels, setOptionLabels] = useState(["", ""]);
  const [constraints, setConstraints] = useState("");
  const [goals, setGoals] = useState("");
  const [facts, setFacts] = useState("");
  const [background, setBackground] = useState("");
  const [busy, setBusy] = useState<RequestKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UiResult | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);

  function buildPayload(): Stage7Request {
    return {
      task: task.trim(),
      options: optionLabels.map((label, index) => ({ id: `option-${index + 1}`, label: label.trim() })),
      explicit_constraints: lines(constraints),
      explicit_goals: lines(goals),
      explicit_context: [
        ...lines(facts).map((text) => ({ kind: "fact" as const, text })),
        ...lines(background).map((text) => ({ kind: "background" as const, text })),
      ],
    };
  }

  async function submit(kind: RequestKind): Promise<void> {
    const payload = buildPayload();
    if (!payload.task || payload.options.some((option) => !option.label)) {
      setError("Заполните задачу и все варианты решения.");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const version = ++requestVersion.current;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "assistant") {
        const data = await requestAssistant(payload, controller.signal);
        if (version === requestVersion.current) setResult({ kind, data, options: payload.options });
      } else {
        const data = await requestCompare(payload, controller.signal);
        if (version === requestVersion.current) setResult({ kind, data, options: payload.options });
      }
    } catch (caught) {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      setError(requestErrorText(caught));
    } finally {
      if (version === requestVersion.current) setBusy(null);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void submit("compare");
  }

  function updateOption(index: number, value: string): void {
    setOptionLabels((current) => current.map((item, itemIndex) => itemIndex === index ? value : item));
  }

  return (
    <motion.section
      id="assistant-compare"
      className="stage7-surface signal-plane"
      aria-labelledby="assistant-compare-title"
      aria-busy={busy !== null}
      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
      animate={reduceMotion ? undefined : { opacity: 1, y: 0 }}
    >
      <div className="section-heading stage7-heading">
        <Icon name="relation" size={64} className="section-art" />
        <p className="eyebrow">Две независимые перспективы</p>
        <h2 id="assistant-compare-title">Независимый совет и прогноз вашего выбора — рядом, но не смешиваются.</h2>
        <p>Независимый совет оценивает задачу только по данным, которые вы ввели. Прогноз вашего выбора пытается определить, какой вариант вы, вероятно, выбрали бы сами, используя доступные данные о ваших предпочтениях, целях и контексте. Сравнение показывает, совпали ли эти два результата.</p>
      </div>
      <form className="stage7-form" onSubmit={onSubmit}>
        <label className="stage7-field stage7-field-wide">
          <span>Задача</span>
          <textarea className="foundation-input" value={task} onChange={(event) => setTask(event.target.value)} rows={3} placeholder="Какое решение нужно разобрать?" required />
        </label>
        <fieldset className="stage7-options">
          <legend>Варианты</legend>
          {optionLabels.map((label, index) => (
            <div className="stage7-option-row" key={`option-${index + 1}`}>
              <label>
                <span className="stage7-sr-only">Вариант {index + 1}</span>
                <input className="foundation-input" value={label} onChange={(event) => updateOption(index, event.target.value)} placeholder={`Вариант ${index + 1}`} required />
              </label>
              {optionLabels.length > 2 ? (
                <button className="secondary-control stage7-icon-button" type="button" aria-label={`Удалить вариант ${index + 1}`} onClick={() => setOptionLabels((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Icon name="close" size={18} /></button>
              ) : null}
            </div>
          ))}
          {optionLabels.length < 8 ? <button className="secondary-control" type="button" onClick={() => setOptionLabels((current) => [...current, ""])}><Icon name="add" size={18} />Добавить вариант</button> : null}
        </fieldset>
        <div className="stage7-input-grid">
          <label className="stage7-field"><span>Явные ограничения <small>по одному в строке</small></span><textarea className="foundation-input" rows={3} value={constraints} onChange={(event) => setConstraints(event.target.value)} /></label>
          <label className="stage7-field"><span>Явные цели <small>по одной в строке</small></span><textarea className="foundation-input" rows={3} value={goals} onChange={(event) => setGoals(event.target.value)} /></label>
          <label className="stage7-field"><span>Факты, введённые вручную <small>по одному в строке</small></span><textarea className="foundation-input" rows={3} value={facts} onChange={(event) => setFacts(event.target.value)} /></label>
          <label className="stage7-field"><span>Фоновый контекст <small>по одному в строке</small></span><textarea className="foundation-input" rows={3} value={background} onChange={(event) => setBackground(event.target.value)} /></label>
        </div>
        <aside className="stage7-disclosure" aria-label="Граница приватности">
          <Icon name="info" size={18} />
          <p>В сервис независимого совета отправляются только задача, варианты и поля, которые вы вручную заполнили выше. Данные памяти, поиска, модели себя, журнала решений и прогноза автоматически не добавляются.</p>
        </aside>
        <div className="stage7-actions">
          <button className="secondary-control" type="button" disabled={busy !== null} onClick={() => void submit("assistant")}><Icon name="relation" size={18} />{busy === "assistant" ? "Получаем совет..." : "Только независимый совет"}</button>
          <button className="primary-control" type="submit" disabled={busy !== null}><Icon name="simulate" size={18} />{busy === "compare" ? "Сравниваем..." : "Независимый совет + прогноз + сравнение"}</button>
        </div>
      </form>
      <div className="stage7-live" role="status" aria-live="polite">{busy ? "Запрос выполняется." : error ?? (result ? "Результат обновлён." : "")}</div>
      <AnimatePresence mode="wait">
        {error ? <motion.div className="stage7-error" role="alert" key="error" initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><Icon name="error" size={18} /><span>{error}</span></motion.div> : null}
        {!error && result?.kind === "assistant" ? <motion.article className="stage7-result-panel stage7-single-result" key="assistant" initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}><div className="stage7-result-title"><Icon name="relation" size={18} /><h3>Независимый совет</h3></div>{assistantSummary(result.data, result.options)}</motion.article> : null}
        {!error && result?.kind === "compare" ? <motion.div key="compare" initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}><ComparePanels result={result.data} options={result.options} /></motion.div> : null}
      </AnimatePresence>
    </motion.section>
  );
}
