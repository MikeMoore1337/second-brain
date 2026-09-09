import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { revealDestination } from "./fold-section";
import { Icon, type IconName } from "./icons";
import { usePageMotion } from "./page-motion";

export function SectionMenu({ navigation }: { navigation: readonly (readonly [string, string, IconName])[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const { paused, setPaused } = usePageMotion();
  const reduced = useReducedMotion();
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <div className="section-menu" ref={root}>
    <button ref={trigger} type="button" className="sections-trigger" aria-expanded={open} aria-controls="section-dropdown" onClick={() => setOpen(!open)}><Icon name={open ? "collapse" : "expand"} size={18} />Разделы</button>
    {open ? <div id="section-dropdown" className="section-dropdown">
      <div className="dropdown-heading"><span>Твоё пространство</span><button type="button" aria-label="Закрыть список разделов" onClick={() => { setOpen(false); trigger.current?.focus(); }}><Icon name="close" size={18} /></button></div>
      <nav aria-label="Все разделы">{navigation.map(([href, label, icon]) => <a href={href} key={href} onClick={() => {
        setOpen(false);
        const target = revealDestination(href);
        if (target) { target.tabIndex = -1; target.focus({ preventScroll: true }); }
      }}><Icon name={icon} size={24} /><span>{label}</span></a>)}</nav>
      {reduced ? <p className="dropdown-motion">Движение отключено настройкой устройства</p> : <label className="dropdown-motion motion-setting"><span>Анимация</span><input type="checkbox" role="switch" checked={!paused} onChange={event => setPaused(!event.target.checked)} /><span className="motion-switch" aria-hidden="true" /></label>}
    </div> : null}
  </div>;
}
