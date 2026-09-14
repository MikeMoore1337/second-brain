import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { AccountControl } from "../App";
import { LoginScreen } from "../login-screen";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/");
});

async function render(element: ReactElement): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(element));
  return host;
}

describe("single-owner login surface", () => {
  it("renders the branded login copy and a server-side GitHub action", async () => {
    const host = await render(<LoginScreen />);

    expect(host.querySelector("[data-auth-screen]")).not.toBeNull();
    expect(host.textContent).toContain("Second Brain");
    expect(host.textContent).toContain("Личное пространство");
    expect(host.textContent).toContain(
      "Сохраняй мысли. Находи связи. Возвращайся к важному.",
    );
    expect(host.textContent).toContain("Доступ разрешён только владельцу.");
    expect(host.textContent).toContain("по вашему GitHub ID.");
    expect(host.textContent).not.toContain("идентификатор GitHub");
    expect(host.querySelector<HTMLAnchorElement>('a[href="/auth/github/login"]')?.textContent).toContain(
      "Войти через GitHub",
    );
    expect(host.querySelectorAll("input, textarea, select")).toHaveLength(0);
    expect(host.querySelector(".login-scene")?.getAttribute("aria-hidden")).toBe("true");
  });

  it.each([
    ["oauth", "Не удалось выполнить вход", "Попробуйте повторить вход через GitHub."],
    ["denied", "Доступ закрыт", "Этот Second Brain доступен только владельцу."],
  ] as const)("renders only fixed copy for %s errors", async (error, title, message) => {
    window.history.replaceState({}, "", `/login?error=${error}&token=not-for-display`);
    const host = await render(<LoginScreen />);

    expect(host.querySelector('[role="alert"]')?.textContent).toContain(title);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(message);
    expect(host.textContent).not.toContain("not-for-display");
    expect(host.textContent).not.toContain("42142321");
  });
});

describe("authenticated account control", () => {
  it("exposes the owner label and logout link without an avatar or external image", async () => {
    const host = await render(<AccountControl />);
    const summary = host.querySelector<HTMLElement>("summary");

    expect(summary?.getAttribute("aria-label")).toBe("Меню аккаунта MikeMoore1337");
    expect(host.textContent).toContain("MikeMoore1337");
    expect(host.textContent).toContain("Выйти");
    expect(host.querySelector<HTMLAnchorElement>('a[href="/auth/logout"]')).not.toBeNull();
    expect(host.querySelector("img")).toBeNull();
    expect(host.querySelector("a[href^='http']")).toBeNull();
  });
});
