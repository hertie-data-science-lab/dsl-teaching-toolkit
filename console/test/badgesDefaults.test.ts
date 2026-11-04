// Decision B (2026-11-04): the institution's content names are withheld by default and a `!` line
// re-includes them; the never-released names are fixed. The copy rule is held to the engine's
// own answers (`schemas/materials.json` `copied`, from `selection.deny`'s rule), and a click on
// the tree flips a default row by writing or removing its `!` line.

import { describe, expect, it } from 'vitest';
import materials from '../schemas/materials.json';
import selectionRules from '../schemas/selection.json';
import { defaultWithheld, hardDenied, neverMaterial, standing, toggle, withholdTally } from '../src/edit/badges';
import { compileAll, withheldOnCopy } from '../src/edit/glob';
import { DENYLIST_CONTENT, DENYLIST_HARD, contentDenied, contentName, denylisted } from '../src/model/selection';

const st = (lines: string[], path: string, dir: boolean) => standing(compileAll(lines), lines, path, dir, true);

describe('the two default groups', () => {
  it('come from the engine’s export, and the denylist is both', () => {
    expect(DENYLIST_HARD).toEqual(selectionRules.denylist.hard);
    expect(DENYLIST_CONTENT).toEqual(selectionRules.denylist.content);
    expect(hardDenied('labs/01/.ENV.local')).toBe(true);
    expect(hardDenied('labs/01/solutions/a.py')).toBe(false);
    expect(contentDenied('labs/01/Tests/t.py')).toBe(true);
    expect(denylisted('grading_config.yml') && denylisted('solutions/a.py')).toBe(true);
  });

  it('a folder copy carries what the engine says, for every case', () => {
    for (const c of materials.cases) {
      const rs = compileAll(c.patterns);
      const copied = c.paths.filter((f) => !neverMaterial(f) && !hardDenied(f) && !withheldOnCopy(rs, f, false, contentName));
      expect(copied.sort(), c.name).toEqual([...c.copied].sort());
    }
  });
});

describe('standing with the defaults', () => {
  it('a content name is withheld by default, at itself or the folder it sits in', () => {
    expect(st([], 'labs/solutions', true)).toEqual({ kind: 'default', at: 'labs/solutions' });
    expect(st([], 'labs/solutions/a.py', false)).toEqual({ kind: 'default', at: 'labs/solutions' });
    expect(defaultWithheld(compileAll([]), 'labs/solutions/a.py')).toBe(true);
    // Without the defaults (every other list) the old reading holds.
    expect(standing([], [], 'labs/solutions', true)).toEqual({ kind: 'released' });
  });

  it('a ! line overrides the default, its own or a broader one, last match winning', () => {
    expect(st(['!labs/solutions/'], 'labs/solutions', true)).toEqual({ kind: 'overridden', rule: '!labs/solutions/' });
    expect(st(['!solutions/'], 'labs/solutions', true)).toEqual({ kind: 'overridden', rule: '!solutions/' });
    expect(st(['!solutions/'], 'labs/solutions/a.py', false)).toEqual({ kind: 'released' });
    expect(st(['!solutions/', 'labs/'], 'labs/solutions', true)).toMatchObject({ kind: 'broader', rule: 'labs/' });
    // Anchored like any other line, and as written (gitignore is case-sensitive).
    expect(st(['!/solutions/'], 'labs/solutions', true)).toEqual({ kind: 'default', at: 'labs/solutions' });
    expect(st(['!tests/'], 'labs/Tests', true)).toEqual({ kind: 'default', at: 'labs/Tests' });
  });
});

describe('a click on a default row', () => {
  it('writes the ! line, and a second click takes it out', () => {
    const once = toggle(['drafts/'], 'labs/solutions', true, true);
    expect(once).toEqual({ lines: ['drafts/', '!labs/solutions/'] });
    expect(toggle((once as { lines: string[] }).lines, 'labs/solutions', true, true)).toEqual({ lines: ['drafts/'] });
    expect(toggle([], 'solutions', true, true)).toEqual({ lines: ['!/solutions/'] });
  });

  it('withholds a row a broader ! line re-includes with its own line, and a click releases it again', () => {
    const once = toggle(['!solutions/'], 'labs/solutions', true, true);
    expect(once).toEqual({ lines: ['!solutions/', 'labs/solutions/'] });
    expect(toggle((once as { lines: string[] }).lines, 'labs/solutions', true, true)).toEqual({ lines: ['!solutions/'] });
  });

  it('releasing a content row withheld by its own line also writes the ! line', () => {
    expect(toggle(['labs/solutions/'], 'labs/solutions', true, true)).toEqual({ lines: ['!labs/solutions/'] });
    // Without the defaults the line just goes.
    expect(toggle(['labs/solutions/'], 'labs/solutions', true)).toEqual({ lines: [] });
  });

  it('a file inside a default-withheld folder cannot be released on its own', () => {
    expect(toggle([], 'labs/solutions/a.py', false, true)).toEqual({ blocked: { rule: '', at: 'labs/solutions', byDefault: true } });
  });
});

describe('the Withhold view’s summary', () => {
  it('counts never released, withheld (by a line or the default) and released', () => {
    const files = ['.releaseignore', 'labs/.env', 'labs/.DS_Store', 'labs/solutions/a.py', 'tests/t.py', 'drafts/x.md', 'lab.md'];
    expect(withholdTally(files, ['drafts/'])).toEqual({ never: 3, withheld: 3, released: 1 });
    expect(withholdTally(files, ['drafts/', '!solutions/', '!.env'])).toEqual({ never: 3, withheld: 2, released: 2 });
  });
});
