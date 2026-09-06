import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "../App";

let root: Root | undefined;

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = undefined;
  document.body.innerHTML = "";
});

async function renderApp(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<App />);
  });
  return host;
}

describe("React foundation shell", () => {
  it("mounts an accessible shell without product data", async () => {
    const host = await renderApp();

    expect(host.querySelector("main#main-content")).not.toBeNull();
    expect(host.querySelector('[aria-label="Основная навигация"]')).not.toBeNull();
    expect(host.querySelector("#foundation-title")?.textContent).toContain(
      "Сигнал остаётся проверяемым",
    );
    expect(host.querySelector("[aria-live='polite']")).not.toBeNull();
  });

  it("opens the only foundation dialog through an accessible trigger", async () => {
    const host = await renderApp();
    const trigger = host.querySelector<HTMLButtonElement>(
      '[data-testid="foundation-dialog-trigger"]',
    );

    expect(trigger).not.toBeNull();
    await act(async () => {
      trigger?.click();
    });

    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.body.textContent).toContain("Границы foundation");
  });
});
