import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import "./cinematic.css";
import "./page-atmosphere.css";
import "./compact-glass.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("React root element is missing");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
