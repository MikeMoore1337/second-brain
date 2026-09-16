import assert from "node:assert/strict";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:8137";
assert.equal(new URL(origin).hostname, "127.0.0.1", "Only a local QA origin is permitted");

const goalId = "0198f4c5-6a00-7000-8000-000000000001";
const goalFingerprint = `sha256:${"1".repeat(64)}`;
const packFingerprint = "b".repeat(64);
const item = {
  item_id: "next-1",
  kind: "next_action",
  title: "Сделать небольшой шаг",
  description: "Выполнить выбранное действие.",
  goal_refs: [{ goal_source_uuid: goalId, goal_identity_fingerprint: goalFingerprint }],
  action_refs: [{ reviewed_action_id: "action-1" }],
  parent_item_id: null,
  target_start_local: null,
  target_end_local: null,
  effort_minutes: 30,
  effort_source: "provider_proposed",
  dependency_ids: [],
};
const capacity = [
  { date: "2026-09-16", available_minutes: 60 },
  { date: "2026-09-17", available_minutes: 60 },
  { date: "2026-09-18", available_minutes: 60 },
];
const contextPack = {
  portfolio_order: [goalId],
  start_local: "2026-09-16",
  end_local: "2026-09-18",
  timezone: "UTC",
  capacity,
  fixed_windows: [],
  planning_constraints: [],
  planning_context: "",
  readiness: "exact_current",
  pack_caveats: [],
  pack_fingerprint: packFingerprint,
};
const providerPreview = {
  source_pack_fingerprint: packFingerprint,
  canonical_json: '{"planning":"preview"}',
  canonical_bytes_sha256: "c".repeat(64),
  assistant_envelope: {},
};
const proposal = {
  proposal_id: "0198f4c5-6a00-7000-8000-000000000003",
  result_state: "proposal",
  as_of: "2026-09-16T06:00:00Z",
  source_pack_fingerprint: packFingerprint,
  provider_envelope_fingerprint: "c".repeat(64),
  provider_result_fingerprint: "d".repeat(64),
  policy_id: "stage17-personal-planning-v1",
  policy_fingerprint: "e".repeat(64),
  items: [item],
  suggested_order: [item.item_id],
  reasons: ["Связь с целью сохранена."],
  caveats: [],
  proposal_fingerprint: "f".repeat(64),
};

function plan(revision, title = item.title) {
  return {
    plan_version: "1",
    plan_id: "0198f4c5-6a00-7000-0000-000000000004",
    revision,
    as_of: "2026-09-16T06:00:00Z",
    source_pack_fingerprint: packFingerprint,
    provider_envelope_fingerprint: proposal.provider_envelope_fingerprint,
    provider_result_fingerprint: proposal.provider_result_fingerprint,
    proposal_fingerprint: proposal.proposal_fingerprint,
    policy_id: proposal.policy_id,
    policy_fingerprint: proposal.policy_fingerprint,
    start_local: contextPack.start_local,
    end_local: contextPack.end_local,
    timezone: contextPack.timezone,
    capacity,
    fixed_windows: [],
    items: [{ ...item, title }],
    selected_item_ids: [item.item_id],
    item_order: [item.item_id],
    plan_fingerprint: (revision === 1 ? "g" : "h").repeat(64),
  };
}

const state = {
  web_contract: "personal_planning_state_web_v1",
  goals: [{
    goal_source_uuid: goalId,
    goal_identity_fingerprint: goalFingerprint,
    goal_text: "Улучшить выносливость",
    goal: {},
    strategy_snapshot: {
      snapshot_id: "0198f4c5-6a00-7000-8000-000000000002",
      snapshot_fingerprint: "a".repeat(64),
      sequence: 1,
      selected_actions: [{ action_id: "action-1", kind: "act", generated: {}, reviewed: {}, edited: false }],
    },
  }],
  eligible_goal_count: 1,
  generated_at: "2026-09-16T06:00:00Z",
  current_plan: null,
  caveats: [],
};

const contextResponse = {
  web_contract: "personal_planning_context_web_v1",
  context_pack: contextPack,
  provider_preview: providerPreview,
  current_plan: null,
};
const generateResponse = {
  ...contextResponse,
  web_contract: "personal_planning_generate_web_v1",
  proposal,
};

function jsonResponse(route, body) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "cache-control": "no-store" },
    body: JSON.stringify(body),
  });
}

async function installRoutes(context, calls, externalRequests, backgroundApiRequests, unexpectedApiRequests) {
  await context.route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (requestUrl.origin !== origin) {
      externalRequests.push(requestUrl.href);
      await route.abort();
      return;
    }
    if (!requestUrl.pathname.startsWith("/api/personal-planning/")) {
      if (requestUrl.pathname === "/api/timeline") {
        backgroundApiRequests.push(requestUrl.pathname);
        await jsonResponse(route, { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 });
        return;
      }
      if (requestUrl.pathname.startsWith("/api/")) {
        unexpectedApiRequests.push(requestUrl.pathname);
        await route.abort();
        return;
      }
      await route.continue();
      return;
    }

    const request = route.request();
    assert.equal(request.headers()["x-second-brain-request"], "personal-planning-v1");
    assert.match(request.headers()["content-type"] ?? "", /^application\/json/u);
    const body = request.postDataJSON();
    calls.push({ path: requestUrl.pathname, body });
    switch (requestUrl.pathname) {
      case "/api/personal-planning/state":
        assert.deepEqual(body, {});
        await jsonResponse(route, state);
        return;
      case "/api/personal-planning/context":
        assert.deepEqual(body.goal_source_uuids, [goalId]);
        await jsonResponse(route, contextResponse);
        return;
      case "/api/personal-planning/generate":
        assert.equal(body.provider_preview, providerPreview.canonical_json);
        assert.deepEqual(body.context_pack, contextPack);
        await jsonResponse(route, generateResponse);
        return;
      case "/api/personal-planning/accept":
        assert.deepEqual(body.selected_item_ids, [item.item_id]);
        assert.deepEqual(body.item_order, [item.item_id]);
        await jsonResponse(route, { web_contract: "personal_planning_accept_web_v1", status: "accepted", plan: plan(1) });
        return;
      case "/api/personal-planning/edit":
        assert.equal(body.items[0].title, "Уточнённый шаг");
        await jsonResponse(route, { web_contract: "personal_planning_edit_web_v1", status: "edited", plan: plan(2, "Уточнённый шаг") });
        return;
      default:
        throw new Error(`Unexpected planning route: ${requestUrl.pathname}`);
    }
  });
}

async function assertLayout(page) {
  const layout = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    smallTargets: [...document.querySelectorAll("#personal-planning button, #personal-planning a, #personal-planning input, #personal-planning textarea")]
      .filter((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.width > 0 && bounds.height > 0 && !element.closest("[hidden]") && element.getAttribute("type") !== "checkbox" && (bounds.width < 43.5 || bounds.height < 43.5);
      })
      .map((element) => ({ text: element.textContent?.trim().slice(0, 60), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height })),
  }));
  assert.equal(layout.overflow, false);
  assert.deepEqual(layout.smallTargets, []);
}

async function run(width) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.SB_QA_CHROMIUM });
  const context = await browser.newContext({ viewport: { width, height: width === 320 ? 844 : 1000 }, isMobile: width < 768, hasTouch: width < 768 });
  const calls = [];
  const externalRequests = [];
  const backgroundApiRequests = [];
  const unexpectedApiRequests = [];
  const pageErrors = [];
  await installRoutes(context, calls, externalRequests, backgroundApiRequests, unexpectedApiRequests);
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(origin);
  await page.locator('[data-semantic-group="growth"] [data-semantic-group-trigger]').click();
  await page.locator('[data-semantic-tool-link="personal-planning"]').click();
  await page.locator("#personal-planning").waitFor({ state: "visible" });
  assert.deepEqual(calls, []);
  assert.equal(await page.locator("#planning-preview-title").count(), 0);

  await page.getByRole("button", { name: "Загрузить состояние", exact: true }).click();
  await page.getByText("Улучшить выносливость", { exact: true }).waitFor();
  await page.locator(".personal-planning-goal input").check();
  await page.getByRole("button", { name: "Собрать контекстный пакет", exact: true }).click();
  await page.locator("#planning-preview-title").waitFor();
  await page.locator(".personal-planning-preview summary").click();
  assert.equal(await page.locator(".personal-planning-preview pre").textContent(), providerPreview.canonical_json);
  assert.equal(await page.locator(".personal-planning-proposal").count(), 0);

  await page.getByRole("button", { name: "Получить предложение", exact: true }).click();
  await page.locator("#planning-proposal-title").waitFor();
  await page.getByText(item.title, { exact: true }).waitFor();
  assert.match(await page.locator("#personal-planning").textContent(), /Ничего не исполняется автоматически/u);
  await page.getByRole("button", { name: "Принять выбранные элементы", exact: true }).click();
  await page.getByText("Текущий план · версия 1", { exact: true }).waitFor();
  assert.match(await page.locator("#personal-planning").textContent(), /внешние действия\./u);

  await page.locator(".personal-planning-current input:not([type=checkbox])").first().fill("Уточнённый шаг");
  await page.getByRole("button", { name: "Сохранить изменения плана", exact: true }).click();
  await page.getByText("Текущий план · версия 2", { exact: true }).waitFor();
  assert.equal(await page.locator(".personal-planning-current input:not([type=checkbox])").first().inputValue(), "Уточнённый шаг");

  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  assert.deepEqual(accessibility.violations, []);
  await assertLayout(page);
  assert.deepEqual(externalRequests, []);
  assert.deepEqual([...new Set(backgroundApiRequests)], ["/api/timeline"]);
  assert.deepEqual(unexpectedApiRequests, []);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(calls.map(({ path }) => path), [
    "/api/personal-planning/state",
    "/api/personal-planning/context",
    "/api/personal-planning/generate",
    "/api/personal-planning/accept",
    "/api/personal-planning/edit",
  ]);
  await context.close();
  await browser.close();
  return { width, calls: calls.length, accessibility: "passed", overflow: false, externalRequests: 0, backgroundApiRequests };
}

const results = [];
for (const width of [320, 1440]) results.push(await run(width));
console.log(JSON.stringify({ scenario: "personal-planning-v1", results }));
