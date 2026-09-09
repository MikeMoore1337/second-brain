import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { SectionMenu } from "../section-menu";
import { PageMotion, usePageMotion } from "../page-motion";

afterEach(() => { document.body.innerHTML = ""; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("discloses navigation, closes on Escape/outside focus and focuses the selected destination", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<><SectionMenu navigation={[["#search", "Поиск", "search"]]} /><section id="search">Поиск заметок</section><button id="outside">Другое действие</button></>));
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
  await act(async () => host.querySelector<HTMLAnchorElement>('a[href="#search"]')!.click());
  expect(document.activeElement).toBe(host.querySelector("#search"));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  await act(async () => root.unmount());
});

it("shares pause with the whole page and suspends decoration when the document is hidden", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  function Probe() { const { paused } = usePageMotion(); return <output>{paused ? "Пауза" : "Движение"}</output>; }
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  await act(async () => root.render(<PageMotion><SectionMenu navigation={[]} /><Probe /></PageMotion>));
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("true");
  await act(async () => host.querySelector<HTMLButtonElement>(".sections-trigger")!.click());
  await act(async () => host.querySelector<HTMLButtonElement>(".dropdown-motion")!.click());
  expect(host.querySelector("output")?.textContent).toBe("Пауза");
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("false");
  await act(async () => host.querySelector<HTMLButtonElement>(".dropdown-motion")!.click());
  hidden.mockReturnValue(true);
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector(".motion-world")?.getAttribute("data-page-motion")).toBe("false");
  await act(async () => root.unmount());
});
