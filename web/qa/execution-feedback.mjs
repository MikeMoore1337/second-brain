import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:4173";
assert.equal(new URL(origin).hostname, "127.0.0.1", "Only a local QA origin is permitted");
const out = process.env.SB_QA_OUT ?? "../.local/stage18-5-qa";
await mkdir(out, { recursive: true });

const plan = {
  planning_snapshot_id: "0198f4c5-6a00-7000-0000-000000000004",
  planning_snapshot_fingerprint: "a".repeat(64),
  planning_plan_revision: 1,
  as_of: "2026-09-16T06:00:00Z",
  start_local: "2026-09-16T09:00",
  end_local: "2026-09-16T18:00",
  planning_timezone: "Europe/Moscow",
  selected_item_count: 1,
  executable_item_count: 1,
  source_status: "current",
};

const itemBase = {
  planning_snapshot_id: plan.planning_snapshot_id,
  planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint,
  planning_plan_revision: 1,
  planning_policy_id: "stage17-personal-planning-v1",
  planning_policy_fingerprint: "c".repeat(64),
  item_id: "next-1",
  accepted_item_fingerprint: "b".repeat(64),
  item_kind: "next_action",
  title: "Сделать небольшой шаг",
  description: "Выполнить выбранное действие.",
  goal_refs: [{ goal_source_uuid: "0198f4c5-6a00-7000-8000-000000000001", goal_identity_fingerprint: "d".repeat(64) }],
  action_refs: [],
  parent_item_id: null,
  target_start_local: "2026-09-16T10:00",
  target_end_local: "2026-09-16T11:00",
  planning_timezone: plan.planning_timezone,
  planned_effort_minutes: 30,
  source_status: "current",
  first_start_at: null,
  latest_event_at: null,
  latest_effective_event_at: null,
  terminal_at: null,
  current_block_reasons: [],
  current_block_note: "",
  actual_effort_minutes: null,
  effort_precision: "unknown",
  terminal_feedback: null,
  window_relation: "unknown",
  event_count: 0,
  effective_event_count: 0,
  voided_event_count: 0,
  correction_count: 0,
  history: [],
  caveats: [],
};

function itemFor(state) {
  if (state === "not_started") return { ...itemBase, state };
  const event = {
    event_id: "0198f4c5-6a00-7000-0000-000000000010",
    event_fingerprint: "e".repeat(64),
    event_type: state === "completed" ? "complete" : "start",
    occurred_at: "2026-09-16T12:30:00Z",
    effective: true,
    voided: false,
    correction: false,
    void_target_event_id: null,
    actual_effort_minutes: state === "completed" ? 42 : null,
    effort_precision: state === "completed" ? "exact" : "unknown",
    actual_result_note: state === "completed" ? "Готово" : "",
    reason_codes: [],
    deviation_codes: [],
    result_disposition: state === "completed" ? "as_planned" : null,
  };
  return {
    ...itemBase,
    state,
    first_start_at: "2026-09-16T12:30:00Z",
    latest_event_at: "2026-09-16T12:30:00Z",
    latest_effective_event_at: "2026-09-16T12:30:00Z",
    terminal_at: state === "completed" ? "2026-09-16T12:30:00Z" : null,
    actual_effort_minutes: state === "completed" ? 42 : null,
    effort_precision: state === "completed" ? "exact" : "unknown",
    event_count: 1,
    effective_event_count: 1,
    history: [event],
  };
}

function report(state) {
  const current = itemFor(state);
  return {
    planning_snapshot_id: plan.planning_snapshot_id,
    planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint,
    planning_plan_revision: 1,
    planning_policy_id: "stage17-personal-planning-v1",
    planning_policy_fingerprint: "c".repeat(64),
    plan_start_local: plan.start_local,
    plan_end_local: plan.end_local,
    planning_timezone: plan.planning_timezone,
    source_status: "current",
    selected_item_count: 1,
    planned_executable_item_count: 1,
    items_with_any_event_count: state === "not_started" ? 0 : 1,
    items_with_effective_start_count: state === "not_started" ? 0 : 1,
    not_started_count: state === "not_started" ? 1 : 0,
    in_progress_count: state === "in_progress" ? 1 : 0,
    paused_count: 0,
    blocked_count: 0,
    completed_count: state === "completed" ? 1 : 0,
    abandoned_count: 0,
    terminal_count: state === "completed" ? 1 : 0,
    terminal_exact_effort_count: state === "completed" ? 1 : 0,
    terminal_unknown_effort_count: 0,
    missing_terminal_effort_count: 0,
    terminal_effort_coverage_denominator: state === "completed" ? 1 : 0,
    comparable_effort_count: state === "completed" ? 1 : 0,
    comparable_planned_effort_minutes: state === "completed" ? 30 : 0,
    comparable_actual_effort_minutes: state === "completed" ? 42 : 0,
    aggregate_effort_delta_minutes: state === "completed" ? 12 : 0,
    effort_deltas: state === "completed" ? [{ item_id: "next-1", accepted_item_fingerprint: itemBase.accepted_item_fingerprint, planned_effort_minutes: 30, actual_effort_minutes: 42, delta_minutes: 12 }] : [],
    within_window_count: 0,
    before_window_count: 0,
    after_window_count: state === "completed" ? 1 : 0,
    no_window_count: 0,
    unknown_window_count: state === "completed" ? 0 : 1,
    window_relation_denominator: state === "completed" ? 1 : 0,
    blocker_reason_counts: [],
    terminal_reason_counts: [],
    deviation_reason_counts: [],
    items: [current],
    caveats: [],
  };
}

function stateResponse(state) {
  return {
    web_contract: "execution_feedback_state_web_v1",
    current_plan: plan,
    report: report(state),
    available_snapshots: [plan],
    caveats: [],
  };
}

function jsonResponse(route, body) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "cache-control": "no-store" },
    body: JSON.stringify(body),
  });
}

function calibrationResponse() {
  return {
    web_contract: "execution_feedback_calibration_web_v1",
    status: "ok",
    calibration: {
      selected_plan_count: 1,
      selected_executable_item_count: 1,
      execution_observed_item_count: 1,
      not_started_count: 0,
      in_progress_count: 0,
      paused_count: 0,
      blocked_current_count: 0,
      completed_count: 1,
      abandoned_count: 0,
      terminal_exact_effort_count: 1,
      terminal_unknown_effort_count: 0,
      missing_terminal_effort_count: 0,
      terminal_effort_coverage_denominator: 1,
      effort_comparable_count: 1,
      sum_planned_effort_minutes: 30,
      sum_actual_effort_minutes: 42,
      sum_delta_minutes: 12,
      within_window_count: 0,
      before_window_count: 0,
      after_window_count: 1,
      no_window_count: 0,
      unknown_window_count: 0,
      window_relation_denominator: 1,
      blocker_reason_counts: [],
      terminal_reason_counts: [],
      deviation_reason_counts: [],
      plans: [],
      caveats: [],
    },
  };
}

async function run(width) {
  let executionState = "not_started";
  const calls = [];
  const externalRequests = [];
  const unexpectedApiRequests = [];
  const pageErrors = [];
  const browser = await chromium.launch({ headless: true, executablePath: process.env.SB_QA_CHROMIUM });
  const context = await browser.newContext({ viewport: { width, height: width <= 430 ? 920 : 1000 }, reducedMotion: "reduce", isMobile: width < 768, hasTouch: width < 768 });
  await context.addInitScript(() => {
    window.__stage18StorageCalls = [];
    for (const method of ["getItem", "setItem", "removeItem", "clear"]) {
      const original = Storage.prototype[method];
      Storage.prototype[method] = function (...args) {
        window.__stage18StorageCalls.push(method);
        return original.apply(this, args);
      };
    }
  });
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      externalRequests.push(url.href);
      await route.abort();
      return;
    }
    if (url.pathname === "/api/timeline") {
      await jsonResponse(route, { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 });
      return;
    }
    if (url.pathname === "/api/self-model") {
      await jsonResponse(route, { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 });
      return;
    }
    if (!url.pathname.startsWith("/api/execution-feedback/")) {
      if (url.pathname.startsWith("/api/")) unexpectedApiRequests.push(url.pathname);
      await route.continue();
      return;
    }
    const request = route.request();
    assert.equal(request.headers()["x-second-brain-request"], "execution-feedback-v1");
    assert.match(request.headers()["content-type"] ?? "", /^application\/json/u);
    const body = request.postDataJSON();
    calls.push({ path: url.pathname, body });
    if (url.pathname.endsWith("/state")) {
      assert.deepEqual(body, {});
      await jsonResponse(route, stateResponse(executionState));
      return;
    }
    if (url.pathname.endsWith("/event")) {
      assert.equal(body.planning_snapshot_id, plan.planning_snapshot_id);
      assert.equal(body.planning_snapshot_fingerprint, plan.planning_snapshot_fingerprint);
      assert.equal(body.item_id, itemBase.item_id);
      assert.equal(body.accepted_item_fingerprint, itemBase.accepted_item_fingerprint);
      assert.ok(body.occurred_at.includes("T"));
      executionState = body.event_type === "complete" ? "completed" : "in_progress";
      await jsonResponse(route, { web_contract: "execution_feedback_event_web_v1", status: "recorded", plan, event: { event_id: "0198f4c5-6a00-7000-0000-000000000010", event_fingerprint: "e".repeat(64), event_type: body.event_type, occurred_at: body.occurred_at, effective: true, voided: false, correction: false, void_target_event_id: null, actual_effort_minutes: body.actual_effort_minutes, effort_precision: body.effort_precision, actual_result_note: body.actual_result_note, reason_codes: body.reason_codes, deviation_codes: body.deviation_codes, result_disposition: body.result_disposition }, item: itemFor(executionState), report: report(executionState) });
      return;
    }
    if (url.pathname.endsWith("/calibration")) {
      assert.deepEqual(body.snapshots, [{ planning_snapshot_id: plan.planning_snapshot_id, planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint }]);
      await jsonResponse(route, calibrationResponse());
      return;
    }
    throw new Error(`Unexpected execution route: ${url.pathname}`);
  });

  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  try {
    await page.goto(origin, { waitUntil: "networkidle" });
    await page.locator('[data-semantic-group="growth"] [data-semantic-group-trigger]').click();
    await page.locator('[data-semantic-tool-link="execution-feedback"]').click();
    await page.locator("#execution-feedback").waitFor({ state: "visible" });
    assert.deepEqual(calls, []);

    await page.getByRole("button", { name: "Загрузить состояние", exact: true }).click();
    await page.getByText(itemBase.title, { exact: true }).waitFor();
    await page.locator("[data-execution-action='start']").click();
    assert.equal(await page.locator("[data-execution-dialog]").evaluate((node) => node.contains(document.activeElement)), true);
    assert.equal(await page.locator("#execution-event-time").inputValue(), "");
    await page.getByRole("button", { name: "Сейчас", exact: true }).click();
    await page.getByRole("button", { name: "Подтвердить: Начать", exact: true }).click();
    await page.getByText("В работе", { exact: true }).waitFor();

    await page.locator("[data-execution-action='complete']").click();
    await page.getByText("Указать точно", { exact: true }).click();
    await page.locator("#execution-effort-minutes").fill("42");
    await page.getByRole("button", { name: "Сейчас", exact: true }).click();
    await page.getByRole("button", { name: "Подтвердить: Завершить", exact: true }).click();
    await page.getByText("Завершено", { exact: true }).waitFor();

    await page.locator(".execution-snapshot-choice input").check();
    await page.getByRole("button", { name: "Собрать калибровку", exact: true }).click();
    await page.locator("[data-execution-calibration-result]").waitFor();
    assert.equal(await page.locator("[data-execution-calibration-result]").getByText("Сопоставимых оценок времени", { exact: false }).count(), 1);

    const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    assert.deepEqual(accessibility.violations, []);
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      smallTargets: [...document.querySelectorAll("#execution-feedback button, #execution-feedback input, #execution-feedback select, #execution-feedback textarea")]
        .filter((element) => element.getClientRects().length && element.getAttribute("type") !== "checkbox" && element.getAttribute("type") !== "radio" && (element.getBoundingClientRect().width < 43.5 || element.getBoundingClientRect().height < 43.5))
        .map((element) => element.textContent?.trim() || element.getAttribute("aria-label") || element.id),
    }));
    assert.equal(layout.overflow, false, `${width}: page overflow`);
    assert.deepEqual(layout.smallTargets, [], `${width}: undersized targets`);
    assert.deepEqual(externalRequests, []);
    assert.deepEqual(unexpectedApiRequests, []);
    assert.deepEqual(pageErrors, []);
    const storageCalls = await page.evaluate(() => window.__stage18StorageCalls ?? []);
    assert.deepEqual(storageCalls, []);
    assert.deepEqual(calls.map(({ path }) => path), [
      "/api/execution-feedback/state",
      "/api/execution-feedback/event",
      "/api/execution-feedback/event",
      "/api/execution-feedback/calibration",
    ]);
    if (width === 390 || width === 1440) await page.screenshot({ path: join(out, `execution-feedback-${width}.png`), fullPage: true });
    return { width, calls: calls.length, accessibility: "passed", overflow: false, storageCalls: 0 };
  } finally {
    await context.close();
    await browser.close();
  }
}

const results = [];
for (const width of [320, 360, 390, 430, 768, 1024, 1440]) results.push(await run(width));
console.log(JSON.stringify({ scenario: "execution-feedback-v1", results }));
