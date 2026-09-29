// The withhold tree (decision 0016): a click toggles a path's exact line, a broader rule is
// named, and `!path` is added only where git allows re-inclusion. Plus `select` mode (the import
// picker).

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { exactLine, toggle } from '../src/edit/badges';
import { PatternTree } from '../src/ui/PatternTree';

const FILES = ['SYLLABUS.md', 'labs/01/lab.pdf', 'labs/01/data.csv', 'lectures/01/a.pdf', 'lectures/02/b.pdf'];

describe('toggling a path', () => {
  it('writes the exact line: anchored at the top level, a folder with its slash', () => {
    expect(exactLine('SYLLABUS.md', false)).toBe('/SYLLABUS.md');
    expect(exactLine('labs', true)).toBe('/labs/');
    expect(exactLine('labs/01/lab.pdf', false)).toBe('labs/01/lab.pdf');
    expect(toggle(['# kept'], 'labs/01', true)).toEqual({ lines: ['# kept', 'labs/01/'] });
    expect(toggle(['a', ''], 'SYLLABUS.md', false)).toEqual({ lines: ['a', '/SYLLABUS.md'] });
  });

  it('removes a path’s own line on the second click, in either spelling', () => {
    expect(toggle(['# kept', 'labs/01/'], 'labs/01', true)).toEqual({ lines: ['# kept'] });
    expect(toggle(['labs/', 'x'], 'labs', true)).toEqual({ lines: ['x'] });
    expect(toggle(['/SYLLABUS.md'], 'SYLLABUS.md', false)).toEqual({ lines: [] });
  });

  it('re-includes a file a broader pattern withholds with a ! line, and removes it again', () => {
    const once = toggle(['*.pdf'], 'lectures/01/a.pdf', false);
    expect(once).toEqual({ lines: ['*.pdf', '!lectures/01/a.pdf'] });
    expect(toggle((once as { lines: string[] }).lines, 'lectures/01/a.pdf', false)).toEqual({ lines: ['*.pdf'] });
  });

  it('refuses to re-include inside a withheld folder, naming the folder rule', () => {
    expect(toggle(['labs/'], 'labs/01/lab.pdf', false)).toEqual({ blocked: { rule: 'labs/', at: 'labs' } });
    expect(toggle(['labs/**'], 'labs/01/lab.pdf', false)).toEqual({ blocked: { rule: 'labs/**', at: 'labs/01' } });
  });
});

describe('the pattern tree', () => {
  const html = (patterns: string[], mode?: 'exclude' | 'select') => render(<PatternTree files={FILES} patterns={patterns} onChange={() => {}} mode={mode} />);

  it('badges from the draft and names the broader rule', () => {
    const out = html(['labs/']);
    expect(out).toMatch(/<span class="ft-name">labs\/<\/span><span class="chip amber">withheld<\/span>/);
    expect(out).toMatch(/<span class="ft-name">lab.pdf<\/span><span class="chip amber">withheld<\/span><span class="footnote">withheld by <code>labs\/<\/code><\/span>/);
    expect(out).toMatch(/<span class="ft-name">a.pdf<\/span><span class="chip ">released to students<\/span>/);
    expect(out).toContain('aria-label="Include labs/"');
    expect(out).toContain('aria-label="Withhold lectures/"');
  });

  it('in select mode ticks everything until a line leaves it out', () => {
    const all = html([], 'select');
    expect(all.match(/type="checkbox"/g)?.length).toBe(10); // 5 files, 5 folders
    expect(all.match(/class="ft-tick" checked/g)?.length).toBe(10);
    const some = html(['lectures/02/'], 'select');
    expect(some).toMatch(/<input type="checkbox" class="ft-tick" aria-label="Include lectures\/02\/"/);
    expect(some).toMatch(/<input type="checkbox" class="ft-tick" checked aria-label="Include lectures\/01\/"/);
    expect(some).toContain('class="file-tree select"');
  });
});
