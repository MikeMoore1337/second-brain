import { MotionConfig } from "motion/react";
import { useEffect, useState, type ReactElement } from "react";
import { CinematicHero } from "./cinematic-hero";
import { LoginScreen, type LoginError } from "./login-screen";
import { PageMotion } from "./page-motion";
import { FoldSection, revealDestination } from "./fold-section";
import { SectionMenu } from "./section-menu";
import memoryBackdrop from "./assets/icons/memory-detail-192.webp";
import growthBackdrop from "./assets/icons/growth-detail-192.webp";
import brainMark from "./assets/brain-mark-48.webp";
import brainMark2x from "./assets/brain-mark-96.webp";

import { AssistantCompareSurface } from "./assistant-compare-surface";
import {
  CaptureSurface,
  DecisionJournalSurface,
  SelfModelSurface,
  SelfRetrievalSurface,
  SearchSurface,
  SimulateMeSurface,
  TimelineSurface,
} from "./parity";
import { DiagnosticsSurface } from "./diagnostics-surface";
import { Icon } from "./icons";
import { applyPwaUpdate, hasPwaUpdate, subscribeToPwaUpdate } from "./pwa";

const navigation = [
  ["#decision-journal", "Журнал решений", "decision"],
  ["#timeline", "Хронология", "timeline"],
  ["#self-model", "Модель себя", "self-model"],
  ["#simulate-me", "Прогноз", "simulate"],
  ["#assistant-compare", "Совет и сравнение", "relation"],
  ["#self-retrieval", "Сбор контекста", "self-retrieval"],
  ["#search", "Поиск", "search"],
  ["#diagnostics", "Диагностика", "diagnostics"],
  ["#memory", "Память", "memory"],
  ["#growth", "Развитие", "growth"],
] as const;

export function AccountControl(): ReactElement {
  return (
    <details className="account-menu">
      <summary aria-label="Меню аккаунта MikeMoore1337">
        <span>MikeMoore1337</span>
        <span className="account-menu-chevron" aria-hidden="true" />
      </summary>
      <div className="account-menu-popover">
        <span className="account-menu-caption">Владелец</span>
        <a href="/auth/logout">Выйти</a>
      </div>
    </details>
  );
}

export function PwaUpdateNotice(): ReactElement | null {
  const [available, setAvailable] = useState(hasPwaUpdate);
  const [updating, setUpdating] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => subscribeToPwaUpdate(() => setAvailable(true)), []);

  if (!available || dismissed) return null;

  const update = async () => {
    setUpdating(true);
    try {
      await applyPwaUpdate();
    } finally {
      setUpdating(false);
    }
  };

  return (
    <aside className="pwa-update-notice" role="status" aria-live="polite">
      <div>
        <strong>Доступна новая версия Second Brain.</strong>
        <span>Обнови её после завершения текущей работы.</span>
      </div>
      <div className="pwa-update-actions">
        <button type="button" onClick={() => void update()} disabled={updating}>
          {updating ? "Обновляем…" : "Обновить"}
        </button>
        <button type="button" className="pwa-update-later" onClick={() => setDismissed(true)}>
          Позже
        </button>
      </div>
    </aside>
  );
}

export function App(): ReactElement {
  const pathname = window.location.pathname;
  const authError = document.body.dataset.secondBrainAuthError;
  const authMode = document.body.dataset.secondBrainAuthMode;
  const showAccountControl = authMode === "github";
  const isAuthSurface = pathname === "/login" || pathname === "/auth/github/callback";
  useEffect(() => {
    if (isAuthSurface) return;
    let timer = 0;
    let previous: HTMLElement | null = null;
    const navigate = () => {
      window.clearTimeout(timer);
      previous?.classList.remove("context-enter");
      const target = revealDestination(window.location.hash);
      if (!target) return;
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
      target.classList.add("context-enter");
      previous = target;
      timer = window.setTimeout(() => target.classList.remove("context-enter"), 350);
    };
    const followLink = (event: MouseEvent) => {
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href^="#"]') : null;
      if (link) revealDestination(link.hash);
    };
    document.addEventListener("click", followLink, true);
    navigate();
    window.addEventListener("hashchange", navigate);
    return () => {
      document.removeEventListener("click", followLink, true);
      window.removeEventListener("hashchange", navigate);
      window.clearTimeout(timer);
      previous?.classList.remove("context-enter");
    };
  }, [isAuthSurface]);
  if (isAuthSurface) {
    const error: LoginError | undefined = authError === "oauth" || authError === "denied" ? authError : undefined;
    return <LoginScreen error={error} />;
  }
  return (
    <MotionConfig reducedMotion="user"><PageMotion>
      <PwaUpdateNotice />
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        const target = document.getElementById("main-content");
        target?.focus();
        target?.scrollIntoView?.({ block: "start" });
      }}>К содержанию</a>
      <div className="page-shell" data-shell>
        <header className="topbar">
          <a className="brand" href="#main-content" aria-label="Second Brain — начало">
            <img className="brand-brain" src={brainMark} srcSet={`${brainMark} 1x, ${brainMark2x} 2x`} width={44} height={44} alt="" />
            <span className="brand-copy"><span className="brand-eyebrow">Личная система знаний</span><span className="brand-name">Second Brain</span></span>
          </a><div className="topbar-actions"><nav className="quick-nav" aria-label="Быстрые действия"><a href="#capture"><Icon name="add" size={18} />Добавить</a><a href="#search"><Icon name="search" size={18} />Поиск</a><SectionMenu navigation={[["#capture", "Добавить материал", "add"], ...navigation]} /></nav>{showAccountControl && <AccountControl />}</div>
        </header>
        <div className="workspace-frame">
          <div className="workspace-content">
            <main id="main-content" tabIndex={-1}>
              <CinematicHero />
              <CaptureSurface />
              <SearchSurface />
              <div className="tools-heading"><h2>Твоё пространство</h2><p>Открой нужный раздел — остальное подождёт.</p></div>
              <div className="fold-list">
                <FoldSection id="decision-journal" title="Журнал решений" icon="decision"><DecisionJournalSurface /></FoldSection>
                <FoldSection id="timeline" title="Хронология" icon="timeline"><TimelineSurface /></FoldSection>
                <FoldSection id="self-model" title="Модель себя" icon="self-model"><SelfModelSurface /></FoldSection>
                <FoldSection id="simulate-me" title="Прогноз" icon="simulate"><SimulateMeSurface /></FoldSection>
                <FoldSection id="assistant-compare" title="Совет и сравнение" icon="relation"><AssistantCompareSurface /></FoldSection>
                <FoldSection id="self-retrieval" title="Сбор контекста" icon="self-retrieval"><SelfRetrievalSurface /></FoldSection>
                <FoldSection id="diagnostics" title="Диагностика" icon="diagnostics"><DiagnosticsSurface /></FoldSection>
              </div>
              <section className="pillars" aria-labelledby="pillars-title"><div className="section-heading"><p className="eyebrow">Две центральные части</p><h2 id="pillars-title">Система, которая растёт вместе с тобой.</h2></div><div className="pillar-grid"><article className="pillar-card pillar-card-memory" id="memory"><img className="pillar-backdrop" src={memoryBackdrop} width={192} height={192} alt="" aria-hidden="true" loading="lazy" decoding="async" /><div className="card-topline"><span className="card-index">01</span><span className="card-dot" aria-hidden="true" /></div><Icon name="memory" size={64} className="section-art" /><h3>Память</h3><p>Собирай идеи, источники и наблюдения в надёжную личную память.</p><div className="card-meta"><span>База знаний</span><Icon name="open" size={18} /></div></article><article className="pillar-card pillar-card-growth" id="growth"><img className="pillar-backdrop" src={growthBackdrop} width={192} height={192} alt="" aria-hidden="true" loading="lazy" decoding="async" /><div className="card-topline"><span className="card-index">02</span><span className="card-dot" aria-hidden="true" /></div><Icon name="growth" size={64} className="section-art" /><h3>Развитие</h3><p>Превращай накопленное знание в ясность, навыки и следующий шаг.</p><div className="card-meta"><span>Личное развитие</span><Icon name="open" size={18} /></div></article></div></section>
            </main>
            <footer className="footer"><span>Локально по умолчанию</span><span className="footer-line" aria-hidden="true" /><span>Приватные знания, осознанное развитие</span></footer>
          </div>

        </div>
      </div>
    </PageMotion></MotionConfig>
  );
}
