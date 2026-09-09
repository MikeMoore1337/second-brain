import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./icons";
import decisionArt from "./assets/decision-chapter.webp";
import selfArt from "./assets/self-model-chapter.webp";
import searchArt from "./assets/search-chapter.webp";

const chapterArt = { decision: decisionArt, "self-model": selfArt, search: searchArt };

type MotionState = { paused: boolean; setPaused: (paused: boolean) => void };
const PageMotionContext = createContext<MotionState | null>(null);

export function usePageMotion(): MotionState {
  const context = useContext(PageMotionContext);
  const [paused, setPaused] = useState(false);
  return context ?? { paused, setPaused };
}

export function PageMotion({ children }: { children: ReactNode }) {
  const [paused, setPaused] = useState(false);
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setAvailable(!document.hidden && !media.matches);
    update(); media.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    const blocks = document.querySelectorAll("main > section, .chapter-interlude, .workspace-rail");
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(entries => {
      for (const entry of entries) {
        const element = entry.target as HTMLElement;
        element.dataset.inView = String(entry.isIntersecting);
        if (entry.isIntersecting) element.dataset.revealed = "true";
      }
    }, { threshold: 0.05 });
    blocks.forEach(block => observer?.observe(block));
    return () => { observer?.disconnect(); media.removeEventListener("change", update); document.removeEventListener("visibilitychange", update); };
  }, []);
  return <PageMotionContext.Provider value={{ paused, setPaused }}><div className="motion-world" data-page-motion={available && !paused}>
    <div className="page-atmosphere" aria-hidden="true"><i className="page-light page-light-left" /><i className="page-light page-light-right" /><div className="page-filament" /></div>
    {children}
  </div></PageMotionContext.Provider>;
}

export function ChapterInterlude({ number, title, text, icon, secondary }: { number: string; title: string; text: string; icon: keyof typeof chapterArt; secondary: IconName }) {
  return <div className="chapter-interlude">
    <div className="chapter-copy"><p className="eyebrow">{number} · Пространство памяти</p><h2>{title}</h2><p>{text}</p></div>
    <div className="chapter-scene" aria-hidden="true">
      <div className="chapter-aura" />
      <svg viewBox="0 0 440 320" fill="none"><path className="chapter-thread" d="M25 240C160 280 100 50 240 120S350 220 425 80" /><path className="chapter-signal" pathLength="100" d="M25 240C160 280 100 50 240 120S350 220 425 80" /></svg>
      <div className="chapter-symbol"><img src={chapterArt[icon]} width={176} height={176} alt="" loading="lazy" decoding="async" /></div>
      <div className="chapter-satellite"><Icon name={secondary} size={64} /></div>
      <div className="chapter-fragment"><span /><span /><span /></div>
    </div>
  </div>;
}
