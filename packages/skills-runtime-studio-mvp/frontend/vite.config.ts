import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/studio/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
  },
});
