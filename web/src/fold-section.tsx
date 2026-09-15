import { type ReactNode } from "react";
import { Icon, type IconName } from "./icons";

export const SEMANTIC_GROUP_REVEAL_EVENT = "second-brain:reveal-semantic-group";
export const SEMANTIC_TOOL_REVEAL_EVENT = "second-brain:reveal-semantic-tool";

function revealSemanticGroup(target: HTMLElement): void {
  const group = target.closest<HTMLElement>("[data-semantic-group]");
  const groupId = group?.dataset.semanticGroup;
  if (!group || !groupId) return;

  document.querySelectorAll<HTMLElement>("[data-semantic-group]").forEach((candidate) => {
    const open = candidate === group;
    candidate.classList.toggle("is-open", open);
    const panel = candidate.querySelector<HTMLElement>("[data-semantic-group-panel]");
    panel?.setAttribute("aria-hidden", String(!open));
    if (open) panel?.removeAttribute("inert");
    else panel?.setAttribute("inert", "");
    candidate.querySelector<HTMLButtonElement>("[data-semantic-group-trigger]")?.setAttribute("aria-expanded", String(open));
  });
  document.dispatchEvent(new CustomEvent(SEMANTIC_GROUP_REVEAL_EVENT, { detail: { groupId } }));
}

/** Open ancestors before anchor scrolling/focus, including repeated same-hash links. */
export function revealDestination(hash: string): HTMLElement | null {
  const target = document.getElementById(hash.slice(1));
  if (!target) return null;
  revealSemanticGroup(target);
  document.dispatchEvent(new CustomEvent(SEMANTIC_TOOL_REVEAL_EVENT, { detail: { targetId: target.id } }));
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
    <summary aria-controls={`${id}-content`}><Icon name={icon} size={48} /><h2>{title}</h2><Icon name="expand" size={20} className="fold-chevron" /></summary>
    <div className="fold-body" id={`${id}-content`}>{children}</div>
  </details>;
}
