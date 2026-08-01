import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `BIOLEAD_BASE` is the path the app is mounted at, and it is a deployment concern, so it
// arrives from the environment and defaults to the root. A host serving the app from a
// subdirectory sets it at build time:
//
//     BIOLEAD_BASE=/somewhere/ npm run build
//
// Development always runs at the root, so `make web` and the dev proxy are unchanged. The
// API path is derived at runtime in src/apiBase.ts, so nothing about the deployment reaches
// the bundle beyond the asset prefix.
export default defineConfig(({ command }) => ({
  base: command === "build" ? process.env.BIOLEAD_BASE || "/" : "/",
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8931" } },
}));
