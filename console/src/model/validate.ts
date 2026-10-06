// The console's validators, compiled from schemas/ at build time (vite.config.ts,
// `virtual:validators`): the page never compiles code at run time, so its CSP needs no
// 'unsafe-eval'. A schema is found by its JSON text.

import type { ValidateFunction } from 'ajv/dist/2020';
import { table } from 'virtual:validators';

const precompiled = table as Record<string, ValidateFunction>;

export function validator(schema: object): ValidateFunction {
  const fn = precompiled[JSON.stringify(schema)];
  if (!fn) throw new Error('No precompiled validator for this schema: it is not under console/schemas/.');
  return fn;
}
