import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// Some shells (e.g. this NixOS profile) export NODE_ENV=production globally. That
// makes Node load React's *production* build, which omits `React.act`, so
// @testing-library/react falls back to the deprecated react-dom/test-utils act and
// throws "React.act is not a function". Pin test mode so the suite is immune.
process.env.NODE_ENV = 'test';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    env: { NODE_ENV: 'test' },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
