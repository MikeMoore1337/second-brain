import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { FoldSection, revealDestination } from "../fold-section";

it("opens a deep destination repeatedly without remounting its editor", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const mounted = vi.fn();
  function Editor() { const [text, setText] = useState(() => { mounted(); return "Черновик"; }); return <section id="timeline"><button onClick={() => setText("Изменено")}>{text}</button></section>; }
  const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
  try {
    await act(async()=>root.render(<FoldSection id="timeline" title="Хронология" icon="timeline"><Editor /></FoldSection>));
    const details=host.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(revealDestination("#timeline")).toBe(host.querySelector("#timeline"));
    expect(details.open).toBe(true);
    await act(async()=>host.querySelector("button")!.click());
    details.open=false;revealDestination("#timeline");
    expect(details.open).toBe(true);
    expect(host.querySelector("button")?.textContent).toBe("Изменено");
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(revealDestination("#unknown")).toBeNull();
  } finally { await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals(); }
});
