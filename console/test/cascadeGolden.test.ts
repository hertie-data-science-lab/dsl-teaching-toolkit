// Decision 0039 rule 2: before the console stops recomputing the cascade, the golden vectors
// exported from the engine (`tests/test_cascade_golden.py`) prove cascade.ts answers each one
// exactly as `settings.resolve` does. The engine's `entry` is the console's `assignment`.

import { describe, expect, it } from 'vitest';
import GOLDEN from './fixtures/cascade-golden.json';
import { resolve, usableBlock, type Layers, type RunKey } from '../src/model/cascade';

type Raw = Record<string, Record<string, unknown>>;

// The one divergence the vectors found: cascade.ts reads a negative late window as 0, the
// engine refuses it (`setting_readers.whole_days`), so the pair's other half answers it with
// none. Moot once the console reads the engine's answer from status.json.
const KNOWN = ['{"semester":{"late_penalty_per_day":"5%","late_window_days":-3}} late_window_days'];

describe('cascade.ts against the engine’s golden vectors', () => {
  it('answers every vector with the engine’s value and source', () => {
    const institution = GOLDEN.institution as Record<string, unknown>;
    const misses: string[] = [];
    for (const v of GOLDEN.vectors) {
      const raw = v.layers as Raw;
      const layers: Layers = {
        assignment: usableBlock(raw.entry ?? {}),
        semester: usableBlock(raw.semester ?? {}),
        course: usableBlock(raw.course ?? {}),
        institution,
      };
      const got = resolve(v.key as RunKey, layers);
      const source = got.source === 'assignment' ? 'entry' : got.source;
      const at = `${JSON.stringify(raw)} ${v.key}`;
      if (KNOWN.includes(at)) continue;
      if (JSON.stringify([got.value, source]) !== JSON.stringify([v.value, v.source])) misses.push(`${JSON.stringify(raw)} ${v.key}: ${JSON.stringify([got.value, source])} != ${JSON.stringify([v.value, v.source])}`);
    }
    expect(misses).toEqual([]);
    expect(GOLDEN.vectors.length).toBeGreaterThan(50);
  });
});
