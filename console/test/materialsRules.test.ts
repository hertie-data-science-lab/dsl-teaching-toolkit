import { describe, expect, it } from 'vitest';
import rules from '../schemas/materials.json';
import { badgeFiles, hostedPaths } from '../src/edit/badges';
import { aliasKind, inferKind, landingSection, readDeclared } from '../src/model/materialsRules';

describe('the publish.yml rule', () => {
  // The engine answered each case (`materials.hosted_paths`); the console must agree.
  for (const c of rules.cases)
    it(`agrees with the engine: ${c.name}`, () => {
      expect([...hostedPaths(c.paths, c.public)].sort()).toEqual(c.hosted);
    });

  it('badges a withheld file withheld, whatever publishes it', () => {
    const b = badgeFiles(['lectures/a.html', 'lectures/a_files/x.js'], ['**'], ['lectures/a.html']).badges;
    expect(b).toEqual({ 'lectures/a.html': 'withheld', 'lectures/a_files/x.js': 'public' });
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
