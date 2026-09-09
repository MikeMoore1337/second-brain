import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { CinematicHero } from "../cinematic-hero";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = ""; });

it("pauses visible decoration explicitly and keeps capture/search actionable", async () => {
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
  await act(async () => host.querySelector<HTMLButtonElement>("button")?.click());
  expect(host.querySelector("section")?.getAttribute("data-moving")).toBe("false");
  expect(host.querySelector("button")?.getAttribute("aria-pressed")).toBe("true");
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
