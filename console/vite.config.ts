/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import { defineConfig } from 'vitest/config';
import preact from '@preact/preset-vite';
import Ajv2020 from 'ajv/dist/2020';
import standaloneCode from 'ajv/dist/standalone';
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

/**
 * `virtual:validators`: every schema under schemas/ (and each op's args_schema in ops.json)
 * compiled to code at build time, so the page needs no `new Function` and the CSP no
 * 'unsafe-eval'. `table` maps JSON.stringify(schema) to its validator.
 */
function validators(): Plugin {
  const ID = 'virtual:validators';
  const dir = new URL('./schemas/', import.meta.url);
  return {
    name: 'console-validators',
    resolveId: (id) => (id === ID ? `\0${ID}` : null),
    load(id) {
      if (id !== `\0${ID}`) return null;
      const read = (f: string) => JSON.parse(readFileSync(new URL(f, dir), 'utf8'));
      const files = readdirSync(dir).filter((f) => f.endsWith('.schema.json'));
      const ops = read('ops.json').ops as { args_schema?: object }[];
      const schemas = [...files.map(read), ...ops.flatMap((o) => (o.args_schema ? [o.args_schema] : []))];
      const keys = [...new Set(schemas.map((s) => JSON.stringify(s)))];
      const ajv = new Ajv2020({ allErrors: true, strict: false, code: { source: true, esm: true } });
      keys.forEach((k, i) => ajv.addSchema(JSON.parse(k), `v${i}`));
      const code = standaloneCode(ajv, Object.fromEntries(keys.map((_, i) => [`v${i}`, `v${i}`])));
      return `${code}\nexport const table = {${keys.map((k, i) => `${JSON.stringify(k)}: v${i}`).join(',\n')}};\n`;
    },
  };
}

// Served from GitHub Pages at https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/
export default defineConfig({
  base: '/dsl-teaching-toolkit/',
  plugins: [preact(), csp(), validators()],
  test: {
    environment: 'node',
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
