import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { revealDestination } from "../fold-section";
import { SemanticNavigation, semanticGroups } from "../semantic-navigation";

let root: Root | undefined;
let host: HTMLDivElement | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function renderNavigation(): Promise<HTMLDivElement> {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<SemanticNavigation renderTool={(tool) => <section id={tool.id} data-test-surface />} />);
  });
  return host;
}

it("renders exactly four semantic directions and removes the old decorative cards", async () => {
  const rendered = await renderNavigation();

  expect(rendered.querySelectorAll("[data-semantic-group]")).toHaveLength(4);
  expect(Array.from(rendered.querySelectorAll("[data-semantic-group] > .semantic-group-summary h3")).map((heading) => heading.textContent)).toEqual([
    "Память",
    "Понимание себя",
    "Решения",
    "Развитие",
  ]);
  expect(semanticGroups.map((group) => group.tools.length)).toEqual([5, 4, 4, 5]);
  expect(rendered.querySelector(".pillars")).toBeNull();
  expect(rendered.querySelector("#memory")).toBeNull();
  expect(rendered.querySelector("#growth")).toBeNull();
  expect(rendered.querySelectorAll(".semantic-tool-link")).toHaveLength(18);
  expect(rendered.querySelectorAll(".semantic-functional-surface")).toHaveLength(18);
  expect(rendered.querySelectorAll(".semantic-functional-surface[hidden]")).toHaveLength(18);
  expect(rendered.querySelectorAll(".semantic-functional-surface > details, .semantic-functional-surface > summary")).toHaveLength(0);
  expect(rendered.querySelectorAll(".semantic-functional-heading")).toHaveLength(18);
  expect(rendered.querySelector('[data-semantic-tool-link="active-learning"]')).toBeNull();
});

it("keeps one compact navigation entry per tool and activates its mounted surface", async () => {
  const rendered = await renderNavigation();
  const links = Array.from(rendered.querySelectorAll<HTMLAnchorElement>(".semantic-tool-link"));
  expect(new Set(links.map((link) => link.dataset.semanticToolLink)).size).toBe(18);

  for (const link of links) {
    await act(async () => link.click());
    const toolId = link.dataset.semanticToolLink!;
    const surface = rendered.querySelector<HTMLElement>(`[data-semantic-functional-surface="${toolId}"]`);
    expect(surface?.hasAttribute("hidden"), toolId).toBe(false);
    expect(document.activeElement).toBe(rendered.querySelector(`#${toolId}`));
    expect(rendered.querySelectorAll(".semantic-functional-surface:not([hidden])")).toHaveLength(1);
  }
});

it("uses a keyboard-sized disclosure control and keeps only one direction open", async () => {
  const rendered = await renderNavigation();
  const triggers = rendered.querySelectorAll<HTMLButtonElement>("[data-semantic-group-trigger]");

  expect(triggers).toHaveLength(4);
  for (const trigger of triggers) {
    expect(trigger.type).toBe("button");
    expect(trigger.getAttribute("aria-controls")).not.toBeNull();
    expect(rendered.querySelector("#" + trigger.getAttribute("aria-controls"))).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  }

  const decisions = rendered.querySelector<HTMLButtonElement>(".semantic-group-decisions [data-semantic-group-trigger]")!;
  decisions.focus();
  expect(document.activeElement).toBe(decisions);
  await act(async () => decisions.click());
  expect(decisions.getAttribute("aria-expanded")).toBe("true");
  expect(rendered.querySelector(".semantic-group-decisions [data-semantic-group-panel]")?.hasAttribute("inert")).toBe(false);

  const growth = rendered.querySelector<HTMLButtonElement>(".semantic-group-growth [data-semantic-group-trigger]")!;
  await act(async () => growth.click());
  expect(growth.getAttribute("aria-expanded")).toBe("true");
  expect(decisions.getAttribute("aria-expanded")).toBe("false");
  expect(rendered.querySelector(".semantic-group-decisions [data-semantic-group-panel]")?.hasAttribute("inert")).toBe(true);

  await act(async () => growth.click());
  expect(growth.getAttribute("aria-expanded")).toBe("false");
  expect(rendered.querySelector(".semantic-group-growth [data-semantic-group-panel]")?.getAttribute("aria-hidden")).toBe("true");
});

it("opens the semantic parent and its functional surface for deep links", async () => {
  const rendered = await renderNavigation();
  const target = rendered.querySelector("#decision-journal")!;

  await act(async () => expect(revealDestination("#decision-journal")).toBe(target));
  expect(rendered.querySelector(".semantic-group-memory")?.classList.contains("is-open")).toBe(true);
  expect(rendered.querySelector(".semantic-group-memory [data-semantic-group-panel]")?.getAttribute("aria-hidden")).toBe("false");
  expect(rendered.querySelector('[data-semantic-functional-surface="decision-journal"]')?.hasAttribute("hidden")).toBe(false);
  expect(rendered.querySelector(".semantic-functional-surface > summary")).toBeNull();
  expect(document.activeElement).toBe(target);
  expect(rendered.querySelector(".semantic-group-memory [data-semantic-group-trigger]")?.getAttribute("aria-expanded")).toBe("true");
});
