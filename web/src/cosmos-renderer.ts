import { fragmentSource, vertexSource } from "./cosmos-shaders";

/** A procedural scene with no image assets or texture sampling. */
export function createCosmosRenderer(gas: HTMLCanvasElement) {
  const parent = gas.parentElement;
  if (!parent) return null;
  const fallback = document.createElement("canvas");
  fallback.className = "cosmos-fallback";
  parent.prepend(fallback);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const gl = gas.getContext("webgl", { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: "low-power" });
  let program: WebGLProgram | null = null;
  let buffer: WebGLBuffer | null = null;
  const shaders: WebGLShader[] = [];
  let uniforms: Record<string, WebGLUniformLocation | null> = {};
  let frame = 0, elapsed = 0, last = 0, previous = 0;
  let paused = false, lost = false, disposed = false;
  let width = 0, height = 0, interval = 1000 / 24, slowFrames = 0;
  const showFallback = () => {
    fallback.hidden = false; gas.hidden = true;
    fallback.width = gas.width; fallback.height = gas.height;
    const ctx = fallback.getContext("2d");
    if (!ctx || !width || !height) return;
    ctx.fillStyle = "#020205"; ctx.fillRect(0,0,fallback.width,fallback.height);
    ctx.scale(fallback.width/width,fallback.height/height);
    let seed=713;
    const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
    // Simplified static procedural fallback; reduced motion retains the full shader.
    for(let i=0;i<140;i++) {
      const x=random()*width,y=height-x/width*height+(random()-.5)*height*.45;
      const r=height*(.04+random()*.14),glow=ctx.createRadialGradient(x,y,0,x,y,r);
      glow.addColorStop(0,"#7135a608");glow.addColorStop(1,"#7135a600");
      ctx.fillStyle=glow;ctx.fillRect(x-r,y-r,r*2,r*2);
    }
    for(let i=0;i<Math.min(2200,width*height/700);i++) {
      ctx.fillStyle=`rgba(211,185,242,${.12+random()*.55})`;
      ctx.beginPath();ctx.arc(random()*width,random()*height,.3+random()*.5,0,Math.PI*2);ctx.fill();
    }
  };
  const initialize = () => {
    if (!gl) return;
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) throw new Error("shader");
      shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error("compile");
      return shader;
    };
    try {
      program = gl.createProgram();
      if (!program) return;
      gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexSource));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error("link");
      gl.useProgram(program);
      buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
      const position = gl.getAttribLocation(program, "position");
      gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      uniforms = Object.fromEntries(["resolution", "viewport", "time", "detail"].map(name => [name, gl.getUniformLocation(program!, name)]));
      fallback.hidden=true;gas.hidden=false;
    } catch {
      shaders.forEach(shader => gl.deleteShader(shader)); shaders.length = 0;
      if (buffer) gl.deleteBuffer(buffer); buffer = null;
      if (program) gl.deleteProgram(program);
      program = null; showFallback();
    }
  };
  const render = () => {
    if (!gl || !program || lost) return;
    gl.uniform2f(uniforms.resolution, gas.width, gas.height);
    gl.uniform2f(uniforms.viewport, width, height);
    gl.uniform1f(uniforms.time, elapsed);
    gl.uniform1f(uniforms.detail, width < 600 ? 7 : 8);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gas.dataset.time = elapsed.toFixed(3);
  };
  const running = () => !disposed && !paused && !reduced.matches && !document.hidden && !!program && !lost;
  const tick = (now: number) => {
    frame = 0;
    if (!running()) return;
    if (previous && now - previous > 80) slowFrames++; else slowFrames = Math.max(0, slowFrames - 1);
    previous = now;
    if (slowFrames > 12) {
      // Protect spatial detail: reduce cadence, never downscale the viewport.
      interval = Math.min(1000 / 15, interval * 1.2); slowFrames = 0;
    }
    if (now - last >= interval) {
      elapsed += Math.min(now - last, 100) / 1000;
      last = now;
      render();
    }
    frame = requestAnimationFrame(tick);
  };
  const update = () => {
    cancelAnimationFrame(frame); frame = 0; last = performance.now(); previous = 0;
    if (running()) frame = requestAnimationFrame(tick);
  };
  const resize = () => {
    width = window.innerWidth; height = window.innerHeight;
    const pixelBudget = width < 600 ? 2400000 : 8300000;
    const scale = Math.min(devicePixelRatio || 1, 2, Math.sqrt(pixelBudget / (width * height)));
    gas.width = Math.round(width * scale); gas.height = Math.round(height * scale);
    gl?.viewport(0, 0, gas.width, gas.height);
    interval = 1000 / (width < 600 ? 20 : 24);
    if (program && !lost) render(); else showFallback();
  };
  const contextLost = (event: Event) => {
    event.preventDefault(); lost = true; showFallback(); update();
  };
  const contextRestored = () => {
    shaders.length = 0; lost = false; initialize(); resize(); update();
  };
  initialize(); resize(); update();
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", update);
  reduced.addEventListener("change", update);
  gas.addEventListener("webglcontextlost", contextLost);
  gas.addEventListener("webglcontextrestored", contextRestored);
  return {
    setPaused(value: boolean) { paused = value; update(); },
    dispose() {
      disposed = true; cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", update);
      reduced.removeEventListener("change", update);
      gas.removeEventListener("webglcontextlost", contextLost);
      gas.removeEventListener("webglcontextrestored", contextRestored);
      shaders.forEach(shader => gl?.deleteShader(shader));
      if (buffer) gl?.deleteBuffer(buffer);
      if (program) gl?.deleteProgram(program);
      fallback.remove();
    },
  };
}
