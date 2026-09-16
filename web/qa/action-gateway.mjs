import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:5173";
const parsedOrigin = new URL(origin);
assert.equal(parsedOrigin.protocol, "http:", "Only a local HTTP QA origin is permitted");
assert.equal(parsedOrigin.hostname, "127.0.0.1", "Only a local QA origin is permitted");
const out = resolve(process.env.SB_QA_OUT ?? "../.local/stage19-action-gateway");
await mkdir(out, { recursive: true });

const status = {
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
    { action_kind: "github.issue.create", risk: "controlled_write", reversibility: "supported" },
    { action_kind: "github.issue.comment", risk: "controlled_write", reversibility: "not_supported" },
    { action_kind: "github.issue.set_state", risk: "controlled_write", reversibility: "compensation_only" },
  ],
  owner_confirmation_required: true,
  background_execution: false,
};

const prepared = {
  prepared_action_id: "0198c8a0-0000-7000-8000-000000000010",
  contract_version: "prepared-external-action-v1",
  operation_id_fingerprint: "a".repeat(64),
  action_kind: "github.issue.create",
  risk: "controlled_write",
  connector: "github_issues",
  connector_policy_id: "github-issues-v1",
  credential_profile_id: "github-actions-primary",
  exact_target_identity: { repository: "MikeMoore1337/second-brain" },
  preflight_fingerprint: "b".repeat(64),
  semantic_payload: { title: "Проверка action gateway", body: "Тело синтетической задачи" },
  payload_fingerprint: "c".repeat(64),
  preview: "Предпросмотр действия GitHub\nРепозиторий: MikeMoore1337/second-brain\nРиск: контролируемое изменение",
  preview_fingerprint: "d".repeat(64),
  prepared_at: "2026-09-17T08:00:00Z",
  expires_at: "2026-09-17T08:15:00Z",
  reversibility: "supported",
  provenance: null,
};

function receipt(state, remoteUrl = null) {
  return {
    receipt_id: "0198c8a0-0000-7000-8000-000000000011",
    receipt_kind: "action",
    operation_id_fingerprint: prepared.operation_id_fingerprint,
    prepared_action_id: prepared.prepared_action_id,
    intent_fingerprint: "e".repeat(64),
    action_kind: prepared.action_kind,
    risk: "controlled_write",
    connector_policy_id: prepared.connector_policy_id,
    credential_profile_id: prepared.credential_profile_id,
    target_safe_identity: prepared.exact_target_identity,
    payload_fingerprint: prepared.payload_fingerprint,
    state,
    attempt_started_at: "2026-09-17T08:01:00Z",
    sent_at: "2026-09-17T08:01:01Z",
    finished_at: "2026-09-17T08:01:06Z",
    remote_safe_identity: remoteUrl ? { repository: "MikeMoore1337/second-brain", issue_number: 301 } : null,
    remote_url: remoteUrl,
    safe_error_code: state === "outcome_uncertain" ? "provider_outcome_uncertain" : null,
    parent_receipt_id: null,
    reconciliation_receipt_id: null,
    compensation_receipt_id: null,
  };
}

function jsonResponse(route, body) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "cache-control": "no-store, private" },
    body: JSON.stringify(body),
  });
}

async function installRoutes(context, calls, backgroundApiRequests, externalRequests, unexpectedApiRequests) {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) {
      externalRequests.push(url.href);
      await route.abort();
      return;
    }
    if (url.pathname === "/" && request.method() === "GET") {
      const response = await route.fetch();
      const headers = { ...response.headers() };
      delete headers["content-encoding"];
      delete headers["content-length"];
      const body = (await response.text()).replace("<body", '<body data-second-brain-auth-mode="github"');
      await route.fulfill({ status: response.status(), headers, body });
      return;
    }
    if (!url.pathname.startsWith("/api/action-gateway/")) {
      if (url.pathname === "/api/timeline") {
        backgroundApiRequests.push(url.pathname);
        await jsonResponse(route, { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 });
        return;
      }
      if (url.pathname.startsWith("/api/")) {
        unexpectedApiRequests.push(url.pathname);
        await route.abort();
        return;
      }
      await route.continue();
      return;
    }

    assert.equal(request.method(), "POST");
    assert.equal(request.headers()["x-second-brain-request"], "action-gateway-v1");
    assert.match(request.headers()["content-type"] ?? "", /^application\/json/u);
    calls.push(url.pathname);
    switch (url.pathname) {
      case "/api/action-gateway/status":
        assert.deepEqual(request.postDataJSON(), {});
        await jsonResponse(route, status);
        return;
      case "/api/action-gateway/prepare": {
        const body = request.postDataJSON();
        assert.equal(body.intent.action_kind, "github.issue.create");
        assert.equal(body.intent.repository, "MikeMoore1337/second-brain");
        assert.equal(body.intent.title, "Проверка action gateway");
        await jsonResponse(route, { prepared, confirmation_token: "synthetic-page-memory-token" });
        return;
      }
      case "/api/action-gateway/execute": {
        const body = request.postDataJSON();
        assert.equal(body.confirmation_token, "synthetic-page-memory-token");
        assert.equal(body.prepared.prepared_action_id, prepared.prepared_action_id);
        await jsonResponse(route, { receipt: receipt("outcome_uncertain"), replayed: false });
        return;
      }
      case "/api/action-gateway/reconcile":
        assert.equal(request.postDataJSON().prepared.prepared_action_id, prepared.prepared_action_id);
        await jsonResponse(route, {
          receipt: receipt("reconciled_executed", "https://github.com/MikeMoore1337/second-brain/issues/301"),
          replayed: false,
        });
        return;
      case "/api/action-gateway/history":
        assert.deepEqual(request.postDataJSON(), {});
        await jsonResponse(route, { receipts: [], count: 0, truncated: false });
        return;
      default:
        throw new Error(`Unexpected action gateway route: ${url.pathname}`);
    }
  });
}

async function layout(page) {
  return page.evaluate(() => {
    const surface = document.querySelector("#action-gateway");
    const visible = (element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.width > 0 && bounds.height > 0 && !element.hasAttribute("hidden") && !element.closest("[hidden]");
    };
    const smallTargets = surface
      ? [...surface.querySelectorAll("button, input, textarea, select, a")]
        .filter(visible)
        .filter((element) => element.getAttribute("type") !== "checkbox")
        .filter((element) => {
          const bounds = element.getBoundingClientRect();
          return bounds.width < 43.5 || bounds.height < 43.5;
        })
        .map((element) => ({ text: element.textContent?.trim().slice(0, 60), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }))
      : [];
    return {
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      smallTargets,
      surfaceVisible: surface !== null && visible(surface),
    };
  });
}

async function run(width, browser) {
  const context = await browser.newContext({
    viewport: { width, height: width < 768 ? 844 : 1000 },
    isMobile: width < 768,
    hasTouch: width < 768,
  });
  const calls = [];
  const backgroundApiRequests = [];
  const externalRequests = [];
  const unexpectedApiRequests = [];
  const pageErrors = [];
  try {
    await installRoutes(context, calls, backgroundApiRequests, externalRequests, unexpectedApiRequests);
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    await page.getByText("Готова", { exact: true }).waitFor();
    const initialLayout = await layout(page);
    assert.equal(initialLayout.surfaceVisible, true);
    assert.equal(initialLayout.overflow, false);
    assert.deepEqual(initialLayout.smallTargets, []);

    if (width === 390 || width === 1440) {
      await page.locator("#action-gateway").scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${out}/initial-${width}.png`, fullPage: true });
      await page.getByLabel("Заголовок", { exact: true }).fill("Проверка action gateway");
      await page.getByLabel("Текст задачи", { exact: true }).fill("Тело синтетической задачи");
      await page.getByRole("button", { name: "Подготовить предпросмотр", exact: true }).click();
      await page.getByRole("heading", { name: "Предпросмотр действия", exact: true }).waitFor();
      assert.equal((await page.locator("body").textContent())?.includes("synthetic-page-memory-token"), false);
      await page.screenshot({ path: `${out}/preview-${width}.png`, fullPage: true });

      await page.getByRole("button", { name: "Подтвердить и выполнить", exact: true }).click();
      await page.getByRole("heading", { name: "Результат не подтверждён", exact: true }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Повторить", exact: true }).count(), 0);
      assert.equal(await page.getByRole("button", { name: "Проверить результат", exact: true }).count(), 1);
      await page.screenshot({ path: `${out}/uncertain-${width}.png`, fullPage: true });

      await page.getByRole("button", { name: "Проверить результат", exact: true }).click();
      await page.getByRole("heading", { name: "Подтверждено проверкой", exact: true }).waitFor();
      await page.getByRole("button", { name: "Показать историю", exact: true }).click();
      await page.getByText("Действий пока нет.", { exact: false }).waitFor();
      await page.screenshot({ path: `${out}/reconciled-${width}.png`, fullPage: true });
      const accessibility = await new AxeBuilder({ page })
        .include("#action-gateway")
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      assert.deepEqual(accessibility.violations, []);
    }

    const statusCallCount = calls.filter((path) => path === "/api/action-gateway/status").length;
    assert.ok(statusCallCount === 1 || statusCallCount === 2, `unexpected status call count: ${statusCallCount}`);
    const actionCalls = calls.filter((path) => path !== "/api/action-gateway/status");
    assert.deepEqual(actionCalls, width === 390 || width === 1440
      ? [
        "/api/action-gateway/prepare",
        "/api/action-gateway/execute",
        "/api/action-gateway/reconcile",
        "/api/action-gateway/history",
      ]
      : []);
    assert.deepEqual(externalRequests, []);
    assert.deepEqual(unexpectedApiRequests, []);
    assert.deepEqual([...new Set(backgroundApiRequests)], ["/api/timeline"]);
    assert.deepEqual(pageErrors, []);
    return { width, ...initialLayout, calls: calls.length, backgroundApiRequests: backgroundApiRequests.length, axe: width === 390 || width === 1440 ? "passed" : "not_run" };
  } finally {
    await context.close();
  }
}

const browser = await chromium.launch({
  headless: true,
  ...(process.env.SB_QA_CHROMIUM ? { executablePath: process.env.SB_QA_CHROMIUM } : {}),
});
const results = [];
try {
  for (const width of [320, 360, 390, 430, 768, 1024, 1440, 1920]) {
    results.push(await run(width, browser));
  }
} finally {
  await writeFile(`${out}/report.json`, JSON.stringify({ scenario: "action-gateway-v1", results }, null, 2));
  await browser.close();
}

console.log(JSON.stringify({ scenario: "action-gateway-v1", results }));
