import { describe, expect, it } from 'vitest';
import { md, releaseIdent } from '../src/model/format';
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
});

describe('releaseIdent', () => {
  const rel = (id: string, kind: string, when: string, number?: number | null) =>
    ({ id, kind, when, number, title: '', state: 'planned', source: null, dest: null, show_on_site: true, tbc: false }) as Release;
  it('uses the number the engine gives the row, with the kind label', () => {
    expect(releaseIdent(rel('lab-09', 'lab', '1', 9), [])).toBe('Lab 9');
    expect(releaseIdent(rel('extra', 'readings', '1', null), [])).toBe('Readings');
    expect(releaseIdent(rel('clinic', 'drop-in', '1', 2), [])).toBe('Drop-in 2');
  });
  it('falls back to the label, then the position, as the engine does', () => {
    expect(releaseIdent(rel('lecture_03', 'lecture', '1'), [])).toBe('Lecture 3');
    expect(releaseIdent(rel('01_lab', 'lab', '1'), [])).toBe('Lab 1');
    const all = [rel('intro', 'lecture', '2026-09-01'), rel('wrap', 'lecture', '2026-09-08')];
    expect(releaseIdent(all[1], all)).toBe('Lecture 2');
  });
});
