import { describe, expect, it } from 'vitest';
import rules from '../schemas/materials.json';
import { badgeFiles } from '../src/edit/badges';
import { compileAll, withheldBy } from '../src/edit/glob';
import { EMPTY_ENTRY_KIND, aliasKind, inferKind, landingSection, readDeclared } from '../src/model/materialsRules';
import landingKinds from '../../tests/fixtures/landing_kinds.json';

type Landing = { dest: string; repo: string; aliases: Record<string, string>; section: string; kind: string };

describe('the withhold rule', () => {
  const withheldPaths = (files: string[], lines: string[]) => files.filter((f) => withheldBy(compileAll(lines), f) !== null);

  // The engine answered each case (`releaseignore`, git's rule); the console must agree.
  for (const c of rules.cases)
    it(`agrees with the engine: ${c.name}`, () => {
      expect(withheldPaths(c.paths, c.patterns).sort()).toEqual(c.withheld);
    });

  it('reads an escaped bracket as itself', () => {
    expect(withheldPaths(['a[b]/x.pdf', 'ab/x.pdf'], ['a\\[b]/*'])).toEqual(['a[b]/x.pdf']);
  });

  it('badges never-material names and the list file withheld, whatever the list says', () => {
    const b = badgeFiles(['l/.gitkeep', 'l/.DS_Store', '.releaseignore', 'l/a.md'], ['!l/.gitkeep']).badges;
    expect(b).toEqual({ 'l/.gitkeep': 'withheld', 'l/.DS_Store': 'withheld', '.releaseignore': 'withheld', 'l/a.md': 'released' });
  });
});

describe('kinds', () => {
  it('reads a folder by its alias, in any case, the repo’s own first', () => {
    expect(aliasKind('Labs')).toBe('lab');
    expect(aliasKind('quiz')).toBeNull();
    expect(aliasKind('quiz', { quiz: 'exam' })).toBe('exam');
    expect(aliasKind('quiz', { quiz: 'nonsense' })).toBe('other');
    // Decision 0031 rule 10: a folder no name covers is supporting files.
    expect(inferKind('datasets')).toEqual({ kind: 'assets', named: false });
    expect(EMPTY_ENTRY_KIND).toBe('lecture');
  });

  // `schedule_plan.deploy_section` + `infer_kind` (#391): dest, repo, aliases -> section, kind.
  // Mirrors `tests/fixtures/landing_kinds.json` (W1) until that shared table is on this branch.
  // One table for both rules: pytest runs it through `schedule_plan.entry_landing`.
  for (const c of landingKinds.cases as Landing[])
    it(`lands ${c.dest} in ${c.repo || 'the default repo'} as the engine does`, () => {
      const section = landingSection({ folder: 'src/x', path: c.dest, dest: c.repo }, 'materials', c.aliases);
      expect(section).toBe(c.section);
      expect(inferKind(section, c.aliases).kind).toBe(c.kind);
    });

  it('falls back to the folder when no path is given', () => {
    expect(landingSection({ folder: 'labs/01', path: '', dest: '' }, 'materials')).toBe('labs');
  });

  it('reads materials.yml as the engine does', () => {
    expect(readDeclared(null)).toEqual({ syllabus: 'SYLLABUS.md', declared: false, kinds: {} });
    expect(readDeclared('syllabus: /E1282.pdf\nkinds:\n  Tutorials/: Lab\n')).toEqual({ syllabus: 'E1282.pdf', declared: true, kinds: { tutorials: 'lab' } });
    expect(readDeclared('kinds: [')).toBeNull();
  });
});
