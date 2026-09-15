import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { SectionMenu } from "../section-menu";
import { PageMotion, usePageMotion } from "../page-motion";
import { semanticGroups } from "../semantic-navigation";

afterEach(() => { document.body.innerHTML = ""; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("discloses navigation, closes on Escape/outside focus and focuses the selected destination", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<><SectionMenu groups={semanticGroups} /><section id="semantic-group-memory">Память</section><button id="outside">Другое действие</button></>));
  const trigger = host.querySelector<HTMLButtonElement>(".sections-trigger")!;
  await act(async () => trigger.click());
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  expect(document.activeElement).toBe(trigger);
  expect(host.querySelector("#section-dropdown")).toBeNull();
  await act(async () => trigger.click());
  await act(async () => host.querySelector<HTMLButtonElement>("#outside")!.focus());
  expect(host.querySelector("#section-dropdown")).toBeNull();
  await act(async () => trigger.click());
  await act(async () => host.querySelector<HTMLAnchorElement>('a[href="#semantic-group-memory"]')!.click());
  expect(document.activeElement).toBe(host.querySelector("#semantic-group-memory"));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  await act(async () => root.unmount());
});

it("shows the same four directions in the compact menu", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<><SectionMenu groups={semanticGroups} /><section id="semantic-group-decisions">Решения</section><section id="decision-compass">Компас решения</section></>));
  const trigger = host.querySelector<HTMLButtonElement>(".sections-trigger")!;

  await act(async () => trigger.click());
  expect(host.querySelector(".section-menu-groups")).not.toBeNull();
  expect(host.querySelectorAll(".section-menu-group")).toHaveLength(4);
  expect(host.querySelector('a[href="#semantic-group-decisions"]')?.textContent).toContain("Решения");
  expect(host.querySelectorAll(".section-dropdown-tool-link")).toHaveLength(0);

  await act(async () => host.querySelector<HTMLAnchorElement>('a[href="#semantic-group-decisions"]')?.click());
  expect(document.activeElement).toBe(host.querySelector("#semantic-group-decisions"));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  await act(async () => root.unmount());
});

it("shares pause with the whole page and suspends decoration when the document is hidden", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  function Probe() { const { paused } = usePageMotion(); return <output>{paused ? "Пауза" : "Движение"}</output>; }
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  await act(async () => root.render(<PageMotion><SectionMenu groups={semanticGroups} /><Probe /></PageMotion>));
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("true");
  await act(async () => host.querySelector<HTMLButtonElement>(".sections-trigger")!.click());
  await act(async () => host.querySelector<HTMLInputElement>(".motion-setting input")!.click());
  expect(host.querySelector("output")?.textContent).toBe("Пауза");
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("false");
  await act(async () => host.querySelector<HTMLInputElement>(".motion-setting input")!.click());
  hidden.mockReturnValue(true);
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("false");
  await act(async () => root.unmount());
});
