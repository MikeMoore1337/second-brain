import { type ReactNode } from "react";
import { Icon, type IconName } from "./icons";

/** Open ancestors before anchor scrolling/focus, including repeated same-hash links. */
export function revealDestination(hash: string): HTMLElement | null {
  const target = document.getElementById(hash.slice(1));
  if (!target) return null;
  let parent: HTMLElement | null = target.parentElement;
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
    parent = parent.parentElement;
  }
  return target;
}

/** Native disclosure keeps mounted editors and pending results intact when closed. */
export function FoldSection({ id, title, icon, children }: { id: string; title: string; icon: IconName; children: ReactNode }) {
  return <details className="fold-section" id={`${id}-panel`}>
    <summary><Icon name={icon} size={48} /><h2>{title}</h2><Icon name="expand" size={20} className="fold-chevron" /></summary>
    <div className="fold-body">{children}</div>
  </details>;
}
