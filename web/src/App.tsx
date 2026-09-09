import { MotionConfig } from "motion/react";
import { type ReactElement } from "react";

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
            <span className="brand-mark" aria-hidden="true">SB</span>
            <span className="brand-copy"><span className="brand-eyebrow">Личная система знаний</span><span className="brand-name">Second Brain</span></span>
          </a>
        </header>
        <div className="workspace-frame">
          <aside className="workspace-rail" aria-label="Навигация рабочего пространства">
            <div className="rail-intro"><span className="rail-kicker">Карта рабочего пространства</span><span className="rail-title">Сигнальное поле</span></div>
            <nav className="topnav" aria-label="Основные разделы"><>{navigation.map(([href, label, icon]) => <a href={href} key={href}><Icon name={icon as IconName} size={18} /><span>{label}</span></a>)}</></nav>
            <span className="status-pill"><span aria-hidden="true" /> Локальная основа</span>
          </aside>
          <div className="workspace-content">
            <main id="main-content" tabIndex={-1}>
              <section className="hero" aria-labelledby="hero-title">
                <div className="hero-copy"><p className="eyebrow">Память <span aria-hidden="true">+</span> развитие</p><h1 id="hero-title">Сохраняй идеи.<br /><em>Развивай<br className="hero-mobile-break" /> понимание.</em></h1><p className="hero-lede">Локальная точка входа в личную систему знаний — спокойное место, где память становится опорой для роста.</p></div>
                <div className="hero-orbit" aria-hidden="true"><div className="orbit orbit-outer" /><div className="orbit orbit-inner" /><div className="orbit-core"><span>SB</span></div><span className="orbit-label orbit-label-top">помни</span><span className="orbit-label orbit-label-bottom">развивай</span></div>
              </section>
              <CaptureSurface />
              <DecisionJournalSurface />
              <TimelineSurface />
              <SelfModelSurface />
              <SimulateMeSurface />
              <AssistantCompareSurface />
              <SelfRetrievalSurface />
              <SearchSurface />
              <DiagnosticsSurface />
              <section className="pillars" aria-labelledby="pillars-title"><div className="section-heading"><p className="eyebrow">Две центральные части</p><h2 id="pillars-title">Система, которая растёт вместе с тобой.</h2></div><div className="pillar-grid"><article className="pillar-card pillar-card-memory" id="memory"><div className="card-topline"><span className="card-index">01</span><span className="card-dot" aria-hidden="true" /></div><h3>Память</h3><p>Собирай идеи, источники и наблюдения в надёжную личную память.</p><div className="card-meta"><span>База знаний</span><Icon name="open" size={18} /></div></article><article className="pillar-card pillar-card-growth" id="growth"><div className="card-topline"><span className="card-index">02</span><span className="card-dot" aria-hidden="true" /></div><h3>Развитие</h3><p>Превращай накопленное знание в ясность, навыки и следующий шаг.</p><div className="card-meta"><span>Личное развитие</span><Icon name="open" size={18} /></div></article></div></section>
            </main>
            <footer className="footer"><span>Локально по умолчанию</span><span className="footer-line" aria-hidden="true" /><span>Приватные знания, осознанное развитие</span></footer>
          </div>
        </div>
      </div>
    </MotionConfig>
  );
}
