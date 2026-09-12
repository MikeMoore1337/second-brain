// Stage 9 browser QA against the synthetic server with intercepted bounded fixtures.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const origin = process.env.SB_QA_URL ?? "http://127.0.0.1:8137";
assert.equal(new URL(origin).hostname, "127.0.0.1", "Only a local synthetic server is permitted");
const out = resolve(process.env.SB_QA_OUT ?? "../.local/stage9d-qa");
await mkdir(out, { recursive: true });

const event = {
  event_id: "0190f4c0-1234-7123-8123-1234567890ab",
  created_at: "2026-09-13T10:00:00.000Z",
  kind: "prediction",
  options: [{ id: "a", ordinal: 0, label: "Первый вариант" }],
  predicted_option_id: "a",
  predicted_option_label: "Первый вариант",
  abstention_code: null,
  derivation_version: "simulate-me-v1",
  policy_id: "simulate-me-direct-exact-v1",
};
const decision = {
  decision_id: "0190f4c0-1234-7123-8123-abcdefabcdef",
  evidence_at: "2026-09-13T11:00:00.000Z",
  evidence_at_precision: "exact",
  created: "2026-09-13T11:00:00.000Z",
  options: [
    { index: 0, label: "Первый вариант", fingerprint: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
    { index: 1, label: "Второй вариант", fingerprint: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
  ],
  chosen_option_index: 0,
  chosen_option: "Первый вариант",
};
const pending = {
  events: [event],
  decision_journals: [{ ...decision, options: decision.options.map(({ index, label }) => ({ index, label })) }],
  limits: { max_events: 32, max_decision_journals: 32 },
};
const review = { event, decision, mapping_basis: "owner-explicit-v1", requires_confirmation: true };
const link = {
  status: "linked",
  audit_event_id: event.event_id,
  decision_id: decision.decision_id,
  link_state: "LINKED_VALID",
  linked_at: "2026-09-13T11:01:00.000Z",
};
const calibration = {
  contract_version: "prospective-audit-calibration-v1",
  derivation_version: "simulate-me-v1",
  policy_id: "simulate-me-direct-exact-v1",
  policy_fingerprint: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  retention_policy: "prospective-audit-retention-180d-v1",
  metrics: {
    audited_operations: 1,
    predictions: 1,
    abstentions: 0,
    linked_actual_decisions: 1,
    pending_unlinked_events: 0,
    invalid_linkage_events: 0,
    unavailable_linkage_events: 0,
    exact_option_matches: 1,
    mismatches: 0,
    coverage: { numerator: 1, denominator: 1 },
    actual_linkage_coverage: { numerator: 1, denominator: 1 },
    evaluated_prediction_coverage: { numerator: 1, denominator: 1 },
    accuracy_non_abstained: { numerator: 1, denominator: 1 },
  },
  invalid_linkage_by_code: [],
  unavailable_linkage_by_code: [],
};

const browser = await chromium.launch({
  headless: true,
  ...(process.env.SB_QA_CHROMIUM ? { executablePath: process.env.SB_QA_CHROMIUM } : {}),
});
const report = { widths: [], requests: [], accessibility: [], errors: [] };

try {
  for (const width of [320, 360, 390, 430, 768, 1024, 1440]) {
    const context = await browser.newContext({
      viewport: { width, height: width < 768 ? 844 : 1000 },
      isMobile: width < 768,
      hasTouch: width < 768,
    });
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname === "/api/prospective-audit/execute") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ event }) });
      if (url.pathname === "/api/prospective-audit/pending") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(pending) });
      if (url.pathname === "/api/prospective-audit/link/review") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(review) });
      if (url.pathname === "/api/prospective-audit/link/confirm") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(link) });
      if (url.pathname === "/api/prospective-audit/calibration") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(calibration) });
      return route.continue();
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => report.errors.push(error.message));
    page.on("request", (request) => {
      if (request.url().startsWith(`${origin}/api/`)) report.requests.push({ width, path: new URL(request.url()).pathname, body: request.postDataJSON?.() });
    });
    await page.goto(origin);
    const surface = page.locator("#prospective-audit");
    await surface.evaluate((element) => { const disclosure = element.closest("details"); if (disclosure) disclosure.open = true; });
    await surface.evaluate((element) => element.scrollIntoView({ block: "start" }));
    await page.screenshot({ path: `${out}/initial-${width}.png` });
    const initialLayout = await surface.evaluate((element) => ({
      width: innerWidth,
      overflow: document.documentElement.scrollWidth > innerWidth,
      smallTargets: [...element.querySelectorAll("button,input,select")]
        .filter((control) => control.getAttribute("type") !== "checkbox")
        .filter((control) => { const rect = control.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && (rect.width < 43.5 || rect.height < 43.5); })
        .map((control) => ({ text: control.textContent?.trim(), width: control.getBoundingClientRect().width, height: control.getBoundingClientRect().height })),
    }));
    assert.equal(initialLayout.overflow, false);
    assert.deepEqual(initialLayout.smallTargets, []);
    report.widths.push({ phase: "initial", ...initialLayout });

    if (width !== 390 && width !== 1440) {
      await context.close();
      continue;
    }
    await surface.locator("#stage9-query").fill("Что выбрать?");
    await surface.locator(".stage9-option-row input").nth(1).fill("Первый вариант");
    await surface.getByRole("button", { name: "Выполнить и записать" }).click();
    await surface.getByRole("heading", { name: "ПРОГНОЗ записан" }).waitFor();
    await surface.getByRole("button", { name: "Показать ожидающие связи" }).click();
    await surface.getByRole("button", { name: "Перечитать и проверить" }).click();
    await surface.locator("select[aria-label^='Соответствие']").selectOption("0");
    await surface.locator(".stage9-confirmation input").check();
    await surface.getByRole("button", { name: "Подтвердить связь" }).click();
    await surface.getByText("Связь записана", { exact: false }).waitFor();
    await surface.getByRole("button", { name: "Обновить aggregate" }).click();
    await surface.getByText("Aggregate обновлён.", { exact: true }).waitFor();
    await page.screenshot({ path: `${out}/linked-${width}.png` });
    const accessibility = await new AxeBuilder({ page }).include("#prospective-audit").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    report.accessibility.push({ width, violations: accessibility.violations });
    assert.equal(accessibility.violations.length, 0);
    const allPageApiRequests = report.requests.filter((request) => request.width === width);
    const stage9Requests = allPageApiRequests.filter((request) => request.path.startsWith("/api/prospective-audit/"));
    assert.deepEqual(stage9Requests.map((request) => request.path), [
      "/api/prospective-audit/execute",
      "/api/prospective-audit/pending",
      "/api/prospective-audit/link/review",
      "/api/prospective-audit/link/confirm",
      "/api/prospective-audit/calibration",
    ]);
    assert.equal(allPageApiRequests.some((request) => request.path === "/api/simulate-me"), false);
    const confirmRequest = stage9Requests.find((request) => request.path.endsWith("/link/confirm"));
    assert.deepEqual(confirmRequest?.body?.mapping, [{ audit_option_id: "a", decision_option_index: 0, decision_option_fingerprint: decision.options[0].fingerprint }]);
    report.widths.push({ phase: "linked", width, overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth) });
    await context.close();
  }
} finally {
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}

assert.equal(report.errors.length, 0);
assert.equal(report.widths.some((entry) => entry.overflow), false);
console.log("Prospective Audit browser QA passed");
