import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:8137";
const parsedOrigin = new URL(origin);
assert.equal(parsedOrigin.protocol, "http:", "Only a local HTTP QA origin is permitted");
assert.equal(parsedOrigin.hostname, "127.0.0.1", "Only a local QA origin is permitted");
const out = resolve(process.env.SB_QA_OUT ?? "../.local/stage20-personal-agent");
const widths = [320, 360, 390, 430, 768, 1024, 1440, 1920];

const planId = "0199f6c0-0000-7000-8000-000000000001";
const fingerprint = (character) => character.repeat(64);
const goal = {
  goal_source_uuid: "0199f6c0-0000-7000-8000-000000000002",
  goal_identity_fingerprint: `sha256:${fingerprint("1")}`,
};
const actionRef = {
  goal_source_uuid: goal.goal_source_uuid,
  goal_identity_fingerprint: goal.goal_identity_fingerprint,
  reviewed_action_id: "action-1",
};
const items = [
  {
    item_id: "item-1",
    kind: "next_action",
    title: "Проверить следующий шаг",
    description: "Синтетический исполнимый элемент для browser QA.",
    goal_refs: [goal],
    action_refs: [actionRef],
    effort_minutes: 30,
  },
  {
    item_id: "item-2",
    kind: "commitment",
    title: "Зафиксировать решение",
    description: "Второй синтетический исполнимый элемент.",
    goal_refs: [goal],
    action_refs: [{ ...actionRef, reviewed_action_id: "action-2" }],
    effort_minutes: 20,
  },
];
const plan = {
  plan_version: "1",
  plan_id: planId,
  revision: 1,
  as_of: "2026-09-17T08:00:00Z",
  source_pack_fingerprint: fingerprint("a"),
  provider_envelope_fingerprint: fingerprint("b"),
  provider_result_fingerprint: fingerprint("c"),
  proposal_fingerprint: fingerprint("d"),
  policy_id: "stage17-personal-planning-v1",
  policy_fingerprint: fingerprint("e"),
  start_local: "2026-09-17",
  end_local: "2026-09-19",
  timezone: "Europe/Moscow",
  capacity: [],
  fixed_windows: [],
  items,
  selected_item_ids: items.map((item) => item.item_id),
  item_order: items.map((item) => item.item_id),
  plan_fingerprint: fingerprint("f"),
};
const state = {
  web_contract: "personal_agent_state_web_v1",
  current_plan: plan,
  execution_items: items.map((item) => ({
    item_id: item.item_id,
    projection: {
      item_id: item.item_id,
      accepted_item_fingerprint: fingerprint(item.item_id === "item-1" ? "7" : "8"),
      item_kind: item.kind,
      source_status: "current",
      state: "not_started",
      current_block_reasons: [],
      caveats: [],
    },
  })),
  stage19: {
    contract: "action-gateway-v1",
    connector: "github_issues",
    policy_id: "github-issues-v1",
    policy_fingerprint: fingerprint("9"),
    status: "ready",
    configured: true,
    repositories: ["MikeMoore1337/second-brain"],
    action_catalog: [
      { action_kind: "github.issue.create", risk: "controlled_write", reversibility: "compensation_only" },
      { action_kind: "github.issue.comment", risk: "controlled_write", reversibility: "not_supported" },
      { action_kind: "github.issue.set_state", risk: "controlled_write", reversibility: "supported" },
    ],
    owner_confirmation_required: true,
    background_execution: false,
  },
  current_run: null,
  run_history: [],
  caveats: [],
};
const providerPreview = {
  mission_fingerprint: fingerprint("a"),
  context_pack_fingerprint: fingerprint("b"),
  canonical_json: '{"task":"Проверить следующий шаг","options":[],"explicit_constraints":[],"explicit_goals":[],"explicit_context":[]}',
  canonical_bytes_sha256: fingerprint("c"),
  assistant_envelope: {
    task: "Проверить следующий шаг",
    options: [],
    explicit_constraints: [],
    explicit_goals: [],
    explicit_context: [],
  },
};
const proposal = {
  contract_version: "personal-agent-run-proposal-v1",
  proposal_id: "0199f6c0-0000-7000-8000-000000000003",
  mission_fingerprint: providerPreview.mission_fingerprint,
  context_pack_fingerprint: providerPreview.context_pack_fingerprint,
  steps: [
    {
      step_id: "action-1",
      position: 1,
      kind: "stage19_action",
      action: {
        action_kind: "github.issue.create",
        repository: "MikeMoore1337/second-brain",
        title: "Синтетическая проверка",
        body: "Это только предпросмотр; внешний шлюз не вызывается.",
      },
    },
  ],
  caveats: [],
  provider_policy_id: "stage20-personal-agent-v1",
  provider_policy_fingerprint: fingerprint("d"),
  provider_fingerprint: fingerprint("e"),
  proposal_fingerprint: fingerprint("f"),
};

function jsonResponse(route, body) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "cache-control": "no-store, private" },
    body: JSON.stringify(body),
  });
}

function installStorageGuard(context) {
  return context.addInitScript(() => {
    const events = [];
    Object.defineProperty(window, "__secondBrainQaStorageEvents", { value: events });
    for (const method of ["getItem", "setItem", "removeItem", "clear"]) {
      const original = Storage.prototype[method];
      Storage.prototype[method] = function (...args) {
        events.push(`storage.${method}`);
        return original.apply(this, args);
      };
    }
    if ("indexedDB" in window && typeof IDBFactory !== "undefined") {
      const originalOpen = IDBFactory.prototype.open;
      IDBFactory.prototype.open = function (...args) {
        events.push("indexedDB.open");
        return originalOpen.apply(this, args);
      };
    }
  });
}

async function installRoutes(context, calls, externalRequests, unexpectedApiRequests) {
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
    if (url.pathname === "/api/timeline") {
      await jsonResponse(route, { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 });
      return;
    }
    if (url.pathname === "/api/action-gateway/status") {
      assert.equal(request.method(), "POST");
      assert.equal(request.headers()["x-second-brain-request"], "action-gateway-v1");
      await jsonResponse(route, {
        contract: "action-gateway-v1",
        connector: "github_issues",
        policy_id: "github-issues-v1",
        credential_profile_id: "github-actions-primary",
        status: "ready",
        configured: true,
        ready: true,
        repositories: ["MikeMoore1337/second-brain"],
        store_status: "ready",
        action_catalog: state.stage19.action_catalog,
        owner_confirmation_required: true,
        background_execution: false,
      });
      return;
    }
    if (!url.pathname.startsWith("/api/personal-agent/")) {
      if (url.pathname.startsWith("/api/")) {
        unexpectedApiRequests.push(url.pathname);
        await route.abort();
        return;
      }
      await route.continue();
      return;
    }

    assert.equal(request.method(), "POST");
    assert.equal(request.headers()["x-second-brain-request"], "personal-agent-v1");
    assert.match(request.headers()["content-type"] ?? "", /^application\/json/u);
    const body = request.postDataJSON();
    calls.push({ path: url.pathname, body });
    switch (url.pathname) {
      case "/api/personal-agent/state":
        assert.deepEqual(body, {});
        await jsonResponse(route, state);
        return;
      case "/api/personal-agent/context":
        assert.equal(body.mission.task, "Проверить следующий шаг");
        assert.equal(body.mission.planning_snapshot_id, planId);
        assert.equal(body.mission.selected_items.length, 2);
        assert.deepEqual(body.mission.external_targets, [{
          action_kind: "github.issue.create",
          repository: "MikeMoore1337/second-brain",
          issue_number: null,
        }]);
        await jsonResponse(route, {
          web_contract: "personal_agent_context_web_v1",
          context_pack: { source: "synthetic-exact-context" },
          provider_preview: providerPreview,
          current_plan: plan,
        });
        return;
      case "/api/personal-agent/build":
        assert.equal(body.provider_preview.canonical_json, providerPreview.canonical_json);
        assert.equal(JSON.stringify(body).includes("confirmation-token"), false);
        await jsonResponse(route, {
          web_contract: "personal_agent_build_web_v1",
          context_pack: { source: "synthetic-exact-context" },
          provider_preview: providerPreview,
          proposal,
        });
        return;
      default:
        throw new Error(`Unexpected Stage20 route: ${url.pathname}`);
    }
  });
}

async function assertLayout(page, width) {
  const layout = await page.evaluate(() => {
    const visible = (element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.width > 0 && bounds.height > 0 && !element.hasAttribute("hidden") && !element.closest("[hidden]");
    };
    const smallTargets = [...document.querySelectorAll("#chief-of-staff button, #chief-of-staff input, #chief-of-staff textarea, #chief-of-staff select")]
      .filter(visible)
      .filter((element) => element.getAttribute("type") !== "checkbox")
      .filter((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.width < 43.5 || bounds.height < 43.5;
      })
      .map((element) => ({ text: element.textContent?.trim().slice(0, 60), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }));
    return {
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      smallTargets,
      liveStatus: [...document.querySelectorAll("#chief-of-staff [role=status]")].some((element) => element.getAttribute("aria-live") === "polite"),
      serviceWorkerController: navigator.serviceWorker?.controller !== null,
    };
  });
  assert.equal(layout.overflow, false, `horizontal overflow at ${width}px`);
  assert.deepEqual(layout.smallTargets, [], `small interactive target at ${width}px`);
  assert.equal(layout.liveStatus, true, `live async status is missing at ${width}px`);
  assert.equal(layout.serviceWorkerController, false, `private page is service-worker controlled at ${width}px`);
  return layout;
}

async function run(width, browser) {
  const context = await browser.newContext({
    viewport: { width, height: width < 768 ? 844 : 1000 },
    isMobile: width < 768,
    hasTouch: width < 768,
    reducedMotion: width === 390 ? "reduce" : "no-preference",
    serviceWorkers: "block",
  });
  const calls = [];
  const externalRequests = [];
  const unexpectedApiRequests = [];
  const pageErrors = [];
  const consoleMessages = [];
  try {
    await installStorageGuard(context);
    await installRoutes(context, calls, externalRequests, unexpectedApiRequests);
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => consoleMessages.push(message.text()));
    await page.goto(origin, { waitUntil: "domcontentloaded" });
    const surface = page.locator("#chief-of-staff");
    await surface.waitFor({ state: "visible" });
    await page.getByText("Проверить следующий шаг", { exact: true }).first().waitFor();
    assert.deepEqual(calls.map(({ path }) => path), ["/api/personal-agent/state"]);

    await page.getByLabel("Миссия", { exact: true }).fill("Проверить следующий шаг");
    await page.locator("#chief-of-staff select").nth(1).selectOption("MikeMoore1337/second-brain");
    await page.getByRole("button", { name: "Собрать точный контекст", exact: true }).click();
    await page.getByRole("heading", { name: "Проверь, что увидит провайдер", exact: true }).waitFor();
    assert.deepEqual(calls.map(({ path }) => path), ["/api/personal-agent/state", "/api/personal-agent/context"]);
    assert.equal(calls.filter(({ path }) => path === "/api/personal-agent/build").length, 0);

    await page.locator(".personal-agent-preview summary").click();
    assert.equal(await page.locator(".personal-agent-preview pre").textContent(), providerPreview.canonical_json);
    await page.getByRole("button", { name: "Построить предложение операции", exact: true }).click();
    await page.getByRole("heading", { name: "Проверь линейную операцию", exact: true }).waitFor();
    await page.locator('[data-step-kind="stage19_action"]').waitFor();
    assert.equal(calls.filter(({ path }) => path === "/api/personal-agent/build").length, 1);
    assert.equal(calls.filter(({ path }) => path === "/api/personal-agent/accept").length, 0);
    assert.equal(calls.some(({ path }) => path.startsWith("/api/action-gateway/")), false);
    assert.match(await surface.textContent(), /Контролируемое изменение/u);
    assert.doesNotMatch(await page.locator("body").textContent(), /Автопилот|Одобрить всё|Запустить всё|Продолжить автоматически/u);

    const focusTarget = page.getByRole("button", { name: "Принять операцию", exact: true });
    await focusTarget.focus();
    assert.equal(await focusTarget.evaluate((element) => element === document.activeElement), true);
    assert.equal(await surface.getAttribute("aria-busy"), "false");
    const layout = await assertLayout(page, width);
    if (width === 390 || width === 1440) {
      const accessibility = await new AxeBuilder({ page })
        .include("#chief-of-staff")
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      assert.deepEqual(accessibility.violations, []);
      await page.screenshot({ path: `${out}/personal-agent-${width}.png`, fullPage: true });
    }
    const storageEvents = await page.evaluate(() => window.__secondBrainQaStorageEvents ?? []);
    assert.deepEqual(storageEvents, [], `browser persistence API used at ${width}px`);
    assert.deepEqual(externalRequests, []);
    assert.deepEqual(unexpectedApiRequests, []);
    assert.deepEqual(pageErrors, []);
    assert.equal(consoleMessages.some((message) => message.includes(providerPreview.canonical_json)), false);
    return { width, ...layout, calls: calls.map(({ path }) => path), axe: width === 390 || width === 1440 ? "passed" : "not_run" };
  } finally {
    await context.close();
  }
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.SB_QA_CHROMIUM ? { executablePath: process.env.SB_QA_CHROMIUM } : {}),
});
try {
  const results = [];
  for (const width of widths) results.push(await run(width, browser));
  console.log(JSON.stringify({ scenario: "personal-agent-v1-adversarial-ui", results }));
} finally {
  await browser.close();
}
