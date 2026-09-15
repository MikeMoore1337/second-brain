import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { registerPwa } from "./pwa";
import "./styles.css";
import "./cinematic.css";
import "./page-atmosphere.css";
import "./compact-glass.css";
import "./semantic-navigation.css";
import "./login.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Не найден корневой элемент React.");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

registerPwa();
