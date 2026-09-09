import type { ImgHTMLAttributes, ReactElement } from "react";

// Original local raster art: separate optical masters for controls and section headings.
import add48 from "./assets/icons/add-compact-48.webp";
import add96 from "./assets/icons/add-compact-96.webp";
import addDetail96 from "./assets/icons/add-detail-96.webp";
import addDetail192 from "./assets/icons/add-detail-192.webp";
import relation48 from "./assets/icons/relation-compact-48.webp";
import relation96 from "./assets/icons/relation-compact-96.webp";
import relationDetail96 from "./assets/icons/relation-detail-96.webp";
import relationDetail192 from "./assets/icons/relation-detail-192.webp";
import memory48 from "./assets/icons/memory-compact-48.webp";
import memory96 from "./assets/icons/memory-compact-96.webp";
import memoryDetail96 from "./assets/icons/memory-detail-96.webp";
import memoryDetail192 from "./assets/icons/memory-detail-192.webp";
import voice48 from "./assets/icons/voice-compact-48.webp";
import voice96 from "./assets/icons/voice-compact-96.webp";
import voiceDetail96 from "./assets/icons/voice-detail-96.webp";
import voiceDetail192 from "./assets/icons/voice-detail-192.webp";
import decision48 from "./assets/icons/decision-compact-48.webp";
import decision96 from "./assets/icons/decision-compact-96.webp";
import decisionDetail96 from "./assets/icons/decision-detail-96.webp";
import decisionDetail192 from "./assets/icons/decision-detail-192.webp";
import success48 from "./assets/icons/success-compact-48.webp";
import success96 from "./assets/icons/success-compact-96.webp";
import timeline48 from "./assets/icons/timeline-compact-48.webp";
import timeline96 from "./assets/icons/timeline-compact-96.webp";
import timelineDetail96 from "./assets/icons/timeline-detail-96.webp";
import timelineDetail192 from "./assets/icons/timeline-detail-192.webp";
import search48 from "./assets/icons/search-compact-48.webp";
import search96 from "./assets/icons/search-compact-96.webp";
import searchDetail96 from "./assets/icons/search-detail-96.webp";
import searchDetail192 from "./assets/icons/search-detail-192.webp";
import open48 from "./assets/icons/open-compact-48.webp";
import open96 from "./assets/icons/open-compact-96.webp";
import self_model48 from "./assets/icons/self-model-compact-48.webp";
import self_model96 from "./assets/icons/self-model-compact-96.webp";
import self_modelDetail96 from "./assets/icons/self-model-detail-96.webp";
import self_modelDetail192 from "./assets/icons/self-model-detail-192.webp";
import self_retrieval48 from "./assets/icons/self-retrieval-compact-48.webp";
import self_retrieval96 from "./assets/icons/self-retrieval-compact-96.webp";
import self_retrievalDetail96 from "./assets/icons/self-retrieval-detail-96.webp";
import self_retrievalDetail192 from "./assets/icons/self-retrieval-detail-192.webp";
import diagnostics48 from "./assets/icons/diagnostics-compact-48.webp";
import diagnostics96 from "./assets/icons/diagnostics-compact-96.webp";
import diagnosticsDetail96 from "./assets/icons/diagnostics-detail-96.webp";
import diagnosticsDetail192 from "./assets/icons/diagnostics-detail-192.webp";
import simulate48 from "./assets/icons/simulate-compact-48.webp";
import simulate96 from "./assets/icons/simulate-compact-96.webp";
import simulateDetail96 from "./assets/icons/simulate-detail-96.webp";
import simulateDetail192 from "./assets/icons/simulate-detail-192.webp";
import growth48 from "./assets/icons/growth-compact-48.webp";
import growth96 from "./assets/icons/growth-compact-96.webp";
import growthDetail96 from "./assets/icons/growth-detail-96.webp";
import growthDetail192 from "./assets/icons/growth-detail-192.webp";
import refresh48 from "./assets/icons/refresh-compact-48.webp";
import refresh96 from "./assets/icons/refresh-compact-96.webp";
import close48 from "./assets/icons/close-compact-48.webp";
import close96 from "./assets/icons/close-compact-96.webp";
import expand48 from "./assets/icons/expand-compact-48.webp";
import expand96 from "./assets/icons/expand-compact-96.webp";
import copy48 from "./assets/icons/copy-compact-48.webp";
import copy96 from "./assets/icons/copy-compact-96.webp";
import warning48 from "./assets/icons/warning-compact-48.webp";
import warning96 from "./assets/icons/warning-compact-96.webp";
import info48 from "./assets/icons/info-compact-48.webp";
import info96 from "./assets/icons/info-compact-96.webp";
import pause48 from "./assets/icons/pause-compact-48.webp";
import pause96 from "./assets/icons/pause-compact-96.webp";
import play48 from "./assets/icons/play-compact-48.webp";
import play96 from "./assets/icons/play-compact-96.webp";

export const ICON_NAMES = ["add","capture","url","text","voice","memory","decision","outcome","timeline","search","open","self-model","self-retrieval","diagnostics","simulate","growth","refresh","save","confirm","cancel","close","expand","collapse","copy","warning","error","success","info","time","relation","pause","play"] as const;
export type IconName = (typeof ICON_NAMES)[number];

type Artwork = { compact: string; compact2x: string; detail?: string; detail2x?: string };
const ARTWORK: Record<string, Artwork> = {
  "add": { compact: add48, compact2x: add96, detail: addDetail96, detail2x: addDetail192 },
  "relation": { compact: relation48, compact2x: relation96, detail: relationDetail96, detail2x: relationDetail192 },
  "memory": { compact: memory48, compact2x: memory96, detail: memoryDetail96, detail2x: memoryDetail192 },
  "voice": { compact: voice48, compact2x: voice96, detail: voiceDetail96, detail2x: voiceDetail192 },
  "decision": { compact: decision48, compact2x: decision96, detail: decisionDetail96, detail2x: decisionDetail192 },
  "success": { compact: success48, compact2x: success96 },
  "timeline": { compact: timeline48, compact2x: timeline96, detail: timelineDetail96, detail2x: timelineDetail192 },
  "search": { compact: search48, compact2x: search96, detail: searchDetail96, detail2x: searchDetail192 },
  "open": { compact: open48, compact2x: open96 },
  "self-model": { compact: self_model48, compact2x: self_model96, detail: self_modelDetail96, detail2x: self_modelDetail192 },
  "self-retrieval": { compact: self_retrieval48, compact2x: self_retrieval96, detail: self_retrievalDetail96, detail2x: self_retrievalDetail192 },
  "diagnostics": { compact: diagnostics48, compact2x: diagnostics96, detail: diagnosticsDetail96, detail2x: diagnosticsDetail192 },
  "simulate": { compact: simulate48, compact2x: simulate96, detail: simulateDetail96, detail2x: simulateDetail192 },
  "growth": { compact: growth48, compact2x: growth96, detail: growthDetail96, detail2x: growthDetail192 },
  "refresh": { compact: refresh48, compact2x: refresh96 },
  "close": { compact: close48, compact2x: close96 },
  "expand": { compact: expand48, compact2x: expand96 },
  "copy": { compact: copy48, compact2x: copy96 },
  "warning": { compact: warning48, compact2x: warning96 },
  "info": { compact: info48, compact2x: info96 },
  "pause": { compact: pause48, compact2x: pause96 },
  "play": { compact: play48, compact2x: play96 },
};
const ALIASES: Partial<Record<IconName, string>> = {
  "capture": "add",
  "url": "relation",
  "text": "memory",
  "outcome": "success",
  "save": "memory",
  "confirm": "success",
  "cancel": "close",
  "collapse": "expand",
  "error": "warning",
  "time": "timeline"
};

export interface IconProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "srcSet" | "sizes" | "alt" | "width" | "height" | "aria-hidden" | "role"> {
  name: IconName;
  size?: number;
  label?: string;
}

export function Icon({ name, size = 20, label, className, ...props }: IconProps): ReactElement {
  const art = ARTWORK[ALIASES[name] ?? name];
  const detailed = size >= 40 && art.detail;
  return <img
    {...props}
    className={["icon", name === "collapse" ? "icon-reversed" : "", className].filter(Boolean).join(" ")}
    data-icon={name}
    src={detailed ? art.detail : art.compact}
    srcSet={detailed ? `${art.detail} 1x, ${art.detail2x} 2x` : `${art.compact} 1x, ${art.compact2x} 2x`}
    width={size}
    height={size}
    loading="lazy"
    decoding="async"
    alt={label ?? ""}
    {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
  />;
}

