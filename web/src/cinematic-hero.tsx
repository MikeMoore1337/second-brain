import { useEffect, useRef, useState, type ReactElement } from "react";
import { Icon } from "./icons";

/** The universe is decorative, never a projection of private notes or a graph. */
export function CinematicHero(): ReactElement {
  const root = useRef<HTMLElement>(null);
  const scene = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);
  const [active, setActive] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    const update = () => {
      setReducedMotion(reduced.matches);
      setActive(visible && !document.hidden && !reduced.matches && !paused);
    };
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    }, { threshold: 0.1 });
    observer?.observe(element);
    reduced.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    update();
    return () => {
      observer?.disconnect();
      reduced.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, [paused]);

  useEffect(() => {
    const element = root.current;
    const layer = scene.current;
    if (!element || !layer || !active || !window.matchMedia("(hover: hover) and (pointer: fine) and (min-width: 1024px)").matches) return;
    const move = (event: PointerEvent) => {
      const rect = element.getBoundingClientRect();
      const x = Math.max(-6, Math.min(6, (event.clientX - rect.left - rect.width / 2) / rect.width * 12));
      const y = Math.max(-4, Math.min(4, (event.clientY - rect.top - rect.height / 2) / rect.height * 8));
      layer.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    };
    const reset = () => { layer.style.transform = ""; };
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerleave", reset);
    return () => {
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerleave", reset);
      reset();
    };
  }, [active]);

  return <section className="cinematic-hero" ref={root} aria-labelledby="hero-title" data-moving={active}>
    <div className="universe-parallax" ref={scene} aria-hidden="true"><div className="universe-image" /><div className="universe-light" /></div>
    <div className="universe-copy">
      <p className="eyebrow">Место для твоих мыслей</p>
      <h1 id="hero-title">Second<br />Brain</h1>
      <p className="universe-thesis">Сохраняй. Соединяй.<br className="mobile-only" /> Понимай.</p>
      <p className="universe-description">Идеи, опыт и вдохновение —<br />в одной личной системе знаний.</p>
      <div className="universe-actions"><a className="universe-primary" href="#capture">Добавить мысль <Icon name="add" size={19} /></a><a className="universe-search" href="#search"><Icon name="search" size={19} /> Найти в памяти</a></div>
      <p className="universe-promise">Твои знания. Твой выбор.<br />Сохранение — только после проверки.</p>
    </div>
    <div className="universe-caption"><span>Из мыслей — в ясность.</span>{reducedMotion ? <span className="motion-preference">Движение отключено настройкой устройства</span> : <button type="button" className="scene-toggle" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Включить движение" : "Остановить движение"}</button>}</div>
  </section>;
}
