import { describe, expect, it } from 'vitest';
import rules from '../schemas/materials.json';
import { badgeFiles } from '../src/edit/badges';
import { compileAll, withheldBy } from '../src/edit/glob';
import { aliasKind, inferKind, landingSection, readDeclared } from '../src/model/materialsRules';

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
    expect(inferKind('datasets')).toEqual({ kind: 'lecture', named: false });
  });

  it('takes the section from where the copy lands', () => {
    expect(landingSection({ folder: 'labs/01', path: '', dest: '' }, 'materials')).toBe('labs');
    expect(landingSection({ folder: 'labs/01', path: 'week-1/lab', dest: '' }, 'materials')).toBe('week-1');
    expect(landingSection({ folder: 'labs', path: '', dest: 'labs-repo' }, 'materials')).toBe('labs-repo');
    expect(landingSection({ folder: 'SYLLABUS.md', path: '', dest: '' }, 'materials')).toBe('materials');
  });

  it('reads materials.yml as the engine does', () => {
    expect(readDeclared(null)).toEqual({ syllabus: 'SYLLABUS.md', declared: false, kinds: {} });
    expect(readDeclared('syllabus: /E1282.pdf\nkinds:\n  Tutorials/: Lab\n')).toEqual({ syllabus: 'E1282.pdf', declared: true, kinds: { tutorials: 'lab' } });
    expect(readDeclared('kinds: [')).toBeNull();
  });
});
