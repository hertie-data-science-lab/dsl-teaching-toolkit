import { describe, expect, it } from 'vitest';
import { assignmentIdent, fmtWhen, md, releaseIdent, sortKey, templateName } from '../src/model/format';
import { weekOf } from '../src/model/schedule';
import type { Release } from '../src/model/types';

describe('md', () => {
  it('joins lines inside a paragraph with a space', () => {
    expect(md('Trees, bagging\nand forests.')).toBe('<p>Trees, bagging and forests.</p>');
  });
  it('breaks a line that ends in two spaces', () => {
    expect(md('Room 2.61  \nclosed book')).toBe('<p>Room 2.61<br>closed book</p>');
  });
  it('starts a new paragraph at a blank line', () => {
    expect(md('One.\n\nTwo *more*.')).toBe('<p>One.</p><p>Two <i>more</i>.</p>');
  });
  it('splits a paragraph that runs into a list', () => {
    expect(md('Intro:\n- a\n- b')).toBe('<p>Intro:</p><ul><li>a</li><li>b</li></ul>');
  });
  it('keeps lists and escapes HTML', () => {
    expect(md('- a\n- <b>')).toBe('<ul><li>a</li><li>&lt;b&gt;</li></ul>');
  });
  it('renders a reading list as GitHub does: headings, one list, autolinks (the demo student view)', () => {
    const src = '### Session 1 readings\n#### Required Readings\n- Chatsiou, K. (2020). Text classification with deep learning. <https://example.org/chatsiou?a=1&b=2>\n- Jurafsky, D., & Martin, J. H. (2024). *Speech and language processing* (Ch. 2). [PDF](s1-jurafsky.pdf)\n\n#### Additional Readings\n* Manning, C. (2008). [Introduction to IR](https://nlp.stanford.edu/IR-book/).';
    expect(md(src)).toBe(
      '<h3>Session 1 readings</h3><h4>Required Readings</h4>'
      + '<ul><li>Chatsiou, K. (2020). Text classification with deep learning. <a href="https://example.org/chatsiou?a=1&amp;b=2" target="_blank" rel="noopener">https://example.org/chatsiou?a=1&amp;b=2</a></li>'
      + '<li>Jurafsky, D., &amp; Martin, J. H. (2024). <i>Speech and language processing</i> (Ch. 2). [PDF](s1-jurafsky.pdf)</li></ul>'
      + '<h4>Additional Readings</h4>'
      + '<ul><li>Manning, C. (2008). <a href="https://nlp.stanford.edu/IR-book/" target="_blank" rel="noopener">Introduction to IR</a>.</li></ul>',
    );
  });
  it('reads a heading only with whitespace after its #s, levels 1 to 6, closing #s dropped', () => {
    expect(md('# One #\n###### Six')).toBe('<h1>One</h1><h6>Six</h6>');
    expect(md('#hashtag and ####### seven')).toBe('<p>#hashtag and ####### seven</p>');
    expect(md('Intro\n## Next\ntext')).toBe('<p>Intro</p><h2>Next</h2><p>text</p>');
  });
  it('continues an item with a following plain line; *emphasis* at a line start is no item', () => {
    expect(md('- a long\n  citation\n- b')).toBe('<ul><li>a long citation</li><li>b</li></ul>');
    expect(md('*Note:* read this')).toBe('<p><i>Note:</i> read this</p>');
  });
});

describe('assignment names', () => {
  it('numbers an assignment by its entry, never by a position', () => {
    expect(assignmentIdent('assignment-3')).toBe('assignment-3'); // never the key's digits (decision 0038 amendment rule 1)
    expect(assignmentIdent('assignment-4-project', 'Project', 4)).toBe('Assignment 4');
    expect(assignmentIdent('assignment-4-project', 'Project', null)).toBe('Project');
    expect(assignmentIdent('assignment-4-project')).toBe('assignment-4-project');
    expect(assignmentIdent('assignment-3d-vision', '3D vision')).toBe('3D vision');
    expect(assignmentIdent('assignment-3d-vision')).toBe('assignment-3d-vision');
  });

  it('names a template by its title, never by a number in its repo name', () => {
    expect(templateName('Regression')).toBe('Regression');
    expect(templateName('  ')).toBe('Untitled template');
    expect(templateName('')).toBe('Untitled template');
  });
});

describe('releaseIdent', () => {
  const rel = (id: string, kind: string, when: string, number?: number | null) =>
    ({ id, kind, when, number, title: '', state: 'planned', source: null, dest: null, show_on_site: true, tbc: false }) as Release;
  it('uses the number the engine gives the row, with the kind label', () => {
    expect(releaseIdent(rel('lab-09', 'lab', '1', 9))).toBe('Lab 9');
    expect(releaseIdent(rel('extra', 'readings', '1', null))).toBe('Readings');
    expect(releaseIdent(rel('clinic', 'drop-in', '1', 2))).toBe('Drop-in 2');
  });
  it('never reads a number out of the key (decision 0038 amendment rule 1)', () => {
    expect(releaseIdent(rel('lecture_03', 'lecture', '1'))).toBe('Lecture');
    expect(releaseIdent(rel('01_lab', 'lab', '1'))).toBe('Lab');
    expect(releaseIdent(rel('wrap', 'lecture', '2026-09-08'))).toBe('Lecture');
  });
});

describe('schedule times', () => {
  const tz = 'Europe/Berlin';
  it('read a space between date and time as the engine does (as T)', () => {
    expect(fmtWhen('2026-10-07 10:00', tz)).toBe('Wed 7 Oct 10:00');
    expect(fmtWhen('2026-10-07 10:00:00', tz)).toBe('Wed 7 Oct 10:00');
    expect(fmtWhen('2026-10-07 10:00+02:00', tz)).toBe('Wed 7 Oct 10:00');
    expect(sortKey('2026-10-07 10:00', tz)).toBe('2026-10-07T10:00');
    expect(weekOf('2026-10-07 10:00', { start: '2026-09-07', end: '2026-12-18', weeks: 15 }, tz)).toBe(5);
  });
  it('still take a date-only value as that day', () => {
    expect(fmtWhen('2026-10-07', tz)).toBe('Wed 7 Oct');
  });
});
