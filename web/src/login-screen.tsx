import type { ReactElement } from "react";
import brainMark from "./assets/brain-mark-48.webp";
import brainMark2x from "./assets/brain-mark-96.webp";
import memoryBrain from "./assets/memory-brain-480.webp";
import memoryBrain2x from "./assets/memory-brain-800.webp";

export type LoginError = "oauth" | "denied";

const ERROR_COPY: Record<LoginError, { readonly title: string; readonly message: string }> = {
  oauth: {
    title: "Не удалось выполнить вход",
    message: "Попробуйте повторить вход через GitHub.",
  },
  denied: {
    title: "Доступ закрыт",
    message: "Этот Second Brain доступен только владельцу.",
  },
};

function queryError(): LoginError | undefined {
  const value = new URLSearchParams(window.location.search).get("error");
  return value === "oauth" || value === "denied" ? value : undefined;
}

function GitHubMark(): ReactElement {
  return (
    <svg className="login-github-mark" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 .5a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.04c-3.34.73-4.04-1.42-4.04-1.42-.55-1.4-1.33-1.77-1.33-1.77-1.09-.75.08-.74.08-.74 1.2.09 1.84 1.23 1.84 1.23 1.07 1.84 2.8 1.31 3.48 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.23-3.22-.12-.3-.53-1.52.12-3.17 0 0 1-.32 3.3 1.23a11.4 11.4 0 0 1 6 0c2.3-1.55 3.3-1.23 3.3-1.23.65 1.65.24 2.87.12 3.17.76.84 1.23 1.91 1.23 3.22 0 4.61-2.8 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.3c0 .32.22.7.83.58A12 12 0 0 0 12 .5Z"
      />
    </svg>
  );
}

export function LoginScreen({ error }: { readonly error?: LoginError }): ReactElement {
  const activeError = error ?? queryError();
  const copy = activeError === undefined ? undefined : ERROR_COPY[activeError];

  return (
    <div className="login-screen" data-auth-screen>
      <header className="login-header">
        <a className="login-brand" href="/login" aria-label="Second Brain — вход">
          <img
            src={brainMark}
            srcSet={`${brainMark} 1x, ${brainMark2x} 2x`}
            width={44}
            height={44}
            alt=""
          />
          <span>Second Brain</span>
        </a>
        <span className="login-header-note">Личное пространство</span>
      </header>

      <main className="login-layout" aria-labelledby="login-title">
        <section className="login-copy">
          <p className="login-context">Личное пространство</p>
          <h1 id="login-title">Second Brain</h1>
          <p className="login-lede">Сохраняй мысли. Находи связи. Возвращайся к важному.</p>
          <p className="login-owner-note">Доступ разрешён только владельцу.</p>

          {copy !== undefined && (
            <div className={`login-error login-error-${activeError}`} role="alert" aria-live="assertive">
              <strong>{copy.title}</strong>
              <span>{copy.message}</span>
            </div>
          )}

          <a className="login-primary" href="/auth/github/login" data-login-action>
            <GitHubMark />
            <span>Войти через GitHub</span>
          </a>
          <p className="login-reassurance">Вход подтверждается на сервере по вашему идентификатору GitHub.</p>
        </section>

        <div className="login-scene" aria-hidden="true">
          <div className="login-scene-glow login-scene-glow-one" />
          <div className="login-scene-glow login-scene-glow-two" />
          <span className="login-scene-line login-scene-line-one" />
          <span className="login-scene-line login-scene-line-two" />
          <img
            className="login-brain"
            src={memoryBrain}
            srcSet={`${memoryBrain} 480w, ${memoryBrain2x} 800w`}
            sizes="(max-width: 700px) 78vw, 48vw"
            width={800}
            height={800}
            alt=""
          />
          <span className="login-scene-caption">Личная память, только для владельца</span>
        </div>
      </main>

      <footer className="login-footer">Приватное пространство владельца</footer>
    </div>
  );
}
