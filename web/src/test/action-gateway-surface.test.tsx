import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { ActionGatewaySurface } from "../action-gateway-surface";
import type {
  ActionGatewayExecutionResponse,
  ActionGatewayPrepared,
  ActionGatewayStatusResponse,
} from "../api";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

const status: ActionGatewayStatusResponse = {
  contract: "action-gateway-v1",
  connector: "github_issues",
  policy_id: "github-issues-v1",
  credential_profile_id: "github-actions-primary",
  status: "ready",
  configured: true,
  ready: true,
  repositories: ["MikeMoore1337/second-brain"],
  store_status: "ready",
  action_catalog: [
    { action_kind: "github.issue.create", risk: "controlled_write", reversibility: "compensation_only" },
    { action_kind: "github.issue.comment", risk: "controlled_write", reversibility: "not_supported" },
    { action_kind: "github.issue.set_state", risk: "controlled_write", reversibility: "supported" },
  ],
  owner_confirmation_required: true,
  background_execution: false,
};

const prepared: ActionGatewayPrepared = {
  prepared_action_id: "0199e6b2-1f6d-7d5d-8a7e-3a8d0d6f1a21",
  contract_version: "prepared-external-action-v1",
  operation_id_fingerprint: "a".repeat(64),
  action_kind: "github.issue.create",
  risk: "controlled_write",
  connector: "github_issues",
  connector_policy_id: "github-issues-v1",
  credential_profile_id: "github-actions-primary",
  exact_target_identity: {
    repository: "MikeMoore1337/second-brain",
    repository_id: 1,
    repository_node_id: "R_repo",
  },
  preflight_fingerprint: "b".repeat(64),
  semantic_payload: {
    repository: "MikeMoore1337/second-brain",
    title: "Внешняя задача",
    body: "Текст",
    marker: "<!-- second-brain-action:0199e6b2-1f6d-7d5d-8a7e-3a8d0d6f1a21 -->",
  },
  payload_fingerprint: "c".repeat(64),
  preview: "Предпросмотр действия GitHub\nТочная цель: MikeMoore1337/second-brain",
  preview_fingerprint: "d".repeat(64),
  prepared_at: "2026-09-16T20:00:00Z",
  expires_at: "2026-09-16T20:05:00Z",
  reversibility: "compensation_only",
  provenance: null,
};

const uncertainResult: ActionGatewayExecutionResponse = {
  replayed: false,
  receipt: {
    receipt_id: "0199e6b2-1f6e-7d5d-8a7e-3a8d0d6f1a22",
    receipt_kind: "action",
    operation_id_fingerprint: "a".repeat(64),
    prepared_action_id: prepared.prepared_action_id,
    intent_fingerprint: "e".repeat(64),
    action_kind: "github.issue.create",
    risk: "controlled_write",
    connector_policy_id: "github-issues-v1",
    credential_profile_id: "github-actions-primary",
    target_safe_identity: prepared.exact_target_identity,
    payload_fingerprint: prepared.payload_fingerprint,
    state: "outcome_uncertain",
    attempt_started_at: "2026-09-16T20:01:00Z",
    sent_at: "2026-09-16T20:01:00Z",
    finished_at: "2026-09-16T20:01:01Z",
    remote_safe_identity: null,
    remote_url: null,
    safe_error_code: "provider_outcome_uncertain",
    parent_receipt_id: null,
    reconciliation_receipt_id: null,
    compensation_receipt_id: null,
  },
};

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<ActionGatewaySurface />);
  });
  return host;
}

function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("ActionGatewaySurface", () => {
  it("keeps the owner prepare and confirmation flow in page memory", async () => {
    vi.spyOn(api, "loadActionGatewayStatus").mockResolvedValue(status);
    vi.spyOn(api, "prepareActionGatewayAction").mockResolvedValue({
      prepared,
      confirmation_token: "secret-confirmation-token",
    });
    const execute = vi.spyOn(api, "executeActionGatewayAction").mockResolvedValue(uncertainResult);
    const host = await renderSurface();
    const title = host.querySelector<HTMLInputElement>("input[type='text']");
    const body = host.querySelector<HTMLTextAreaElement>("textarea");
    const form = host.querySelector<HTMLFormElement>("form");
    expect(title).not.toBeNull();
    expect(body).not.toBeNull();
    expect(form).not.toBeNull();
    if (!title || !body || !form) return;

    await act(async () => {
      setValue(title, "Внешняя задача");
      setValue(body, "Текст");
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(host.textContent).toContain("Предпросмотр действия");
    expect(host.textContent).not.toContain("secret-confirmation-token");
    const confirm = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent?.includes("Подтвердить и выполнить"));
    expect(confirm).not.toBeUndefined();

    await act(async () => confirm?.click());

    expect(execute).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Результат не подтверждён");
    expect(host.textContent).toContain("Не нажимай «Повторить»");
    expect(Array.from(host.querySelectorAll("button")).some((button) => button.textContent?.trim() === "Повторить")).toBe(false);
  });

  it("explains disabled connector without rendering a write form as available", async () => {
    vi.spyOn(api, "loadActionGatewayStatus").mockResolvedValue({
      ...status,
      status: "disabled",
      configured: false,
      ready: false,
      repositories: [],
    });
    const host = await renderSurface();

    expect(host.textContent).toContain("Не настроена");
    expect(host.textContent).toContain("Серверный коннектор выключен");
    expect(host.querySelector<HTMLButtonElement>(".action-gateway-primary")?.disabled).toBe(true);
  });
});
