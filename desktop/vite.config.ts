import { defineConfig, Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const RUNTIME_PATH = path.join(
  os.homedir(),
  'Library',
  'Application Support',
  'Say',
  'runtime.json',
);

function readRuntime(): { host: string; port: number; token: string } | null {
  try {
    const data = JSON.parse(fs.readFileSync(RUNTIME_PATH, 'utf8')) as {
      host?: string;
      port?: number;
      token?: string;
    };
    if (data.port) {
      return { host: data.host ?? '127.0.0.1', port: data.port, token: data.token ?? '' };
    }
  } catch {
    // runtime.json absent or unreadable — frontend falls back to baked env.
  }
  return null;
}

// Vite middleware that exposes the current runtime.json to the browser at
// `/__say/runtime`. The file is re-read on every request, so the frontend
// recovers automatically when the engine restarts with a new port/token —
// no Vite restart needed.
function sayRuntimePlugin(): Plugin {
  return {
    name: 'say-runtime',
    configureServer(server) {
      server.middlewares.use('/__say/runtime', (_req, res) => {
        const rt = readRuntime();
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Cache-Control', 'no-store');
        res.end(JSON.stringify(rt ?? { error: 'runtime.json not found — start the engine' }));
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), sayRuntimePlugin()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: 'localhost',
  },
  envPrefix: ['VITE_', 'TAURI_'],
  define: (() => {
    // Bake the runtime values into the bundle at Vite startup as a last-resort
    // fallback for environments where the dev middleware isn't available
    // (e.g. a production preview). The browser dev path prefers the
    // `/__say/runtime` middleware above, which re-reads on every fetch.
    const rt = readRuntime();
    return {
      'import.meta.env.VITE_DEV_ENGINE_PORT': JSON.stringify(rt ? String(rt.port) : ''),
      'import.meta.env.VITE_DEV_ENGINE_TOKEN': JSON.stringify(rt?.token ?? ''),
    };
  })(),
  build: {
    target: 'es2021',
    sourcemap: false,
  },
});
