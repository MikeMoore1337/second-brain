import * as Dialog from "@radix-ui/react-dialog";
import { motion, MotionConfig } from "motion/react";
import { useState, type FormEvent, type ReactElement } from "react";

import { searchNotes } from "./api";

type SearchState = "idle" | "checking" | "ready" | "error";

const navigation = [
  { href: "#memory", label: "Memory" },
  { href: "#growth", label: "Growth" },
  { href: "#signal", label: "Signal" },
] as const;

export function App(): ReactElement {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState<SearchState>("idle");

  async function handleSearch(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!query.trim() || searchState === "checking") {
      return;
    }

    setSearchState("checking");
    try {
      await searchNotes(query);
      setSearchState("ready");
    } catch {
      setSearchState("error");
    }
  }

  const searchStatus = {
    idle: "Готово к проверке существующего same-origin API.",
    checking: "Проверяю read-only Search contract…",
    ready: "Search contract отвечает; данные остаются server-owned.",
    error: "Не удалось проверить Search contract. Повтори запрос локально.",
  }[searchState];

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen bg-sb-void text-sb-text-primary">
        <header className="border-b border-sb-line bg-sb-ink/85">
          <div className="mx-auto flex min-h-16 max-w-6xl items-center justify-between gap-6 px-5 py-3 sm:px-8">
            <a
              className="brand-link font-mono text-sm font-semibold tracking-[0.16em] text-sb-text-primary"
              href="#signal"
            >
              SECOND BRAIN
            </a>
            <nav aria-label="Основная навигация">
              <ul className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 font-mono text-xs text-sb-text-secondary">
                {navigation.map((item) => (
                  <li key={item.href}>
                    <a className="route-link" href={item.href}>
                      {item.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
        </header>

        <main id="main-content" className="mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
          <motion.section
            aria-labelledby="foundation-title"
            className="signal-plane grid gap-10 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.6fr)] lg:items-end"
            initial={{ opacity: 0, transform: "translateY(8px)" }}
            animate={{ opacity: 1, transform: "translateY(0)" }}
            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
          >
            <div>
              <p className="mb-4 font-mono text-xs uppercase tracking-[0.18em] text-sb-accent">
                React foundation · #141
              </p>
              <h1
                id="foundation-title"
                className="max-w-3xl text-4xl font-semibold tracking-[-0.03em] text-sb-text-primary sm:text-6xl"
              >
                Сигнал остаётся проверяемым.
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-7 text-sb-text-secondary sm:text-lg">
                Это минимальная React/TypeScript foundation surface. Полный перенос
                текущего Web GUI и его русскоязычных сценариев остаётся отдельной задачей
                #142.
              </p>
            </div>

            <div className="signal-basin" id="signal">
              <div className="mb-5 flex items-center justify-between gap-4">
                <span className="font-mono text-xs uppercase tracking-[0.14em] text-sb-text-tertiary">
                  Runtime seam
                </span>
                <span className="signal-node" aria-hidden="true" />
              </div>
              <p className="text-sm leading-6 text-sb-text-secondary">
                React отвечает за presentation-layer. FastAPI сохраняет API, security и
                canonical vault boundaries.
              </p>
              <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
                <Dialog.Trigger asChild>
                  <button
                    className="primary-control mt-6 w-full"
                    data-testid="foundation-dialog-trigger"
                    type="button"
                  >
                    Открыть границы foundation
                  </button>
                </Dialog.Trigger>
                <Dialog.Portal>
                  <Dialog.Overlay className="foundation-dialog-overlay" />
                  <Dialog.Content
                    aria-describedby="foundation-dialog-description"
                    className="foundation-dialog-content"
                  >
                    <Dialog.Title className="text-xl font-semibold text-sb-text-primary">
                      Границы foundation
                    </Dialog.Title>
                    <Dialog.Description
                      className="mt-3 text-sm leading-6 text-sb-text-secondary"
                      id="foundation-dialog-description"
                    >
                      Здесь нет новой persistence, auth, provider или API-логики. React
                      вызывает только существующие same-origin endpoints.
                    </Dialog.Description>
                    <Dialog.Close asChild>
                      <button className="secondary-control mt-6 w-full" type="button">
                        Закрыть
                      </button>
                    </Dialog.Close>
                  </Dialog.Content>
                </Dialog.Portal>
              </Dialog.Root>
            </div>
          </motion.section>

          <section className="mt-12 grid gap-5 md:grid-cols-2" id="memory">
            <div className="surface-line">
              <p className="font-mono text-xs uppercase tracking-[0.14em] text-sb-text-tertiary">
                Memory
              </p>
              <h2 className="mt-3 text-xl font-medium text-sb-text-primary">
                Каноническим остаётся vault.
              </h2>
              <p className="mt-3 text-sm leading-6 text-sb-text-secondary">
                Browser state не получает authority над Markdown-first knowledge base.
              </p>
            </div>
            <div className="surface-line" id="growth">
              <p className="font-mono text-xs uppercase tracking-[0.14em] text-sb-text-tertiary">
                Growth
              </p>
              <h2 className="mt-3 text-xl font-medium text-sb-text-primary">
                Parity следует после foundation.
              </h2>
              <p className="mt-3 text-sm leading-6 text-sb-text-secondary">
                Существующие capture, review, write и cognitive surfaces не исчезают до
                завершения #142.
              </p>
            </div>
          </section>

          <section aria-labelledby="api-check-title" className="mt-12 signal-basin">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.14em] text-sb-text-tertiary">
                  Same-origin API seam
                </p>
                <h2 id="api-check-title" className="mt-3 text-xl font-medium text-sb-text-primary">
                  Проверка существующего Search contract
                </h2>
              </div>
              <span aria-live="polite" className="text-sm text-sb-text-secondary">
                {searchStatus}
              </span>
            </div>
            <form className="mt-6 flex flex-col gap-3 sm:flex-row" onSubmit={handleSearch}>
              <label className="sr-only" htmlFor="foundation-query">
                Запрос для read-only Search
              </label>
              <input
                className="foundation-input min-h-11 min-w-0 flex-1"
                id="foundation-query"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Например, текущий контекст"
                value={query}
              />
              <button className="primary-control min-h-11" disabled={searchState === "checking"} type="submit">
                {searchState === "checking" ? "Проверяю…" : "Проверить API"}
              </button>
            </form>
          </section>
        </main>
      </div>
    </MotionConfig>
  );
}
