import { defineConfig } from 'vitest/config';
import preact from '@preact/preset-vite';

// Served from GitHub Pages at https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/
export default defineConfig({
  base: '/dsl-teaching-toolkit/',
  plugins: [preact()],
  test: {
    environment: 'node',
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
