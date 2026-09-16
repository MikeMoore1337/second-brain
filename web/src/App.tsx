import { MotionConfig } from "motion/react";
import { useEffect, useState, type ReactElement } from "react";
import { CinematicHero } from "./cinematic-hero";
import { LoginScreen, type LoginError } from "./login-screen";
import { PageMotion } from "./page-motion";
import { revealDestination } from "./fold-section";
import { SectionMenu } from "./section-menu";
import brainMark from "./assets/brain-mark-48.webp";
import brainMark2x from "./assets/brain-mark-96.webp";

import { AssistantCompareSurface } from "./assistant-compare-surface";
import { RetrospectiveCalibrationSurface } from "./retrospective-calibration-surface";
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
import { ProspectiveAuditSurface } from "./prospective-audit-surface";
import { CognitiveTwinSurface } from "./cognitive-twin-surface";
import { DecisionCompassSurface } from "./decision-compass-surface";
import { GrowthSurface } from "./growth-surface";
import { PersonalStrategySurface } from "./personal-strategy-surface";
import { PersonalPlanningSurface } from "./personal-planning-surface";
import { PersonalExperimentsSurface } from "./personal-experiments-surface";
import { AdaptiveCognitiveTwinSurface } from "./adaptive-cognitive-twin-surface";
import { SemanticNavigation, semanticGroups, type SemanticTool } from "./semantic-navigation";

function renderSemanticTool(tool: SemanticTool): ReactElement | null {
  switch (tool.id) {
    case "capture": return <CaptureSurface />;
    case "search": return <SearchSurface />;
    case "decision-journal": return <DecisionJournalSurface />;
    case "timeline": return <TimelineSurface />;
    case "self-retrieval": return <SelfRetrievalSurface />;
    case "self-model": return <SelfModelSurface />;
    case "cognitive-twin": return <CognitiveTwinSurface includeGrowth={false} />;
    case "retrospective-calibration": return <RetrospectiveCalibrationSurface />;
    case "diagnostics": return <DiagnosticsSurface />;
    case "simulate-me": return <SimulateMeSurface />;
    case "prospective-audit": return <ProspectiveAuditSurface />;
    case "assistant-compare": return <AssistantCompareSurface />;
    case "decision-compass": return <DecisionCompassSurface />;
    case "growth-engine": return <GrowthSurface />;
    case "personal-strategy": return <PersonalStrategySurface />;
    case "personal-planning": return <PersonalPlanningSurface />;
    case "personal-experiments": return <PersonalExperimentsSurface />;
    case "adaptive-cognitive-twin": return <AdaptiveCognitiveTwinSurface />;
    default: return tool satisfies never;
  }
}

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
          </a><div className="topbar-actions"><nav className="quick-nav" aria-label="Навигация по направлениям"><SectionMenu groups={semanticGroups} /></nav>{showAccountControl && <AccountControl />}</div>
        </header>
        <div className="workspace-frame">
          <div className="workspace-content">
            <main id="main-content" tabIndex={-1}>
              <CinematicHero />
              <SemanticNavigation renderTool={renderSemanticTool} />
            </main>
            <footer className="footer"><span>Локально по умолчанию</span><span className="footer-line" aria-hidden="true" /><span>Приватные знания, осознанное развитие</span></footer>
          </div>

        </div>
      </div>
    </PageMotion></MotionConfig>
  );
}
