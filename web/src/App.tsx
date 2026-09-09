import { MotionConfig } from "motion/react";
import { useEffect, type ReactElement } from "react";
import { CinematicHero } from "./cinematic-hero";

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
import { Icon, type IconName } from "./icons";

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
      const target = document.getElementById(window.location.hash.slice(1));
      if (!target) return;
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
      target.classList.add("context-enter");
      previous = target;
      timer = window.setTimeout(() => target.classList.remove("context-enter"), 350);
    };
    window.addEventListener("hashchange", navigate);
    return () => {
      window.removeEventListener("hashchange", navigate);
      window.clearTimeout(timer);
      previous?.classList.remove("context-enter");
    };
  }, []);
  return (
    <MotionConfig reducedMotion="user">
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        const target = document.getElementById("main-content");
        target?.focus();
        target?.scrollIntoView?.({ block: "start" });
      }}>К содержанию</a>
      <div className="page-shell" data-shell>
        <header className="topbar">
          <a className="brand" href="#main-content" aria-label="Second Brain — начало">
            <Icon name="memory" size={26} />
            <span className="brand-copy"><span className="brand-eyebrow">Личная система знаний</span><span className="brand-name">Second Brain</span></span>
          </a><nav className="quick-nav" aria-label="Быстрые действия"><a href="#capture"><Icon name="add" size={18} />Добавить</a><a href="#search"><Icon name="search" size={18} />Поиск</a><a href="#workspace-navigation"><Icon name="expand" size={18} />Разделы</a></nav>
        </header>
        <div className="workspace-frame">
          <div className="workspace-content">
            <main id="main-content" tabIndex={-1}>
              <CinematicHero />
              <CaptureSurface />
              <DecisionJournalSurface />
              <TimelineSurface />
              <SelfModelSurface />
              <SimulateMeSurface />
              <AssistantCompareSurface />
              <SelfRetrievalSurface />
              <SearchSurface />
              <DiagnosticsSurface />
              <section className="pillars" aria-labelledby="pillars-title"><div className="section-heading"><p className="eyebrow">Две центральные части</p><h2 id="pillars-title">Система, которая растёт вместе с тобой.</h2></div><div className="pillar-grid"><article className="pillar-card pillar-card-memory" id="memory"><div className="card-topline"><span className="card-index">01</span><span className="card-dot" aria-hidden="true" /></div><Icon name="memory" size={64} className="section-art" /><h3>Память</h3><p>Собирай идеи, источники и наблюдения в надёжную личную память.</p><div className="card-meta"><span>База знаний</span><Icon name="open" size={18} /></div></article><article className="pillar-card pillar-card-growth" id="growth"><div className="card-topline"><span className="card-index">02</span><span className="card-dot" aria-hidden="true" /></div><Icon name="growth" size={64} className="section-art" /><h3>Развитие</h3><p>Превращай накопленное знание в ясность, навыки и следующий шаг.</p><div className="card-meta"><span>Личное развитие</span><Icon name="open" size={18} /></div></article></div></section>
            </main>
            <footer className="footer"><span>Локально по умолчанию</span><span className="footer-line" aria-hidden="true" /><span>Приватные знания, осознанное развитие</span></footer>
          </div>
          <aside id="workspace-navigation" className="workspace-rail" aria-label="Навигация рабочего пространства">
            <div className="rail-intro"><span className="rail-kicker">Твоё пространство</span><span className="rail-title">Мысли становятся знанием</span></div>
            <nav className="topnav" aria-label="Основные разделы"><>{navigation.map(([href, label, icon]) => <a href={href} key={href}><Icon name={icon as IconName} size={18} /><span>{label}</span></a>)}</></nav>
            <span className="status-pill"><span aria-hidden="true" /> Локальная основа</span>
          </aside>
        </div>
      </div>
    </MotionConfig>
  );
}
