import { useEffect, useState, type FormEvent, type ReactElement } from "react";

import {
  ApiRequestError,
  executeActionGatewayAction,
  loadActionGatewayHistory,
  loadActionGatewayStatus,
  prepareActionGatewayAction,
  prepareActionGatewayCompensation,
  reconcileActionGatewayAction,
  type ActionGatewayActionKind,
  type ActionGatewayHistoryResponse,
  type ActionGatewayIntent,
  type ActionGatewayPreparedResponse,
  type ActionGatewayReceipt,
  type ActionGatewayState,
  type ActionGatewayStatusResponse,
} from "./api";

type BusyState = "status" | "prepare" | "execute" | "reconcile" | "compensation" | "history" | null;
type PlanMode = "action" | "compensation";

type ActionForm = {
  readonly actionKind: ActionGatewayActionKind;
  readonly repository: string;
  readonly issueNumber: string;
  readonly title: string;
  readonly body: string;
  readonly comment: string;
  readonly desiredState: ActionGatewayState;
};

const INITIAL_FORM: ActionForm = {
  actionKind: "github.issue.create",
  repository: "",
  issueNumber: "",
  title: "",
  body: "",
  comment: "",
  desiredState: "open",
};

const ACTION_LABELS: Readonly<Record<ActionGatewayActionKind, string>> = {
  "github.issue.create": "Создать задачу GitHub",
  "github.issue.comment": "Добавить комментарий",
  "github.issue.set_state": "Изменить состояние задачи",
};

const STATE_LABELS: Readonly<Record<ActionGatewayState, string>> = {
  open: "открыта",
  closed: "закрыта",
};

const RECEIPT_STATE_LABELS: Readonly<Record<ActionGatewayReceipt["state"], string>> = {
  already_satisfied: "Уже выполнено",
  execution_started: "Выполнение начато",
  executed: "Выполнено",
  failed_before_send: "Не отправлено",
  failed_confirmed_no_mutation: "Отклонено без изменения",
  outcome_uncertain: "Результат не подтверждён",
  reconciled_executed: "Подтверждено проверкой",
  reconciled_not_executed: "Проверка не нашла изменения",
  reconciliation_ambiguous: "Проверка неоднозначна",
  compensation_prepared: "Компенсация подготовлена",
};

const ERROR_LABELS: Readonly<Record<string, string>> = {
  provider_rejected: "GitHub отклонил действие без изменения.",
  provider_outcome_uncertain: "Результат не подтверждён. Проверь его отдельно.",
  execution_started_without_terminal_receipt: "Результат не подтверждён. Проверь его отдельно.",
  reconciliation_ambiguous: "Результат нельзя безопасно определить.",
};

function newOperationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `action-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError && /[А-Яа-яЁё]/u.test(error.message)) {
    return error.message;
  }
  return fallback;
}

function statusLabel(status: ActionGatewayStatusResponse["status"]): string {
  if (status === "ready") return "Готова";
  if (status === "credential_unavailable") return "Учетные данные недоступны";
  return "Не настроена";
}

function statusDescription(status: ActionGatewayStatusResponse): string {
  if (status.status === "ready") {
    return status.repositories.length > 0
      ? `Разрешённые цели: ${status.repositories.join(", ")}.`
      : "Разрешённые цели пока не указаны.";
  }
  if (status.status === "credential_unavailable") {
    return "На сервере не завершена отдельная настройка доступа к GitHub. Вход через GitHub для этого не используется.";
  }
  return "Серверный коннектор выключен. Подготовка внешних действий сейчас недоступна.";
}

function targetLabel(receipt: ActionGatewayReceipt): string {
  const repository = receipt.target_safe_identity.repository;
  const issueNumber = receipt.target_safe_identity.issue_number;
  if (typeof repository !== "string") return "Точная цель не указана";
  return typeof issueNumber === "number" ? `${repository} · задача #${issueNumber}` : repository;
}

function formatTime(value: string | null): string {
  if (!value) return "время не указано";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime())
    ? "время не указано"
    : timestamp.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function buildIntent(form: ActionForm): ActionGatewayIntent | null {
  const base = {
    contract_version: "action-intent-v1" as const,
    operation_id: newOperationId(),
    action_kind: form.actionKind,
    connector: "github_issues" as const,
    repository: form.repository,
  };
  if (form.actionKind === "github.issue.create") {
    return { ...base, title: form.title, body: form.body };
  }
  const issueNumber = Number.parseInt(form.issueNumber, 10);
  if (!Number.isSafeInteger(issueNumber) || issueNumber < 1) return null;
  if (form.actionKind === "github.issue.comment") {
    return { ...base, issue_number: issueNumber, comment: form.comment };
  }
  return { ...base, issue_number: issueNumber, desired_state: form.desiredState };
}

function canCompensate(receipt: ActionGatewayReceipt): boolean {
  return (
    receipt.receipt_kind !== "compensation" &&
    (receipt.state === "executed" || receipt.state === "reconciled_executed") &&
    receipt.action_kind !== "github.issue.comment"
  );
}

function planTarget(plan: ActionGatewayPreparedResponse): string {
  const target = plan.prepared.exact_target_identity;
  const repository = target.repository;
  const issueNumber = target.issue_number;
  if (typeof repository !== "string") return "Точная цель будет показана после проверки.";
  return typeof issueNumber === "number" ? `${repository} · задача #${issueNumber}` : repository;
}

function historyItem(item: ActionGatewayReceipt): ReactElement {
  return (
    <li className="action-gateway-history-item" key={item.receipt_id}>
      <div>
        <strong>{ACTION_LABELS[item.action_kind]}</strong>
        <span>{targetLabel(item)}</span>
      </div>
      <div className="action-gateway-history-meta">
        <span data-receipt-state={item.state}>{RECEIPT_STATE_LABELS[item.state]}</span>
        <time dateTime={item.finished_at ?? undefined}>{formatTime(item.finished_at)}</time>
      </div>
    </li>
  );
}

export function ActionGatewaySurface(): ReactElement {
  const [status, setStatus] = useState<ActionGatewayStatusResponse | null>(null);
  const [form, setForm] = useState<ActionForm>(INITIAL_FORM);
  const [plan, setPlan] = useState<ActionGatewayPreparedResponse | null>(null);
  const [planMode, setPlanMode] = useState<PlanMode>("action");
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null);
  const [parentReceiptId, setParentReceiptId] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ActionGatewayReceipt | null>(null);
  const [history, setHistory] = useState<ActionGatewayHistoryResponse | null>(null);
  const [busy, setBusy] = useState<BusyState>("status");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void loadActionGatewayStatus(undefined, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setStatus(response);
        setForm((current) => ({
          ...current,
          repository: current.repository || response.repositories[0] || "",
        }));
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          setError(safeError(requestError, "Не удалось проверить доступность действий."));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(null);
      });
    return () => controller.abort();
  }, []);

  const refreshStatus = async (): Promise<void> => {
    setBusy("status");
    setError(null);
    try {
      const response = await loadActionGatewayStatus();
      setStatus(response);
      setForm((current) => ({
        ...current,
        repository: current.repository || response.repositories[0] || "",
      }));
    } catch (requestError) {
      setError(safeError(requestError, "Не удалось проверить доступность действий."));
    } finally {
      setBusy(null);
    }
  };

  const handlePrepare = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const intent = buildIntent(form);
    if (!intent) {
      setError("Укажи положительный номер задачи GitHub.");
      return;
    }
    setBusy("prepare");
    setError(null);
    setNotice(null);
    setReceipt(null);
    try {
      const response = await prepareActionGatewayAction(intent);
      setPlan(response);
      setPlanMode("action");
      setConfirmationToken(response.confirmation_token);
      setParentReceiptId(null);
      setNotice("Предпросмотр готов. Внешнее изменение ещё не выполнялось.");
    } catch (requestError) {
      setError(safeError(requestError, "Не удалось подготовить действие."));
    } finally {
      setBusy(null);
    }
  };

  const handleExecute = async (): Promise<void> => {
    if (!plan || !confirmationToken) return;
    setBusy("execute");
    setError(null);
    setNotice(null);
    try {
      const response = await executeActionGatewayAction(
        plan.prepared,
        confirmationToken,
        planMode === "compensation" ? parentReceiptId : null,
      );
      setReceipt(response.receipt);
      setConfirmationToken(null);
      setNotice(response.replayed ? "Показан ранее сохранённый результат этой операции." : "Получен результат действия.");
    } catch (requestError) {
      setConfirmationToken(null);
      setError(safeError(requestError, "Не удалось выполнить действие."));
    } finally {
      setBusy(null);
    }
  };

  const handleReconcile = async (): Promise<void> => {
    if (!plan) return;
    setBusy("reconcile");
    setError(null);
    setNotice(null);
    try {
      const response = await reconcileActionGatewayAction(plan.prepared);
      setReceipt(response.receipt);
      setNotice("Проверка завершена без повторной отправки действия.");
    } catch (requestError) {
      setError(safeError(requestError, "Не удалось проверить результат действия."));
    } finally {
      setBusy(null);
    }
  };

  const handleCompensation = async (): Promise<void> => {
    if (!receipt || !canCompensate(receipt)) return;
    setBusy("compensation");
    setError(null);
    setNotice(null);
    try {
      const response = await prepareActionGatewayCompensation(receipt.receipt_id, newOperationId());
      setPlan(response);
      setPlanMode("compensation");
      setConfirmationToken(response.confirmation_token);
      setParentReceiptId(response.parent_receipt_id ?? receipt.receipt_id);
      setNotice("Компенсация подготовлена как отдельное действие. Проверь её перед подтверждением.");
    } catch (requestError) {
      setError(safeError(requestError, "Не удалось подготовить компенсацию."));
    } finally {
      setBusy(null);
    }
  };

  const handleHistory = async (): Promise<void> => {
    setBusy("history");
    setError(null);
    try {
      setHistory(await loadActionGatewayHistory());
    } catch (requestError) {
      setError(safeError(requestError, "Не удалось загрузить историю действий."));
    } finally {
      setBusy(null);
    }
  };

  const clearPlan = (): void => {
    setPlan(null);
    setConfirmationToken(null);
    setParentReceiptId(null);
    setPlanMode("action");
    setReceipt(null);
    setNotice(null);
  };

  const actionReady = status?.ready === true && Boolean(form.repository);
  const isIssueAction = form.actionKind !== "github.issue.create";
  const isComment = form.actionKind === "github.issue.comment";
  const busyNow = busy !== null;

  return (
    <section className="action-gateway-surface" id="action-gateway" aria-labelledby="action-gateway-title">
      <div className="action-gateway-heading">
        <div>
          <p className="action-gateway-kicker">Внешние интеграции · только по запросу</p>
          <h2 id="action-gateway-title">Действия</h2>
          <p className="action-gateway-lead">
            Подготовь одно точное изменение в разрешённом GitHub-репозитории, проверь предпросмотр и подтверди его отдельно.
          </p>
        </div>
        <div className="action-gateway-status-block" data-status={status?.status ?? "loading"}>
          <span className="action-gateway-status-label">Задачи GitHub</span>
          <strong>{status ? statusLabel(status.status) : "Проверяем…"}</strong>
          <span>{status ? statusDescription(status) : "Проверяем серверную готовность коннектора."}</span>
          <button type="button" className="action-gateway-secondary" onClick={() => void refreshStatus()} disabled={busyNow}>
            Обновить статус
          </button>
        </div>
      </div>

      <div className="action-gateway-principles" aria-label="Правила внешних действий">
        <span>Точная цель перед отправкой</span>
        <span>Явное подтверждение владельца</span>
        <span>Без фонового запуска и повтора</span>
      </div>

      <div className="action-gateway-workspace">
        <form className="action-gateway-form" onSubmit={(event) => void handlePrepare(event)}>
          <div className="action-gateway-form-heading">
            <div>
              <span className="action-gateway-eyebrow">Шаг 1 · намерение</span>
              <h3>Что нужно сделать?</h3>
            </div>
            <span className="action-gateway-risk">Контролируемое изменение</span>
          </div>

          <label className="action-gateway-field action-gateway-field-wide">
            <span>Тип действия</span>
            <select
              value={form.actionKind}
              onChange={(event) => setForm((current) => ({ ...current, actionKind: event.target.value as ActionGatewayActionKind }))}
              disabled={busyNow}
            >
              <option value="github.issue.create">Создать задачу GitHub</option>
              <option value="github.issue.comment">Добавить комментарий</option>
              <option value="github.issue.set_state">Изменить состояние задачи</option>
            </select>
          </label>

          <label className="action-gateway-field action-gateway-field-wide">
            <span>Разрешённый репозиторий</span>
            <select
              value={form.repository}
              onChange={(event) => setForm((current) => ({ ...current, repository: event.target.value }))}
              disabled={busyNow || !status?.repositories.length}
              required
            >
              <option value="">Выбери точную цель</option>
              {status?.repositories.map((repository) => <option key={repository} value={repository}>{repository}</option>)}
            </select>
          </label>

          {isIssueAction && (
            <label className="action-gateway-field">
              <span>Номер задачи</span>
              <input
                inputMode="numeric"
                type="number"
                min="1"
                max="2147483647"
                value={form.issueNumber}
                onChange={(event) => setForm((current) => ({ ...current, issueNumber: event.target.value }))}
                disabled={busyNow}
                required
              />
            </label>
          )}

          {form.actionKind === "github.issue.create" && (
            <>
              <label className="action-gateway-field">
                <span>Заголовок</span>
                <input
                  type="text"
                  maxLength={256}
                  value={form.title}
                  onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                  disabled={busyNow}
                  required
                />
              </label>
              <label className="action-gateway-field action-gateway-field-wide">
                <span>Текст задачи</span>
                <textarea
                  rows={5}
                  maxLength={65536}
                  value={form.body}
                  onChange={(event) => setForm((current) => ({ ...current, body: event.target.value }))}
                  disabled={busyNow}
                  required
                />
              </label>
            </>
          )}

          {isComment && (
            <label className="action-gateway-field action-gateway-field-wide">
              <span>Комментарий</span>
              <textarea
                rows={5}
                maxLength={65536}
                value={form.comment}
                onChange={(event) => setForm((current) => ({ ...current, comment: event.target.value }))}
                disabled={busyNow}
                required
              />
            </label>
          )}

          {form.actionKind === "github.issue.set_state" && (
            <label className="action-gateway-field">
              <span>Новое состояние</span>
              <select
                value={form.desiredState}
                onChange={(event) => setForm((current) => ({ ...current, desiredState: event.target.value as ActionGatewayState }))}
                disabled={busyNow}
              >
                <option value="open">Открыта</option>
                <option value="closed">Закрыта</option>
              </select>
            </label>
          )}

          <div className="action-gateway-form-actions action-gateway-field-wide">
            <button type="submit" className="action-gateway-primary" disabled={!actionReady || busyNow}>
              {busy === "prepare" ? "Проверяем цель…" : "Подготовить предпросмотр"}
            </button>
            <span>Сначала будет только проверка точной цели в режиме чтения.</span>
          </div>
        </form>

        <aside className="action-gateway-explainer">
          <span className="action-gateway-eyebrow">Как это работает</span>
          <h3>Решение остаётся у тебя</h3>
          <ol>
            <li><strong>Подготовь.</strong> Сервис перечитает репозиторий и задачу.</li>
            <li><strong>Проверь.</strong> Предпросмотр покажет точную цель, риск и срок.</li>
            <li><strong>Подтверди.</strong> Только эта кнопка разрешает одну попытку.</li>
          </ol>
          <p>Подготовленные данные и подтверждение живут только в памяти этой страницы.</p>
        </aside>
      </div>

      {plan && (
        <div className="action-gateway-plan" data-plan-mode={planMode}>
          <div className="action-gateway-plan-heading">
            <div>
              <span className="action-gateway-eyebrow">Шаг 2 · {planMode === "compensation" ? "компенсация" : "предпросмотр"}</span>
              <h3>{planMode === "compensation" ? "Предпросмотр компенсации" : "Предпросмотр действия"}</h3>
            </div>
            <span className="action-gateway-plan-target">{planTarget(plan)}</span>
          </div>
          <pre className="action-gateway-preview">{plan.prepared.preview}</pre>
          <p className="action-gateway-expiry">Подготовлено до: {formatTime(plan.prepared.expires_at)}. Срок истечения проверяется сервером.</p>
          <div className="action-gateway-plan-actions">
            <button type="button" className="action-gateway-primary" onClick={() => void handleExecute()} disabled={!confirmationToken || busyNow}>
              {busy === "execute" ? "Выполняем…" : planMode === "compensation" ? "Подтвердить компенсацию" : "Подтвердить и выполнить"}
            </button>
            <button type="button" className="action-gateway-secondary" onClick={clearPlan} disabled={busyNow}>Отменить предпросмотр</button>
          </div>
        </div>
      )}

      {error && <p className="action-gateway-message action-gateway-message-error" role="alert">{error}</p>}
      {notice && <p className="action-gateway-message action-gateway-message-notice" role="status" aria-live="polite">{notice}</p>}

      {receipt && (
        <div className="action-gateway-receipt" data-state={receipt.state} role="status" aria-live="polite">
          <div>
            <span className="action-gateway-eyebrow">Шаг 3 · квитанция</span>
            <h3>{RECEIPT_STATE_LABELS[receipt.state]}</h3>
            <p>{ACTION_LABELS[receipt.action_kind]} · {targetLabel(receipt)}</p>
          </div>
          <p className="action-gateway-receipt-detail">
            {receipt.safe_error_code ? (ERROR_LABELS[receipt.safe_error_code] ?? "Операция завершилась с безопасным ограничением.") : `Завершено: ${formatTime(receipt.finished_at)}.`}
          </p>
          {receipt.state === "outcome_uncertain" && plan && (
            <div className="action-gateway-uncertain">
              <strong>Не нажимай «Повторить».</strong>
              <span>Проверь результат запросом только для чтения; новое изменение не отправляется.</span>
              <button type="button" className="action-gateway-secondary" onClick={() => void handleReconcile()} disabled={busyNow}>
                {busy === "reconcile" ? "Проверяем…" : "Проверить результат"}
              </button>
            </div>
          )}
          {canCompensate(receipt) && (
            <button type="button" className="action-gateway-danger" onClick={() => void handleCompensation()} disabled={busyNow}>
              {busy === "compensation" ? "Готовим компенсацию…" : "Подготовить компенсацию"}
            </button>
          )}
          {receipt.remote_url?.startsWith("https://github.com/") && (
            <a className="action-gateway-remote-link" href={receipt.remote_url} target="_blank" rel="noreferrer">Открыть точную цель GitHub</a>
          )}
        </div>
      )}

      <div className="action-gateway-history">
        <div className="action-gateway-history-heading">
          <div>
            <span className="action-gateway-eyebrow">Операционный след</span>
            <h3>История действий</h3>
          </div>
          <button type="button" className="action-gateway-secondary" onClick={() => void handleHistory()} disabled={busyNow}>
            {busy === "history" ? "Загружаем…" : "Показать историю"}
          </button>
        </div>
        {history && (
          history.receipts.length > 0 ? (
            <ol className="action-gateway-history-list">{history.receipts.slice().reverse().map(historyItem)}</ol>
          ) : <p className="action-gateway-empty">Действий пока нет. История не читает содержимое задач.</p>
        )}
      </div>
    </section>
  );
}
