import { useEffect, useRef, useState, type ReactElement } from "react";
import { Icon } from "./icons";
import coreDepth from "./assets/core-depth.webp";
import horizon from "./assets/memory-horizon.webp";

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
    if (!element || !layer || !active) return;
    const desktop = window.matchMedia("(hover: hover) and (pointer: fine) and (min-width: 1024px)");
    let frame = 0;
    const planes = Array.from(layer.querySelectorAll<HTMLElement>("[data-depth]"));
    const move = (event: PointerEvent) => {
      if (!desktop.matches) return;
      cancelAnimationFrame(frame);
      const rect = element.getBoundingClientRect();
      const x = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1));
      const y = Math.max(-1, Math.min(1, (event.clientY - rect.top) / rect.height * 2 - 1));
      frame = requestAnimationFrame(() => {
        for (const plane of planes) {
          const depth = Number(plane.dataset.depth);
          plane.style.transform = `translate3d(${x * depth}px, ${y * depth * .6}px, 0)`;
        }
      });
    };
    const reset = () => { cancelAnimationFrame(frame); for (const plane of planes) plane.style.transform = ""; };
    desktop.addEventListener("change", reset);
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerleave", reset);
    return () => {
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerleave", reset);
      desktop.removeEventListener("change", reset);
      reset();
    };
  }, [active]);

  return <section className="cinematic-hero" ref={root} aria-labelledby="hero-title" data-moving={active}>
    <div className="universe-parallax" ref={scene} aria-hidden="true">
      <img className="memory-horizon" src={horizon} alt="" width="1536" height="768" />
      <div className="universe-light" />
      <div className="memory-stage">
        <div className="stage-haze" data-depth="5"><div className="haze-breath" /></div>
        <div className="stage-orbits" data-depth="9">
          {["far", "middle", "near"].map((plane) => <div className={`orbit-plane orbit-plane-${plane}`} key={plane}>
            <svg className="orbit-track" viewBox="0 0 600 600" fill="none"><circle cx="300" cy="300" r="286" stroke="currentColor" strokeWidth="1.2" /><circle cx="300" cy="300" r="280" stroke="currentColor" strokeWidth=".35" /></svg>
            <div className="orbit-traveller"><span /></div>
          </div>)}
        </div>
        <div className="stage-core" data-depth="6"><div className="core-assembly"><img className="core-depth" src={coreDepth} alt="" width="768" height="768" /><div className="core-corona" /></div></div>
        <div className="stage-fragments" data-depth="16">
          <div className="thought-fragment fragment-one"><span className="fragment-label">Наблюдение</span><span className="fragment-thought">Всё начинается<br />с мысли.</span><span className="fragment-rule" /></div>
          <div className="thought-fragment fragment-two"><Icon name="relation" size={22} /><span>Найти связь</span><span className="fragment-rule" /></div>
          <div className="thought-fragment fragment-three"><span className="fragment-label">Идея</span><span className="fragment-thought">Увидеть<br />по-новому.</span></div>
          <div className="thought-fragment fragment-four"><Icon name="memory" size={20} /><span className="fragment-rule" /><span className="fragment-rule" /></div>
        </div>
        <div className="stage-particles" data-depth="20">{Array.from({length: 12}, (_, i) => <i key={i} className={`light-particle particle-${i}`} />)}</div>
        <div className="pointer-light" data-depth="28" />
      </div>
    </div>
    <div className="universe-copy">
      <p className="eyebrow">Место для твоих мыслей</p>
      <h1 id="hero-title">Second<br /><span>Brain</span></h1>
      <p className="universe-thesis">Мысли обретают форму.</p>
      <p className="universe-description">Сохраняй важное. Находи связи.<br />Возвращайся к тому, что вдохновляет.</p>
      <div className="universe-actions"><a className="universe-primary" href="#capture">Добавить мысль <Icon name="add" size={19} /></a><a className="universe-search" href="#search"><Icon name="search" size={19} /> Найти в памяти</a></div>
      <p className="universe-promise">Твои знания. Твой выбор.<br />Сохранение — только после проверки.</p>
    </div>
    <div className="universe-caption"><span>Больше связей. Больше ясности.</span>{reducedMotion ? <span className="motion-preference">Движение отключено настройкой устройства</span> : <button type="button" className="scene-toggle" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Включить движение" : "Остановить движение"}</button>}</div>
  </section>;
}
