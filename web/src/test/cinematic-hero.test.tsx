import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { CinematicHero } from "../cinematic-hero";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = ""; });

it("keeps visible decoration and capture/search actionable without a hero pause button", async () => {
  let intersect: IntersectionObserverCallback = () => {};
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: IntersectionObserverCallback) { intersect = callback; }
    observe() {} disconnect() {}
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<CinematicHero />));
  await act(async () => intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
  expect(host.querySelector("section")?.getAttribute("data-moving")).toBe("true");
  expect(host.querySelector("button")).toBeNull();
  expect(host.querySelector('a[href="#capture"]')).not.toBeNull();
  expect(host.querySelector('a[href="#search"]')).not.toBeNull();
  await act(async () => root.unmount());
});

it("reports reduced motion and never enables continuous movement", async () => {
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({ matches: query.includes("prefers-reduced-motion"), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList));
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<CinematicHero />));
  expect(host.querySelector("section")?.getAttribute("data-moving")).toBe("false");
  expect(host.textContent).toContain("Движение отключено настройкой устройства");
  expect(host.querySelector("button")).toBeNull();
  await act(async () => root.unmount());
});

it("stops on visibilitychange and offscreen, and resumes only when both are visible", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList));
  let intersect: IntersectionObserverCallback = () => {};
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: IntersectionObserverCallback) { intersect = callback; }
    observe() {} disconnect() {}
  });
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<CinematicHero />));
  const visible = async (isIntersecting: boolean) => act(async () => intersect([{ isIntersecting } as IntersectionObserverEntry], {} as IntersectionObserver));
  await visible(true);
  expect(host.querySelector("section")?.dataset.moving).toBe("true");
  hidden.mockReturnValue(true);
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector("section")?.dataset.moving).toBe("false");
  await visible(false);
  hidden.mockReturnValue(false);
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(host.querySelector("section")?.dataset.moving).toBe("false");
  await visible(true);
  expect(host.querySelector("section")?.dataset.moving).toBe("true");
  await act(async () => root.unmount());
});
