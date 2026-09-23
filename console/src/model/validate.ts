// One Ajv for the whole console: each schema object is compiled once, on first use.

import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020';

const ajv = new Ajv2020({ allErrors: true, strict: false });
const compiled = new WeakMap<object, ValidateFunction>();

export function validator(schema: object): ValidateFunction {
  let fn = compiled.get(schema);
  if (!fn) {
    fn = ajv.compile(schema);
    compiled.set(schema, fn);
  }
  return fn;
}
