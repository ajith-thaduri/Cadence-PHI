/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The production build is written into the Python package and served by `python -m harness_ui serve`.
// `npm run dev` proxies /api to a console started with `python -m harness_ui serve --port 8765`.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../harness_ui/static',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // stable vendor chunks: application changes do not invalidate cached libraries
        manualChunks: {
          react: ['react', 'react-dom', 'react-router', '@tanstack/react-query'],
          mantine: ['@mantine/core', '@mantine/hooks', '@mantine/notifications', '@mantine/dropzone'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
