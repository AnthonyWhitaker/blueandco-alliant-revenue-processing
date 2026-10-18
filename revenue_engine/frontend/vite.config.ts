/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { visualizer } from 'rollup-plugin-visualizer';
import { defineConfig, loadEnv, type PluginOption } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [
      react(),
      // `npm run analyze` writes dist/stats.html (treemap of what's in each chunk).
      env.ANALYZE === '1' &&
        (visualizer({
          filename: 'dist/stats.html',
          gzipSize: true,
          template: 'treemap',
        }) as PluginOption),
    ],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        // Same-origin /api in dev, matching production where FastAPI serves the SPA.
        '/api': { target: env.API_PROXY_TARGET ?? 'http://localhost:8000', changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  };
});
