import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// The three font families, bundled into assets/ instead of loaded from
// Google Fonts: the same files, from the Fontsource packages (SIL OFL 1.1).
import "@fontsource-variable/dm-sans";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./tokens.css";
import "./styles.css";
import "maplibre-gl/dist/maplibre-gl.css";
import { setWorkerUrl } from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";

// MapLibre (since v6) runs its tile processing in a separate module worker,
// which it looks for beside its own script. The bundler does not put it
// there, so emit it as an asset and give MapLibre its URL before any map is
// created.
setWorkerUrl(maplibreWorkerUrl);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
