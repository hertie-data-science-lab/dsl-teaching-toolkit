import { defineConfig } from 'vitest/config';
import preact from '@preact/preset-vite';
import type { Plugin } from 'vite';
import { withCsp } from './src/csp.ts';

/** Fills index.html's Content-Security-Policy on a build, with the relay's origin from VITE_AUTH_RELAY_URL. */
function csp(): Plugin {
  let build = false;
  let relay: string | undefined;
  return {
    name: 'console-csp',
    configResolved(c) {
      build = c.command === 'build';
      relay = c.env.VITE_AUTH_RELAY_URL;
    },
    transformIndexHtml: (html) => withCsp(html, build, relay),
  };
}

// Served from GitHub Pages at https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/
export default defineConfig({
  base: '/dsl-teaching-toolkit/',
  plugins: [preact(), csp()],
  test: {
    environment: 'node',
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
