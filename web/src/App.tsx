import { MotionConfig } from "motion/react";
import { useEffect, type ReactElement } from "react";
import { CinematicHero } from "./cinematic-hero";
import { PageMotion } from "./page-motion";
import { FoldSection, revealDestination } from "./fold-section";
import { SectionMenu } from "./section-menu";
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

export function App(): ReactElement {
  useEffect(() => {
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
  }, []);
  return (
    <MotionConfig reducedMotion="user"><PageMotion>
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
          </a><nav className="quick-nav" aria-label="Быстрые действия"><a href="#capture"><Icon name="add" size={18} />Добавить</a><a href="#search"><Icon name="search" size={18} />Поиск</a><SectionMenu navigation={[["#capture", "Добавить материал", "add"], ...navigation]} /></nav>
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
              <section className="pillars" aria-labelledby="pillars-title"><div className="section-heading"><p className="eyebrow">Две центральные части</p><h2 id="pillars-title">Система, которая растёт вместе с тобой.</h2></div><div className="pillar-grid"><article className="pillar-card pillar-card-memory" id="memory"><div className="card-topline"><span className="card-index">01</span><span className="card-dot" aria-hidden="true" /></div><Icon name="memory" size={64} className="section-art" /><h3>Память</h3><p>Собирай идеи, источники и наблюдения в надёжную личную память.</p><div className="card-meta"><span>База знаний</span><Icon name="open" size={18} /></div></article><article className="pillar-card pillar-card-growth" id="growth"><div className="card-topline"><span className="card-index">02</span><span className="card-dot" aria-hidden="true" /></div><Icon name="growth" size={64} className="section-art" /><h3>Развитие</h3><p>Превращай накопленное знание в ясность, навыки и следующий шаг.</p><div className="card-meta"><span>Личное развитие</span><Icon name="open" size={18} /></div></article></div></section>
            </main>
            <footer className="footer"><span>Локально по умолчанию</span><span className="footer-line" aria-hidden="true" /><span>Приватные знания, осознанное развитие</span></footer>
          </div>

        </div>
      </div>
    </PageMotion></MotionConfig>
  );
}
