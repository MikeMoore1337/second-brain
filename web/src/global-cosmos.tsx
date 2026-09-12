import { useEffect, useRef } from "react";
import { usePageMotion } from "./page-motion";
import { createCosmosRenderer } from "./cosmos-renderer";
import "./global-cosmos.css";

/** One viewport-wide environment, outside every content/hero clipping boundary. */
export function GlobalCosmosBackground() {
  const gas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<ReturnType<typeof createCosmosRenderer>>(null);
  const { paused } = usePageMotion();
  useEffect(() => {
    if (!gas.current) return;
    renderer.current = createCosmosRenderer(gas.current);
    return () => { renderer.current?.dispose(); renderer.current = null; };
  }, []);
  useEffect(() => { renderer.current?.setPaused(paused); }, [paused]);
  return <div className="global-cosmos" aria-hidden="true">
    <canvas ref={gas} className="cosmos-gas" />
  </div>;
}
