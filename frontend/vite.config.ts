import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@contracts': fileURLToPath(new URL('../contracts', import.meta.url)) },
  },
  server: {
    port: 5173,
    // фикстуры лежат в ../contracts — разрешаем dev-серверу читать корень репозитория
    fs: { allow: [repoRoot] },
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  test: { environment: 'node' },
});
