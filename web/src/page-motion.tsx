import { GlobalCosmosBackground } from "./global-cosmos";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
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
    const blocks = document.querySelectorAll("main > section, .fold-section");
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
    <GlobalCosmosBackground />
    {children}
  </div></PageMotionContext.Provider>;
}
