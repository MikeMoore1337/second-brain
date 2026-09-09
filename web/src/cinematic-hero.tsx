import { useEffect, useRef, useState, type ReactElement } from "react";
import { Icon } from "./icons";
import brainSmall from "./assets/memory-brain-480.webp";
import brainLarge from "./assets/memory-brain-800.webp";

const thoughtConnections = [
  "M105 175 C145 170 164 220 230 250",
  "M500 275 C464 300 452 321 395 335",
  "M175 495 C190 440 228 417 270 385",
  "M445 76 C420 138 376 158 345 202",
];

/** Decorative memory metaphor, never private notes or a working knowledge graph. */
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
      <div className="memory-depth" />
      <div className="universe-light" />
      <div className="memory-stage">
        <div className="stage-haze" data-depth="5"><div className="haze-breath" /></div>
        <div className="stage-connections" data-depth="9">
          <svg viewBox="0 0 600 600" fill="none" className="thought-connections">
            {thoughtConnections.map((d, index) => <g key={d} className={`thought-link thought-link-${index}`}><path className="connection-thread" d={d} /><path className="connection-pulse" d={d} pathLength="100" /></g>)}
          </svg>
        </div>
        <div className="stage-brain" data-depth="6"><div className="brain-assembly"><img className="memory-brain" src={brainLarge} srcSet={`${brainSmall} 480w, ${brainLarge} 800w`} sizes="(max-width: 600px) 330px, 540px" alt="" width="800" height="800" /><div className="brain-region brain-region-front" /><div className="brain-region brain-region-middle" /><div className="brain-region brain-region-back" /></div></div>
        <div className="stage-fragments" data-depth="16">
          <div className="thought-fragment fragment-one"><span className="fragment-label">Наблюдение</span><span className="fragment-thought">Всё начинается<br />с мысли.</span><span className="fragment-rule" /></div>
          <div className="thought-fragment fragment-two"><Icon name="relation" size={22} /><span>Связь с идеей</span><span className="fragment-rule" /></div>
          <div className="thought-fragment fragment-three"><span className="fragment-label">К важному</span><span className="fragment-thought">Вернуться.<br />Осмыслить.</span></div>
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
      <p className="universe-description">Сохраняй мысли. Находи связи.<br />Возвращайся к важному.</p>
      <div className="universe-actions"><a className="universe-primary" href="#capture">Добавить мысль <Icon name="add" size={19} /></a><a className="universe-search" href="#search"><Icon name="search" size={19} /> Найти в памяти</a></div>
      <p className="universe-promise">Твои знания. Твой выбор.<br />Сохранение — только после проверки.</p>
    </div>
    <div className="universe-caption"><span>Больше связей. Больше ясности.</span>{reducedMotion ? <span className="motion-preference">Движение отключено настройкой устройства</span> : <button type="button" className="scene-toggle" aria-pressed={paused} onClick={() => setPaused(!paused)}><Icon name={paused ? "play" : "pause"} size={18} />{paused ? "Включить движение" : "Остановить движение"}</button>}</div>
  </section>;
}
