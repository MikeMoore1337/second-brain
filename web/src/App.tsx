import { MotionConfig } from "motion/react";
import { type ReactElement } from "react";

import {
  CaptureSurface,
  DecisionJournalSurface,
  SelfModelSurface,
  SelfRetrievalSurface,
  SearchSurface,
  SimulateMeSurface,
  TimelineSurface,
} from "./parity";
import { Icon, type IconName } from "./icons";

const navigation = [
  ["#decision-journal", "Decision Journal", "decision"],
  ["#timeline", "Timeline", "timeline"],
  ["#self-model", "Self Model", "self-model"],
  ["#simulate-me", "Simulate Me", "simulate"],
  ["#self-retrieval", "Self Retrieval", "self-retrieval"],
  ["#search", "Search", "search"],
  ["#memory", "Memory", "memory"],
  ["#growth", "Growth", "growth"],
] as const;

export function App(): ReactElement {
  return (
    <MotionConfig reducedMotion="user">
      <a className="skip-link" href="#main-content">К содержанию</a>
      <div className="page-shell" data-shell>
        <header className="topbar">
          <a className="brand" href="#main-content" aria-label="Second Brain — начало">
            <span className="brand-mark" aria-hidden="true">SB</span>
            <span className="brand-copy"><span className="brand-eyebrow">Personal knowledge system</span><span className="brand-name">Second Brain</span></span>
          </a>
        </header>
        <div className="workspace-frame">
          <aside className="workspace-rail" aria-label="Навигация рабочего пространства">
            <div className="rail-intro"><span className="rail-kicker">Workspace map</span><span className="rail-title">Signal field</span></div>
            <nav className="topnav" aria-label="Основные разделы"><>{navigation.map(([href, label, icon]) => <a href={href} key={href}><Icon name={icon as IconName} size={18} /><span>{label}</span></a>)}</></nav>
            <span className="status-pill"><span aria-hidden="true" /> Local foundation</span>
          </aside>
          <div className="workspace-content">
            <main id="main-content">
              <section className="hero" aria-labelledby="hero-title">
                <div className="hero-copy"><p className="eyebrow">Memory <span aria-hidden="true">+</span> Growth</p><h1 id="hero-title">Сохраняй идеи.<br /><em>Развивай<br className="hero-mobile-break" /> понимание.</em></h1><p className="hero-lede">Локальная точка входа в личную систему знаний — спокойное место, где память становится опорой для роста.</p></div>
                <div className="hero-orbit" aria-hidden="true"><div className="orbit orbit-outer" /><div className="orbit orbit-inner" /><div className="orbit-core"><span>SB</span></div><span className="orbit-label orbit-label-top">remember</span><span className="orbit-label orbit-label-bottom">grow</span></div>
              </section>
              <CaptureSurface />
              <DecisionJournalSurface />
              <TimelineSurface />
              <SelfModelSurface />
              <SimulateMeSurface />
              <SelfRetrievalSurface />
              <SearchSurface />
              <section className="pillars" aria-labelledby="pillars-title"><div className="section-heading"><p className="eyebrow">Две центральные части</p><h2 id="pillars-title">Система, которая растёт вместе с тобой.</h2></div><div className="pillar-grid"><article className="pillar-card pillar-card-memory" id="memory"><div className="card-topline"><span className="card-index">01</span><span className="card-dot" aria-hidden="true" /></div><h3>Memory</h3><p>Собирай идеи, источники и наблюдения в надёжную личную память.</p><div className="card-meta"><span>Knowledge base</span><Icon name="open" size={18} /></div></article><article className="pillar-card pillar-card-growth" id="growth"><div className="card-topline"><span className="card-index">02</span><span className="card-dot" aria-hidden="true" /></div><h3>Growth</h3><p>Превращай накопленное знание в ясность, навыки и следующий шаг.</p><div className="card-meta"><span>Personal development</span><Icon name="open" size={18} /></div></article></div></section>
            </main>
            <footer className="footer"><span>Local by default</span><span className="footer-line" aria-hidden="true" /><span>Private knowledge, deliberate growth</span></footer>
          </div>
        </div>
      </div>
    </MotionConfig>
  );
}
