import { describe, expect, it } from 'vitest';
import rules from '../schemas/selection.json';
import demo from './fixtures/demo-hosting.json';
import { HOST_DEFAULT, PUBLISH_DEFAULT, exactPattern, hostable, keeps, kindOfLanded, landings, readSel, rootIgnore, select, withDefaults, withPublic, type Sel } from '../src/model/selection';

describe('the selection rule', () => {
  // The engine answered each case (`selection.select`); the console must agree.
  for (const c of rules.cases)
    it(`agrees with the engine: ${c.name}`, () => {
      const by = c.kinds_by_folder as Record<string, string>;
      const use = c.use === 'host' ? HOST_DEFAULT : c.use === 'publish' ? PUBLISH_DEFAULT : c.use === null ? undefined : readSel(c.use);
      const got = select(c.paths, (p) => by[p.split('/')[0]] ?? null, { use, withheld: rootIgnore(c.releaseignore), floor: c.floor ?? undefined });
      expect(got).toEqual(c.kept);
    });

  it('hosts what the engine hosts for the demo fixture', () => {
    // tests/test_selection.py writes the file from the real `site._hostable`.
    const kinds = demo.kinds as Record<string, string | null>;
    for (const c of demo.cases) expect(hostable(demo.paths, readSel(c.host), (p) => kinds[p] ?? null).sort()).toEqual(c.hosted);
  });

  it('takes a left-out key from the default, never a written empty one', () => {
    expect(withDefaults({}, PUBLISH_DEFAULT)).toEqual(PUBLISH_DEFAULT);
    expect(withDefaults({ exclude: [] }, PUBLISH_DEFAULT).exclude).toEqual([]);
  });

  it('reads landings and public copies off a schedule', () => {
    const doc = { readings: { r1: { files: [{ from_course_repo: 'cm', from_path: 'readings/01', public: false }, { from_course_repo: 'cm', from_path: 'readings/01/a[1].pdf', public: true }] } }, lectures: { l1: { files: [{ from_course_repo: 'cm', from_path: 'x', to_path: 'lectures/01' }] } } };
    const ls = landings(doc, 'materials');
    const kindOf = kindOfLanded(ls);
    expect(kindOf('lectures/01/a.pdf')).toBe('lectures');
    expect(kindOf('readings/01/b.pdf')).toBe('readings');
    expect(kindOf('other.pdf')).toBe(null);
    const host: Sel = withPublic(HOST_DEFAULT, ls);
    expect(keeps(host, 'readings/01/a[1].pdf', 'readings')).toBe(true);
    expect(keeps(host, 'readings/01/b.pdf', 'readings')).toBe(false);
    expect(exactPattern('a[1]/b*.pdf')).toBe('/a\\[1\\]/b\\*.pdf');
  });
});
