import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { AuthProvider } from "./lib/auth";
import { ToastProvider } from "./lib/toast";
import { applyTheme, readTheme } from "./lib/theme";

// El tema se aplica antes de pintar para no mostrar un destello del otro.
applyTheme(readTheme());

const root = document.getElementById("root");
if (!root) throw new Error("Falta el contenedor #root en index.html");

createRoot(root).render(
  <StrictMode>
    <ToastProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ToastProvider>
  </StrictMode>,
);
