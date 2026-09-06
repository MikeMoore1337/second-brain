import type { ReactElement, SVGProps } from "react";

/**
 * Local Lucide-derived semantic subset. The paths are checked into the bundle
 * so the UI never needs an icon font, CDN, or runtime icon loader.
 */
export const ICON_NAMES = [
  "add",
  "capture",
  "url",
  "text",
  "voice",
  "memory",
  "decision",
  "outcome",
  "timeline",
  "search",
  "open",
  "self-model",
  "self-retrieval",
  "diagnostics",
  "simulate",
  "growth",
  "refresh",
  "save",
  "confirm",
  "cancel",
  "close",
  "expand",
  "collapse",
  "copy",
  "warning",
  "error",
  "success",
  "info",
  "time",
  "relation",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

const ICON_PATHS: Record<IconName, ReactElement> = {
  add: <><path d="M5 12h14" /><path d="M12 5v14" /></>,
  capture: <><path d="M4 4h16v16H4z" /><path d="M4 13h4l2 3h4l2-3h4" /></>,
  url: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.71 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
  text: <><path d="M4 7V4h16v3" /><path d="M9 20h6" /><path d="M12 4v16" /></>,
  voice: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><path d="M12 19v3" /><path d="M8 22h8" /></>,
  memory: <><path d="M12 7v14" /><path d="M3 18a4 4 0 0 1 4-4h5" /><path d="M21 18a4 4 0 0 0-4-4h-5" /><path d="M3 5a4 4 0 0 1 4-4h5v17H7a4 4 0 0 0-4 4z" /><path d="M21 5a4 4 0 0 0-4-4h-5v17h5a4 4 0 0 1 4 4z" /></>,
  decision: <><circle cx="6" cy="3" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M6 5v10a3 3 0 0 0 3 3h7" /><path d="M8 3h8a2 2 0 0 1 2 2v1" /></>,
  outcome: <><circle cx="12" cy="12" r="10" /><path d="m8 12 2.5 2.5L16 9" /></>,
  timeline: <><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
  open: <><path d="M7 7h10v10" /><path d="M7 17 17 7" /></>,
  "self-model": <><circle cx="12" cy="7" r="4" /><path d="M5 21a7 7 0 0 1 14 0" /></>,
  "self-retrieval": <><path d="M4 4h6" /><path d="M4 8h4" /><path d="M4 12h5" /><circle cx="15" cy="15" r="4" /><path d="m18 18 3 3" /></>,
  diagnostics: <><path d="M4 2v6a4 4 0 0 0 8 0V2" /><path d="M8 2v4" /><path d="M4 2h8" /><path d="M12 13a5 5 0 0 0 10 0v-1" /><circle cx="19" cy="7" r="2" /></>,
  simulate: <><path d="m12 3-1.5 4.5L6 9l4.5 1.5L12 15l1.5-4.5L18 9l-4.5-1.5z" /><path d="m19 15-.75 2.25L16 18l2.25.75L19 21l.75-2.25L22 18l-2.25-.75z" /><path d="m5 3-.5 1.5L3 5l1.5.5L5 7l.5-1.5L7 5l-1.5-.5z" /></>,
  growth: <><path d="m3 17 6-6 4 4 8-8" /><path d="M15 7h6v6" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.5-4L4 9" /><path d="M4 4v5h5" /><path d="M4 13a8 8 0 0 0 14.5 4L20 15" /><path d="M20 20v-5h-5" /></>,
  save: <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" /><path d="M17 21v-8H7v8" /><path d="M7 3v5h8" /></>,
  confirm: <path d="m5 12 4 4L19 6" />,
  cancel: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
  close: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
  expand: <><path d="m6 9 6 6 6-6" /></>,
  collapse: <><path d="m18 15-6-6-6 6" /></>,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
  warning: <><path d="m12 3 10 18H2Z" /><path d="M12 9v4" /><path d="M12 17h.01" /></>,
  error: <><circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" /><path d="m9 9 6 6" /></>,
  success: <><circle cx="12" cy="12" r="10" /><path d="m8 12 2.5 2.5L16 9" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 10v6" /><path d="M12 7h.01" /></>,
  time: <><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>,
  relation: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.71 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
};

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "aria-hidden" | "role"> {
  name: IconName;
  size?: number;
  label?: string;
}

export function Icon({ name, size = 20, label, className, ...props }: IconProps): ReactElement {
  return (
    <svg
      {...props}
      className={className}
      data-icon={name}
      fill="none"
      focusable="false"
      height={size}
      role={label ? "img" : undefined}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.5"
      viewBox="0 0 24 24"
      width={size}
      {...(label ? { "aria-label": label } : { "aria-hidden": true })}
    >
      {label ? <title>{label}</title> : null}
      {ICON_PATHS[name]}
    </svg>
  );
}
